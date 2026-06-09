"""carsim_core - pure helpers for the CarSim MCP server.

No MCP runtime dependency here so these functions can be unit-checked directly
(see smoke_test.py). The MCP server (server.py) just wraps these.

The module owns the CarSim *domain*: path resolution, parsfile parse/edit,
simfile generation, co-sim scaffolding, headless solver / headless co-sim runs,
and result parsing. MATLAB *execution* for the primary path is delegated to the
official MATLAB MCP by the orchestrating agent (filesystem hand-off).
"""

from __future__ import annotations

import os
import re
import glob
import json
import shutil
import subprocess
from pathlib import Path

# Heavy/native libs imported at MODULE LOAD (main thread, at server boot).
# Importing numpy/scipy lazily inside a tool function deadlocks, because FastMCP
# runs sync tools in a worker thread and the *first* import of these C-extensions
# in a non-main thread can hang on the import lock (Windows). Keep them here.
import numpy as np
from scipy.io import loadmat as _loadmat

# --------------------------------------------------------------------------- #
# Path resolution
# --------------------------------------------------------------------------- #

DEFAULT_CARSIM_ROOT = r"E:\Carsim2024"
DEFAULT_MATLAB_EXE = r"E:\Program Files\MATLAB\R2023b\bin\matlab.exe"

# Directory containing this module (used to find templates/).
MODULE_DIR = Path(__file__).resolve().parent
TEMPLATES_DIR = MODULE_DIR / "templates"


def _p(path) -> dict:
    """Wrap a path with an existence flag for diagnostic output."""
    p = Path(path)
    return {"path": str(p), "exists": p.exists()}


def _autodetect_matlab() -> str:
    env = os.environ.get("MATLAB_EXE")
    if env and Path(env).exists():
        return env
    if Path(DEFAULT_MATLAB_EXE).exists():
        return DEFAULT_MATLAB_EXE
    # Search common install roots for any R20xx MATLAB.
    for root in (r"E:\Program Files\MATLAB", r"C:\Program Files\MATLAB"):
        cand = sorted(glob.glob(os.path.join(root, "R20*", "bin", "matlab.exe")), reverse=True)
        if cand:
            return cand[0]
    return DEFAULT_MATLAB_EXE  # return default even if missing, so caller sees exists:false


def _autodetect_db(carsim_root: str) -> str | None:
    """Best-effort discovery of the CarSim working database directory.

    Order: $CARSIM_DB -> Windows registry -> filesystem scan for a dir that
    looks like a CarSim database (contains a Runs folder or a simfile.sim).
    Returns None if nothing convincing is found (DB not yet initialized).
    """
    env = os.environ.get("CARSIM_DB")
    if env and Path(env).exists():
        return env

    # Registry: CarSim stores the current database under the vendor key.
    try:
        import winreg  # noqa: PLC0415  (Windows-only, imported lazily)
        for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
            try:
                base = winreg.OpenKey(hive, r"Software\Mechanical Simulation Corporation")
            except OSError:
                continue
            # Walk subkeys looking for a value that points at an existing dir.
            i = 0
            while True:
                try:
                    sub = winreg.EnumKey(base, i)
                except OSError:
                    break
                i += 1
                try:
                    k = winreg.OpenKey(base, sub)
                except OSError:
                    continue
                for vname in ("CurrentDatabase", "DataBase", "Database", "DataDir", "WorkingDir"):
                    try:
                        val, _ = winreg.QueryValueEx(k, vname)
                        if val and Path(val).exists():
                            return str(val)
                    except OSError:
                        pass
    except Exception:
        pass

    # The install ships its working database as <root>\DATA (this is what the
    # CarSim "Select Recent Database" dialog points at on this machine).
    root_data = os.path.join(carsim_root, "DATA")
    if os.path.isdir(os.path.join(root_data, "Runs")):
        return root_data

    # Filesystem scan of other likely locations.
    candidates = []
    pub = os.environ.get("PUBLIC", r"C:\Users\Public")
    for base in (os.path.join(pub, "Documents"), carsim_root,
                 os.path.expanduser(r"~\Documents")):
        if not os.path.isdir(base):
            continue
        for entry in glob.glob(os.path.join(base, "*")):
            if not os.path.isdir(entry):
                continue
            name = os.path.basename(entry).lower()
            if "carsim" in name or "cs_" in name or name == "data":
                candidates.append(entry)
    for c in candidates:
        if os.path.isdir(os.path.join(c, "Runs")):
            return c
    return None


def resolve_paths() -> dict:
    """Resolve and existence-check every path the MCP relies on."""
    root = os.environ.get("CARSIM_ROOT", DEFAULT_CARSIM_ROOT)
    matlab = _autodetect_matlab()
    db = _autodetect_db(root)
    info = {
        "carsim_root": _p(root),
        "gui_exe": _p(os.path.join(root, "CarSim_64.exe")),
        "cli_solver": _p(os.path.join(root, "Programs", "VS_SolverWrapper_CLI_64.exe")),
        "simulink_solver_dir": _p(os.path.join(root, "Programs", "solvers", "Matlab")),
        "vs_sf_mex": _p(os.path.join(root, "Programs", "solvers", "Matlab", "vs_sf.mexw64")),
        "simulink_examples_dir": _p(os.path.join(root, "DATA", "Extensions", "Simulink")),
        "cpar_dir": _p(os.path.join(root, "Resources", "CPAR_Archives")),
        "matlab_exe": _p(matlab),
        "templates_dir": _p(str(TEMPLATES_DIR)),
        "carsim_db": _p(db) if db else {"path": None, "exists": False},
    }
    info["ok"] = all(
        info[k]["exists"] for k in
        ("gui_exe", "cli_solver", "simulink_solver_dir", "vs_sf_mex", "matlab_exe")
    )
    return info


# --------------------------------------------------------------------------- #
# Parsfile parse / edit
# --------------------------------------------------------------------------- #
# CarSim parsfiles are line-based:
#   KEYWORD value            e.g.  M_S 1653
#   KEYWORD path/to/link     (dataset links)
#   ! comment line
# Keywords may legitimately repeat (e.g. table rows), so we keep all occurrences.

_COMMENT_PREFIXES = ("!",)


def read_parsfile(path: str) -> dict:
    """Parse a CarSim parsfile into {path, keywords, raw_lines}.

    keywords maps KEYWORD -> list of value-strings (one per occurrence, in order).
    """
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"parsfile not found: {path}")
    raw_lines = p.read_text(encoding="latin-1").splitlines()
    keywords: dict[str, list[str]] = {}
    for line in raw_lines:
        s = line.strip()
        if not s or s.startswith(_COMMENT_PREFIXES):
            continue
        parts = s.split(None, 1)
        kw = parts[0]
        val = parts[1].strip() if len(parts) > 1 else ""
        keywords.setdefault(kw, []).append(val)
    return {"path": str(p), "keywords": keywords, "raw_lines": raw_lines}


def write_parsfile(path: str, edits: dict, mode: str = "set", backup: bool = True) -> dict:
    """Edit keyword values in a parsfile.

    edits: {KEYWORD: new_value}. For mode='set', replace the value of the FIRST
    occurrence of each keyword (preserving the keyword token and indentation);
    append `KEYWORD value` if the keyword is absent. Writes a .bak backup.
    Returns {changed, added, backup}.
    """
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"parsfile not found: {path}")
    lines = p.read_text(encoding="latin-1").splitlines()
    remaining = {str(k): str(v) for k, v in edits.items()}
    changed, added = [], []

    for idx, line in enumerate(lines):
        s = line.strip()
        if not s or s.startswith(_COMMENT_PREFIXES):
            continue
        parts = s.split(None, 1)
        kw = parts[0]
        if kw in remaining:
            new_val = remaining.pop(kw)
            # Preserve leading whitespace of the original line.
            lead = line[: len(line) - len(line.lstrip())]
            lines[idx] = f"{lead}{kw} {new_val}"
            changed.append(kw)
        if not remaining:
            break

    for kw, val in remaining.items():  # keywords not present -> append
        lines.append(f"{kw} {val}")
        added.append(kw)

    backup_path = None
    if backup:
        backup_path = str(p) + ".bak"
        shutil.copy2(p, backup_path)
    p.write_text("\n".join(lines) + "\n", encoding="latin-1")
    return {"changed": changed, "added": added, "backup": backup_path}


# --------------------------------------------------------------------------- #
# Example discovery
# --------------------------------------------------------------------------- #

def list_examples() -> dict:
    """List shipped CPAR archives and Simulink example models."""
    paths = resolve_paths()
    cpar_dir = paths["cpar_dir"]["path"]
    sim_dir = paths["simulink_examples_dir"]["path"]

    cpars = sorted(os.path.basename(f) for f in glob.glob(os.path.join(cpar_dir, "*.cpar")))
    models = []
    for ext in ("slx", "mdl"):
        for f in glob.glob(os.path.join(sim_dir, "**", f"*.{ext}"), recursive=True):
            models.append({"name": os.path.basename(f), "path": f, "ext": ext})
    models.sort(key=lambda m: m["name"].lower())
    return {"cpar_archives": cpars, "simulink_models": models,
            "n_cpar": len(cpars), "n_models": len(models)}


# --------------------------------------------------------------------------- #
# Simfile generation
# --------------------------------------------------------------------------- #
# The simfile (simfile.sim) is the top-level text file the solver / vs_sf reads.
# CarSim's GUI normally writes it via "Generate Files for this Run". We support
# templating a new simfile from an existing GUI-generated one, swapping the
# referenced parsfile path. This avoids guessing the exact format.

_SIMFILE_INPUT_KEYS = ("INPUT", "PARSFILE")


def generate_simfile(out_path: str, run_parsfile: str | None = None,
                     template_simfile: str | None = None) -> dict:
    """Create/refresh a simfile at out_path.

    If template_simfile is given, copy it and (optionally) rewrite the parsfile
    reference to run_parsfile. The canonical source of a template is a
    GUI-generated simfile.sim. Returns {simfile, parsfile, templated_from}.
    """
    out = Path(out_path)
    if template_simfile is None:
        raise ValueError(
            "generate_simfile needs template_simfile (a GUI-generated simfile.sim). "
            "Generate one once via the CarSim GUI: open a Simulink run and press "
            "'Generate Files for this Run'.")
    tpl = Path(template_simfile)
    if not tpl.exists():
        raise FileNotFoundError(f"template simfile not found: {template_simfile}")

    lines = tpl.read_text(encoding="latin-1").splitlines()
    if run_parsfile:
        for idx, line in enumerate(lines):
            parts = line.strip().split(None, 1)
            if parts and parts[0] in _SIMFILE_INPUT_KEYS:
                lead = line[: len(line) - len(line.lstrip())]
                lines[idx] = f"{lead}{parts[0]} {run_parsfile}"
                break
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines) + "\n", encoding="latin-1")
    return {"simfile": str(out), "parsfile": run_parsfile, "templated_from": str(tpl)}


# --------------------------------------------------------------------------- #
# Headless CarSim solver (no MATLAB)
# --------------------------------------------------------------------------- #

def _parse_simfile_outputs(simfile: str) -> dict:
    """Pull declared output/IO references out of a simfile, best-effort.

    Keys match CarSim's real simfile schema (see DATA\\simfile.sim):
    FILEBASE, ERDFILE (.vs results), LOGFILE, ECHO, FINAL, INPUTARCHIVE, INPUT,
    DLLFILE, and the co-sim port counts PORTS_IMP / PORTS_EXP / EXT_MODEL_STEP.
    """
    out = {}
    keys = ("FILEBASE", "ERDFILE", "LOGFILE", "ECHO", "FINAL", "INPUTARCHIVE",
            "INPUT", "DLLFILE", "PORTS_IMP", "PORTS_EXP", "EXT_MODEL_STEP")
    try:
        for line in Path(simfile).read_text(encoding="latin-1").splitlines():
            parts = line.strip().split(None, 1)
            if len(parts) == 2 and parts[0] in keys:
                out[parts[0]] = parts[1]
    except OSError:
        pass
    return out


def run_solver(simfile: str, timeout: int = 120) -> dict:
    """Run the headless CarSim solver on a simfile via VS_SolverWrapper_CLI_64."""
    paths = resolve_paths()
    cli = paths["cli_solver"]["path"]
    if not Path(cli).exists():
        raise FileNotFoundError(f"CLI solver not found: {cli}")
    if not Path(simfile).exists():
        raise FileNotFoundError(f"simfile not found: {simfile}")
    proc = subprocess.run(
        [cli, simfile], capture_output=True, text=True, timeout=timeout,
        cwd=str(Path(simfile).parent))
    declared = _parse_simfile_outputs(simfile)
    return {
        "returncode": proc.returncode,
        "stdout_tail": (proc.stdout or "")[-2000:],
        "stderr_tail": (proc.stderr or "")[-2000:],
        "output_dir": str(Path(simfile).parent),
        "declared_outputs": declared,
    }


# --------------------------------------------------------------------------- #
# Co-sim scaffolding
# --------------------------------------------------------------------------- #

def _fill_template(template_name: str, mapping: dict) -> str:
    text = (TEMPLATES_DIR / template_name).read_text(encoding="utf-8")
    for key, val in mapping.items():
        text = text.replace(key, str(val))
    return text


def scaffold_cosim(out_dir: str, simfile: str, kind: str = "slx",
                   model: str | None = None, controller_call: str | None = None,
                   stop_time: str = "5") -> dict:
    """Generate a ready-to-run co-sim package in out_dir.

    kind='slx': fills run_cosim_slx template; `model` is a Simulink model that
        contains the CarSim S-Function (vs_sf). Defaults to a copy of the shipped
        DATA\\Extensions\\Simulink\\example.mdl.
    kind='mfile': fills run_cosim_mfile template; `controller_call` is the MATLAB
        statement that runs your .m controller (must assign a variable `results`).

    Returns {runner_m, model, simfile, results_path, solver_dir}.
    """
    paths = resolve_paths()
    solver_dir = paths["simulink_solver_dir"]["path"]
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    results_path = str(out / "results.mat")
    runner_m = str(out / "run_cosim.m")

    if kind == "slx":
        if model is None:
            src = os.path.join(paths["simulink_examples_dir"]["path"], "example.mdl")
            model = str(out / "example.mdl")
            shutil.copy2(src, model)
        text = _fill_template("run_cosim_slx.m.tmpl", {
            "@SOLVER_DIR@": solver_dir,
            "@SIMFILE@": simfile,
            "@MODEL@": model,
            "@STOP@": stop_time,
            "@RESULTS@": results_path,
        })
    elif kind == "mfile":
        if not controller_call:
            controller_call = "results = struct('note','replace with your controller call');"
        text = _fill_template("run_cosim_mfile.m.tmpl", {
            "@SOLVER_DIR@": solver_dir,
            "@SIMFILE@": simfile,
            "@CONTROLLER_CALL@": controller_call,
            "@STOP@": stop_time,
            "@RESULTS@": results_path,
        })
        model = None
    else:
        raise ValueError("kind must be 'slx' or 'mfile'")

    Path(runner_m).write_text(text, encoding="utf-8")
    return {"runner_m": runner_m, "model": model, "simfile": simfile,
            "results_path": results_path, "solver_dir": solver_dir}


# --------------------------------------------------------------------------- #
# Headless co-sim (optional fallback; primary path is the MATLAB MCP)
# --------------------------------------------------------------------------- #

def run_cosim_headless(runner_m: str, timeout: int = 600) -> dict:
    """Run a generated co-sim driver via `matlab -batch` (unattended fallback)."""
    paths = resolve_paths()
    matlab = paths["matlab_exe"]["path"]
    if not Path(matlab).exists():
        raise FileNotFoundError(f"MATLAB not found: {matlab}")
    if not Path(runner_m).exists():
        raise FileNotFoundError(f"runner not found: {runner_m}")
    runner = Path(runner_m)
    cmd = [matlab, "-batch", f"run('{runner.as_posix()}')"]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout,
                          cwd=str(runner.parent))
    stdout = proc.stdout or ""
    results_path = str(runner.parent / "results.mat")
    return {
        "returncode": proc.returncode,
        "cosim_ok": "COSIM_OK" in stdout,
        "stdout_tail": stdout[-3000:],
        "stderr_tail": (proc.stderr or "")[-3000:],
        "results_path": results_path if Path(results_path).exists() else None,
    }


# --------------------------------------------------------------------------- #
# Result parsing
# --------------------------------------------------------------------------- #

def _summarize_array(name: str, arr) -> dict | None:
    a = np.asarray(arr).squeeze()
    if a.dtype.kind not in "fiu" or a.size == 0:
        return None
    a = a.astype(float).ravel()
    return {"len": int(a.size), "min": float(np.nanmin(a)),
            "max": float(np.nanmax(a)), "final": float(a[-1])}


def read_results(path: str, max_channels: int = 50) -> dict:
    """Parse a results file (.mat preferred) into per-channel summaries."""
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"results not found: {path}")

    if p.suffix.lower() == ".mat":
        mat = _loadmat(path, squeeze_me=True, struct_as_record=False)
        channels = {}
        for key, val in mat.items():
            if key.startswith("__"):
                continue
            # Simulink.SimulationOutput / structs: walk one level of attributes.
            if hasattr(val, "_fieldnames"):
                for fn in val._fieldnames:
                    sub = getattr(val, fn)
                    s = _summarize_array(f"{key}.{fn}", sub)
                    if s:
                        channels[f"{key}.{fn}"] = s
            else:
                s = _summarize_array(key, val)
                if s:
                    channels[key] = s
            if len(channels) >= max_channels:
                break
        return {"source": "mat", "n": len(channels), "channels": channels}

    if p.suffix.lower() in (".erd", ".vs"):
        # CarSim 2024 ERD (Version 2): JSON header (.vs) + float32 data (.vsb).
        grp = json.loads(p.read_text(encoding="latin-1"))["VsChannelGroup"]
        chans = grp.get("Channels", [])
        names = [c.get("Name Aliases", ["?"])[0] for c in chans]
        xstep = float(grp.get("XStep", 0) or 0)
        xstart = float(grp.get("XStart", 0) or 0)
        xlabel = grp.get("XLabel", "Time")

        data_file = p.with_suffix(".vsb")
        if not data_file.exists():
            return {"source": "erd", "n": len(names),
                    "channels": {n: {} for n in names[:max_channels]},
                    "note": "header parsed but .vsb binary not found; names only.",
                    "data_file": None}

        raw = np.fromfile(str(data_file), dtype="<f4")
        nchan = len(names)
        nsamp = raw.size // nchan if nchan else 0
        mat = raw[: nsamp * nchan].reshape(nsamp, nchan)  # sample-major frames

        channels = {}
        tvec = xstart + xstep * np.arange(nsamp)
        s = _summarize_array(xlabel, tvec)
        if s:
            channels[xlabel] = s
        for i, nm in enumerate(names):
            if len(channels) >= max_channels:
                break
            s = _summarize_array(nm, mat[:, i])
            if s:
                channels[nm] = s
        return {"source": "erd", "n": nchan, "n_samples": int(nsamp),
                "x_label": xlabel, "x_step": xstep,
                "channels": channels, "data_file": str(data_file)}

    raise ValueError(f"unsupported results file type: {p.suffix}")


# --------------------------------------------------------------------------- #
# GUI launch
# --------------------------------------------------------------------------- #

def launch_gui(dataset: str | None = None) -> dict:
    """Launch CarSim_64.exe (non-blocking). Returns the new process pid."""
    paths = resolve_paths()
    gui = paths["gui_exe"]["path"]
    if not Path(gui).exists():
        raise FileNotFoundError(f"CarSim GUI not found: {gui}")
    args = [gui]
    if dataset:
        args.append(dataset)
    proc = subprocess.Popen(args, cwd=str(Path(gui).parent))
    return {"pid": proc.pid, "launched": gui, "dataset": dataset}
