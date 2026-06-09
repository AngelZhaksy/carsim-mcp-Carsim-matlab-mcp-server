# CarSim MCP — a Claude Code plugin

Drive **CarSim (test in 2024)** from Claude Code (or any MCP client): edit parameters, run the
headless solver, and run **CarSim ↔ MATLAB/Simulink co-simulation** (both `.m`
controllers and `.slx` models). It ships as a Claude Code **plugin** with a bundled
**MCP server**.

CarSim-domain work lives in this server; **MATLAB execution is delegated to the
official MATLAB MCP** (a warm, visible MATLAB session). The two hand off over the
filesystem, orchestrated by the agent.

> ⚠️ **You need your own valid CarSim (test in 2024) and MATLAB licenses.** This project does
> not include or circumvent any license. On a license-managed CarSim, start the
> CarSim License Manager (`<CarSim>\Programs\cslm.exe`) **before** running the
> solver/co-sim, or CarSim returns a FlexNet error.

---

## Features / tools (10)

| Tool | What it does |
|---|---|
| `carsim_info` | Resolve & existence-check every path (root, GUI, CLI solver, `vs_sf` mex, MATLAB, DB). The "is it wired up" probe. |
| `launch_gui` | Open the CarSim GUI (optional; **not** needed for solving/co-sim). |
| `list_examples` | List shipped CPAR archives + Simulink example models. |
| `read_parsfile` / `write_parsfile` | Read/edit parameters: vehicle params, I/O channels, dataset links (auto `.bak`). |
| `run_solver` | Headless CarSim solve via the CLI solver wrapper (no MATLAB, no GUI). |
| `generate_simfile` | Produce a co-sim `simfile` by templating from a GUI-generated one. |
| `scaffold_cosim` | Generate a ready-to-run co-sim package (`.slx` or `.m` driver + `run_cosim.m`). |
| `read_results` | Parse CarSim ERD (`.vs` JSON + `.vsb` float32) or a `.mat` into per-channel `{len,min,max,final}`. |
| `run_cosim_headless` | Optional fallback: run a driver via `matlab -batch` (unattended). |

The recommended co-sim path runs the driver through the **MATLAB MCP**, not this fallback.

---

## Requirements

- **CarSim (test in 2024)** (Windows) with a valid license. Start `cslm.exe` first if license-managed.
- **MATLAB** (tested R2023b) + Simulink, with a valid license.
- **Python 3.10+** with `mcp[cli]` and `scipy` (see [`requirements.txt`](requirements.txt)).
- The official **MATLAB MCP** server connected in your MCP client (for the co-sim run step).

The CarSim ↔ Simulink bridge (`vs_sf.mexw64`) and CLI solver ship **with CarSim** —
nothing extra to build.

---

## Install

### A. As a Claude Code plugin (recommended)

From a local clone / unzipped folder:

```shell
/plugin marketplace add /absolute/path/to/carsim-mcp
/plugin install carsim-mcp@carsim-mcp
```

From GitHub (after you push this repo):

```shell
/plugin marketplace add AngelZhaksy/carsim-mcp-Carsim-matlab-mcp-server
/plugin install carsim-mcp@carsim-mcp
```

Then install the Python deps into the `python` the plugin will launch:

```shell
python -m pip install -r requirements.txt
```

Restart Claude Code (or `/reload-plugins`) so the `mcp__carsim__*` tools load.

### B. As a plain MCP server (no plugin)

Add to your MCP client config (Claude Code: `~/.claude.json` → `mcpServers`):

```json
{
  "mcpServers": {
    "carsim": {
      "command": "python",
      "args": ["/absolute/path/to/carsim-mcp/server.py"],
      "env": {
        "CARSIM_ROOT": "E:\\Carsim2024",
        "MATLAB_EXE": "E:\\Program Files\\MATLAB\\R2023b\\bin\\matlab.exe"
      }
    }
  }
}
```

---

## Configuration (environment variables)

All optional — sensible defaults are auto-detected; override for your install.

| Var | Default | Meaning |
|---|---|---|
| `CARSIM_ROOT` | `E:\Carsim2024` | CarSim install root |
| `CARSIM_DB` | auto (`<root>\DATA`) | CarSim working database |
| `MATLAB_EXE` | auto-detected R20xx | MATLAB executable |

Verify with the `carsim_info` tool: it returns `ok: true` when the core paths exist.

---

## Usage — the co-simulation recipe

The agent orchestrates three steps (CarSim MCP → MATLAB MCP → CarSim MCP):

```text
# 1) Prepare (CarSim MCP): generate a runnable co-sim package
res = carsim.scaffold_cosim(out_dir=..., simfile="...\\simfile.sim",
                            kind="slx", model="...\\your_model.slx", stop_time="15")

# 2) Run (MATLAB MCP): execute the driver in the warm MATLAB session
matlab.run_matlab_file(script_path=res.runner_m)      # expect "COSIM_OK"

# 3) Read (CarSim MCP): parse the CarSim ERD output
carsim.read_results(path="...\\Results\\Run_<id>\\LastRun.vs")   # -> channels min/max/final
```

`kind="mfile"` wraps your own MATLAB `.m` controller instead of an `.slx`.
A full worked example and parameter-editing recipes are in
[`docs/carsim-matlab.md`](docs/carsim-matlab.md). Architecture is in
[`docs/DESIGN.md`](docs/DESIGN.md).

> The CarSim solver runs as a DLL **inside** Simulink via the `vs_sf` S-Function —
> you do **not** need the CarSim GUI open to co-simulate.

---

## Development / tests

```shell
python -m pip install -r requirements.txt
python smoke_test.py          # pure-helper checks (paths, parsfile, scaffold, examples)
python mcp_handshake_test.py  # full MCP stdio handshake + carsim_info
claude plugin validate .      # validate the plugin manifest
```

### Note for contributors: never lazy-import native libs inside a tool
Claude Code runs sync MCP tools in a **worker thread**. Importing `numpy`/`scipy`
(or any C-extension) **inside** a tool function makes the first import happen in a
non-main thread, which can **deadlock on the import lock (Windows)** and hang the
tool forever. Keep such imports at **module top level** (see `carsim_core.py`).

---

## Troubleshooting

- **A tool hangs forever** → a heavy/native import was added inside a tool function;
  move it to module top level (see the contributor note above).
- **Code edits don't apply** → MCP servers load at session start; restart Claude Code
  or `/reload-plugins`.
- **CarSim FlexNet error `-8,523` / GUI won't open** → start `cslm.exe` first.
- **`carsim_info` shows `exists:false`** → set `CARSIM_ROOT` / `MATLAB_EXE` env vars.

---

## License

MIT — see [LICENSE](LICENSE). Does not include or circumvent CarSim/MATLAB licensing;
bring your own valid licenses.
