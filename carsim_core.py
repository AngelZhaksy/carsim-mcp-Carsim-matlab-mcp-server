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
import stat
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


def _ensure_writable(path: Path) -> None:
    """Clear the read-only attribute so the file can be overwritten.

    Shipped CarSim library datasets are read-only on disk; the GUI clears this on
    save, and so do we. A .bak backup is always written first by the caller, so
    the original content remains recoverable.
    """
    try:
        if path.exists() and not (os.stat(path).st_mode & stat.S_IWRITE):
            os.chmod(path, os.stat(path).st_mode | stat.S_IWRITE)
    except OSError:
        pass


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
        _ensure_writable(Path(backup_path))  # backups of RO files are RO too
    _ensure_writable(p)
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
        # CarSim's .vsb starts with a small fixed header, so the total float
        # count is NOT an exact multiple of nchan (v2024: 6 leading floats /
        # 24 bytes). Skip it; otherwise every column is shifted and the channel
        # names get mislabeled onto the wrong data (e.g. Vx reading 359 km/h).
        # The header length is the remainder; data after it is row-major
        # (sample-major) frames. Verified against a Double Lane Change run:
        # Vx -> 72 km/h, Station -> 0..199 m, Bk_Stat -> 0.
        header = raw.size - nsamp * nchan
        mat = raw[header: header + nsamp * nchan].reshape(nsamp, nchan)

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
    """Launch CarSim_64.exe (non-blocking). Returns the new process pid.

    Note: CarSim_64.exe has no documented CLI to open a *specific* screen; it
    opens to the last-used Run Control. For programmatic parameter work, edit the
    underlying parsfiles directly (get_dataset / set_dataset) -- no GUI needed.
    """
    paths = resolve_paths()
    gui = paths["gui_exe"]["path"]
    if not Path(gui).exists():
        raise FileNotFoundError(f"CarSim GUI not found: {gui}")
    args = [gui]
    if dataset:
        args.append(dataset)
    proc = subprocess.Popen(args, cwd=str(Path(gui).parent))
    return {"pid": proc.pid, "launched": gui, "dataset": dataset}


# --------------------------------------------------------------------------- #
# CarSim database navigation (GUI libraries -> parsfiles)
# --------------------------------------------------------------------------- #
# Every dataset visible in the CarSim GUI is a plain-text .par file under
# <DB>\<Library>\[<Category>\]<file>.par. The GUI is closed-source, but the DATA
# is not: each .par carries its own GUI breadcrumb on line ~2 as
#   #FullDataName <Library>`<DataSet>`<Category>
# and a metadata block (#Library/#DataSet/#Category/#FileID/#Modified) near the
# end. We use those to give the agent a searchable catalog of the whole database.

# DATA subfolders that are bookkeeping, not GUI libraries.
_NON_LIBRARY_DIRS = {
    "Results", "Runs", "Log", "slprj", "Animator", "Plot", "Output",
    "Preferences", "Configuration", "Extensions",
}


def _db_dir() -> Path:
    """Resolve the CarSim working database directory or raise."""
    db = resolve_paths()["carsim_db"]["path"]
    if not db or not Path(db).is_dir():
        raise FileNotFoundError(
            "CarSim database not found. Set CARSIM_DB or CARSIM_ROOT so that "
            "<root>\\DATA exists.")
    return Path(db)


def _read_head(path: Path, n: int = 8) -> list[str]:
    """Read the first n lines of a file cheaply (latin-1)."""
    out = []
    with path.open("r", encoding="latin-1", errors="replace") as fh:
        for _ in range(n):
            line = fh.readline()
            if not line:
                break
            out.append(line.rstrip("\n"))
    return out


def parse_identity(path: str | Path) -> dict:
    """Extract the GUI identity (library/dataset/category) of a .par file.

    Prefers the fast top-of-file `#FullDataName A`B`C` line; falls back to the
    `#Library/#DataSet/#Category` metadata block if needed.
    """
    p = Path(path)
    library = dataset = category = None
    file_id = modified = None

    head = _read_head(p, 8)
    for line in head:
        if line.startswith("#FullDataName"):
            rest = line[len("#FullDataName"):].strip()
            parts = rest.split("`")
            if len(parts) >= 1:
                library = parts[0].strip() or None
            if len(parts) >= 2:
                dataset = parts[1].strip() or None
            if len(parts) >= 3:
                category = parts[2].strip() or None
            break

    if library is None or file_id is None or modified is None:
        # Scan the (small) metadata block, usually near the file end.
        try:
            for line in p.read_text(encoding="latin-1", errors="replace").splitlines():
                if line.startswith("#Library") and ":" in line:
                    library = library or line.split(":", 1)[1].strip()
                elif line.startswith("#DataSet") and ":" in line:
                    dataset = dataset or line.split(":", 1)[1].strip()
                elif line.startswith("#Category") and ":" in line:
                    category = category or line.split(":", 1)[1].strip()
                elif line.startswith("#FileID") and ":" in line:
                    file_id = line.split(":", 1)[1].strip()
                elif line.startswith("#Modified") and ":" in line:
                    modified = line.split(":", 1)[1].strip()
        except OSError:
            pass

    return {"path": str(p), "library": library, "dataset": dataset,
            "category": category, "file_id": file_id, "modified": modified}


def list_libraries() -> dict:
    """List CarSim GUI libraries (DATA subfolders that hold datasets).

    Returns {libraries:[{name, n_datasets, categories:[...]}], db, n}.
    """
    db = _db_dir()
    libs = []
    for entry in sorted(db.iterdir()):
        if not entry.is_dir() or entry.name in _NON_LIBRARY_DIRS:
            continue
        pars = list(entry.rglob("*.par"))
        if not pars:
            continue
        cats = sorted({c.name for c in entry.iterdir() if c.is_dir()})
        libs.append({"name": entry.name, "n_datasets": len(pars),
                     "categories": cats})
    return {"db": str(db), "n": len(libs), "libraries": libs}


def browse_library(library: str, category: str | None = None,
                   limit: int = 300) -> dict:
    """List datasets in a library (optionally filtered to a category subfolder).

    `library` is a DATA subfolder name (e.g. 'Suspensions', 'Powertrain',
    'Vehicles'). Returns each dataset's GUI identity + file path.
    """
    db = _db_dir()
    base = db / library
    if not base.is_dir():
        avail = [e.name for e in db.iterdir()
                 if e.is_dir() and e.name not in _NON_LIBRARY_DIRS]
        raise FileNotFoundError(
            f"library '{library}' not found under {db}. "
            f"Available: {', '.join(sorted(avail))}")
    search = base / category if category else base
    if not search.is_dir():
        cats = sorted(c.name for c in base.iterdir() if c.is_dir())
        raise FileNotFoundError(
            f"category '{category}' not in '{library}'. Available: {', '.join(cats)}")

    datasets = []
    for f in sorted(search.rglob("*.par")):
        ident = parse_identity(f)
        datasets.append({"file": str(f), "dataset": ident["dataset"],
                         "category": ident["category"],
                         "library": ident["library"]})
        if len(datasets) >= limit:
            break
    return {"library": library, "category": category,
            "n": len(datasets), "datasets": datasets}


def find_dataset(query: str, limit: int = 50) -> dict:
    """Fuzzy-search every dataset in the database by GUI identity text.

    Matches case-insensitively against library/dataset/category names. Useful
    for 'where do I set X' questions, e.g. find_dataset('motor') or
    find_dataset('sprung mass').
    """
    db = _db_dir()
    terms = [t for t in query.lower().split() if t]
    hits = []
    for f in db.rglob("*.par"):
        # Skip run-result echoes etc.
        if any(part in _NON_LIBRARY_DIRS for part in f.parts):
            continue
        ident = parse_identity(f)
        hay = " ".join(filter(None, (ident["library"], ident["dataset"],
                                     ident["category"], f.stem))).lower()
        if all(t in hay for t in terms):
            hits.append({"file": str(f), "library": ident["library"],
                         "dataset": ident["dataset"],
                         "category": ident["category"]})
            if len(hits) >= limit:
                break
    return {"query": query, "n": len(hits), "results": hits}


# --------------------------------------------------------------------------- #
# Structured dataset read / write (identity + scalars + tables, annotated)
# --------------------------------------------------------------------------- #

def _is_data_row(s: str) -> bool:
    """True if a line looks like CarSim table data (numeric first char)."""
    if not s:
        return False
    return s[0].isdigit() or s[0] in "+-."


_STRUCTURAL = {"PARSFILE", "END"}


def _parse_dataset_body(lines: list[str]) -> tuple[dict, dict]:
    """Split a parsfile body into scalar keywords and table blocks.

    Returns (scalars, tables) where scalars maps KEYWORD -> [values] and tables
    maps KEYWORD -> {method, rows:[...]}. A keyword is treated as a table header
    when its next meaningful line is a data row or ENDTABLE.
    """
    scalars: dict[str, list[str]] = {}
    tables: dict[str, dict] = {}
    n = len(lines)
    i = 0
    while i < n:
        s = lines[i].strip()
        if (not s or s.startswith("!") or s.startswith("#")
                or s in _STRUCTURAL):
            i += 1
            continue
        parts = s.split(None, 1)
        kw = parts[0]
        val = parts[1].strip() if len(parts) > 1 else ""

        # Peek at the next meaningful line to detect a table header.
        j = i + 1
        while j < n and (not lines[j].strip() or lines[j].strip().startswith("!")):
            j += 1
        nxt = lines[j].strip() if j < n else ""
        if _is_data_row(nxt) or nxt.startswith("ENDTABLE"):
            rows = []
            k = j
            while k < n:
                t = lines[k].strip()
                if t.startswith("ENDTABLE"):
                    break
                if t and not t.startswith("!"):
                    rows.append(t)
                k += 1
            tables.setdefault(kw, {"method": val, "rows": rows})
            i = k + 1
            continue
        scalars.setdefault(kw, []).append(val)
        i += 1
    return scalars, tables


def get_dataset(path: str, annotate: bool = True, max_tables: int = 40) -> dict:
    """Read a dataset parsfile into identity + scalar params + tables.

    With annotate=True, each scalar keyword is tagged with {desc, unit} from the
    locally-built keyword dictionary (see build_keyword_dictionary) when known.
    """
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"dataset not found: {path}")
    lines = p.read_text(encoding="latin-1", errors="replace").splitlines()
    scalars, tables = _parse_dataset_body(lines)
    ident = parse_identity(p)

    kdict = _load_keyword_dict() if annotate else {}
    params = {}
    for kw, vals in scalars.items():
        entry = {"value": vals[0] if len(vals) == 1 else vals}
        if annotate:
            meta = kdict.get(_kw_base(kw))
            if meta:
                entry["desc"] = meta.get("desc")
                if meta.get("unit"):
                    entry["unit"] = meta["unit"]
        params[kw] = entry

    table_out = {}
    for i, (kw, tb) in enumerate(tables.items()):
        if i >= max_tables:
            break
        item = {"method": tb["method"], "n_rows": len(tb["rows"]),
                "rows": tb["rows"][:200]}
        if annotate:
            meta = kdict.get(_kw_base(kw))
            if meta:
                item["desc"] = meta.get("desc")
                if meta.get("unit"):
                    item["unit"] = meta["unit"]
        table_out[kw] = item

    return {"path": str(p), "identity": ident,
            "n_params": len(params), "params": params,
            "n_tables": len(tables), "tables": table_out}


def set_dataset(path: str, edits: dict, backup: bool = True) -> dict:
    """Edit scalar keyword values in a dataset and refresh its #Modified stamp.

    Thin wrapper over write_parsfile that also bumps the #Modified metadata line
    so the change is visible in the GUI's dataset list. Returns write_parsfile's
    result plus {modified_stamp}.
    """
    res = write_parsfile(path, edits, mode="set", backup=backup)

    # Bump #Modified to now (CarSim format: MM-DD-YYYY HH:MM:SS).
    import time
    stamp = time.strftime("%m-%d-%Y %H:%M:%S")
    p = Path(path)
    lines = p.read_text(encoding="latin-1", errors="replace").splitlines()
    bumped = False
    for idx, line in enumerate(lines):
        if line.startswith("#Modified") and ":" in line:
            head = line.split(":", 1)[0]
            lines[idx] = f"{head}: {stamp}"
            bumped = True
            break
    if bumped:
        p.write_text("\n".join(lines) + "\n", encoding="latin-1")
    res["modified_stamp"] = stamp if bumped else None
    return res


# --------------------------------------------------------------------------- #
# Keyword dictionary (built locally from CarSim's own echo files)
# --------------------------------------------------------------------------- #
# CarSim's solver writes Results\<run>\LastRun_echo.par files where every active
# parameter is echoed with its units and description:
#     M_SU   1370 ; kg ! Mass of unladen sprung mass (SU) [I]
# We harvest the union across all echo files into a keyword dictionary. This is
# authoritative (CarSim's own text) and version-correct for *this* install.
#
# Copyright note: the descriptions are CarSim's proprietary documentation, so the
# generated keywords.json is a LOCAL artifact (.gitignored) -- never redistributed.

KEYWORD_DICT_PATH = MODULE_DIR / "keywords.json"

# Echo line: KEYWORD  value [; unit] [! description], with `! ...` continuations.
_ECHO_DEF_RE = re.compile(r"^(?P<kw>[A-Za-z_][A-Za-z0-9_().\-]*)\s+(?P<rest>.*)$")
_DESC_FLAGS_RE = re.compile(r"\s*\[[DIL]\]\s*$")  # trailing [D]/[I]/[L] markers

_kw_dict_cache: dict | None = None


def _kw_base(keyword: str) -> str:
    """Normalize a keyword for dictionary lookup: drop array indices, upper-case.

    e.g. 'OPT_ENGINE_PITCH_REACTION(1)' -> 'OPT_ENGINE_PITCH_REACTION'.
    """
    return re.sub(r"\(.*\)$", "", keyword).strip().upper()


def parse_echo_dictionary(echo_path: str | Path) -> dict:
    """Parse one *_echo.par into {KEYWORD: {desc, unit}} (best-effort)."""
    out: dict[str, dict] = {}
    last_kw = None
    try:
        lines = Path(echo_path).read_text(encoding="latin-1",
                                          errors="replace").splitlines()
    except OSError:
        return out
    for line in lines:
        if not line.strip():
            last_kw = None
            continue
        # Continuation: indented '! more description'.
        if line[0] in " \t" and line.lstrip().startswith("!"):
            if last_kw and out.get(last_kw):
                extra = line.lstrip()[1:].strip()
                extra = _DESC_FLAGS_RE.sub("", extra)
                if extra:
                    out[last_kw]["desc"] = (out[last_kw]["desc"] + " " + extra).strip()
            continue
        if line[0] in " \t!#":
            last_kw = None
            continue
        m = _ECHO_DEF_RE.match(line)
        if not m:
            last_kw = None
            continue
        kw = _kw_base(m.group("kw"))
        rest = m.group("rest")
        if kw in _STRUCTURAL or kw in ("DATASET_TITLE", "CATEGORY", "TITLE",
                                       "MODEL_LAYOUT"):
            last_kw = None
            continue
        unit = None
        desc = None
        # Split description off first ' ! '.
        if "!" in rest:
            left, desc = rest.split("!", 1)
            desc = _DESC_FLAGS_RE.sub("", desc.strip())
        else:
            left = rest
        # Unit is the token after ';' in the left part.
        if ";" in left:
            _, unit_part = left.split(";", 1)
            unit = unit_part.strip() or None
            if unit in ("-", "--"):
                unit = None
        if desc or unit:
            prev = out.get(kw)
            # Keep the richest entry seen across echoes.
            if prev is None or (not prev.get("desc") and desc) or \
               (not prev.get("unit") and unit):
                out[kw] = {"desc": desc or (prev or {}).get("desc"),
                           "unit": unit or (prev or {}).get("unit")}
            last_kw = kw
        else:
            last_kw = None
    return out


def build_keyword_dictionary(refresh: bool = True, max_files: int = 0) -> dict:
    """Build keywords.json from all Results\\**\\*_echo.par on THIS install.

    refresh=False just reports the existing dictionary. max_files>0 limits how
    many echo files are scanned (for a quick partial build). Returns stats.
    """
    global _kw_dict_cache
    if not refresh and KEYWORD_DICT_PATH.exists():
        d = json.loads(KEYWORD_DICT_PATH.read_text(encoding="utf-8"))
        _kw_dict_cache = d
        return {"path": str(KEYWORD_DICT_PATH), "n_keywords": len(d),
                "refreshed": False}

    db = _db_dir()
    results = db / "Results"
    echoes = sorted(results.rglob("*_echo.par")) if results.is_dir() else []
    if max_files:
        echoes = echoes[:max_files]

    merged: dict[str, dict] = {}
    for e in echoes:
        for kw, meta in parse_echo_dictionary(e).items():
            cur = merged.get(kw)
            if cur is None:
                merged[kw] = meta
            else:
                if not cur.get("desc") and meta.get("desc"):
                    cur["desc"] = meta["desc"]
                if not cur.get("unit") and meta.get("unit"):
                    cur["unit"] = meta["unit"]

    KEYWORD_DICT_PATH.write_text(
        json.dumps(merged, ensure_ascii=False, indent=0, sort_keys=True),
        encoding="utf-8")
    _kw_dict_cache = merged
    return {"path": str(KEYWORD_DICT_PATH), "n_keywords": len(merged),
            "n_echo_files": len(echoes), "refreshed": True}


def _load_keyword_dict() -> dict:
    """Load (and cache) the keyword dictionary; empty dict if not built yet."""
    global _kw_dict_cache
    if _kw_dict_cache is not None:
        return _kw_dict_cache
    if KEYWORD_DICT_PATH.exists():
        try:
            _kw_dict_cache = json.loads(KEYWORD_DICT_PATH.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            _kw_dict_cache = {}
    else:
        _kw_dict_cache = {}
    return _kw_dict_cache


def describe_keyword(keyword: str) -> dict:
    """Look up a parsfile keyword's meaning + unit in the local dictionary.

    Substring fallback: if the exact keyword is unknown, return up to 15 keywords
    that contain the query, so the agent can discover the right name.
    """
    d = _load_keyword_dict()
    if not d:
        return {"keyword": keyword, "known": False,
                "note": "keyword dictionary not built yet; run "
                        "build_keyword_dictionary() once (needs CarSim run "
                        "results in DATA\\Results)."}
    base = _kw_base(keyword)
    if base in d:
        return {"keyword": base, "known": True, **d[base]}
    sub = [k for k in d if base in k][:15]
    return {"keyword": base, "known": False, "similar": sub,
            "n_in_dict": len(d)}
