# CarSim MCP — Design (2026-06-09)

## Goal
A Python MCP server (`carsim`) that lets Claude Code drive CarSim 2024 for parameter
editing and CarSim↔MATLAB/Simulink co-simulation (both `.m` and `.slx`). MATLAB
execution is delegated to the **official MATLAB MCP** (warm, visible session); the
CarSim MCP owns the CarSim domain. Agent orchestrates the hand-off via the filesystem.

## Environment (verified 2026-06-09)
- CarSim root: `E:\Carsim2024` (GUI `CarSim_64.exe`, CLI solver `Programs\VS_SolverWrapper_CLI_64.exe`)
- CarSim↔Simulink bridge: `E:\Carsim2024\Programs\solvers\Matlab\vs_sf.mexw64` (+ `vs_sfv`, `vs_ctl`, `vs_dyn`, ...)
- Shipped co-sim examples: `E:\Carsim2024\DATA\Extensions\Simulink\` (`example.mdl`, `Base_Model.mdl`, `abs_brake_slx.slx`, `ext_steer_*.mdl`, ...)
- Shipped CPAR archives: `E:\Carsim2024\Resources\CPAR_Archives\*.cpar`
- MATLAB: `E:\Program Files\MATLAB\R2023b\bin\matlab.exe` (MATLAB MCP live)
- Python: 3.11.9 (system) ; Node v24
- **CarSim working database not yet initialized** — one-time GUI launch needed (computer-use) to create `CarSim_Data` and generate a co-sim `simfile`.

## Division of labor
- **CarSim MCP** — CarSim domain only: launch GUI, list examples, read/write parsfiles,
  run headless CarSim solver, generate simfile, scaffold co-sim packages, parse results.
- **MATLAB MCP (official)** — all MATLAB execution/compilation: `run_matlab_file`,
  `evaluate_matlab_code`. Reuses the warm, visible MATLAB session.
- Hand-off is via the filesystem (simfile / .slx / .m / results.mat).

## Tool surface (CarSim MCP)
| Tool | Purpose |
|---|---|
| `carsim_info` | Resolve & sanity-check paths/version; the "is it wired up" probe |
| `launch_gui(dataset?)` | Open `CarSim_64.exe`, optionally on a dataset |
| `list_examples()` | Discover shipped CPAR archives + Simulink example models |
| `read_parsfile(path)` | Parse keyword/value parsfile lines into a dict |
| `write_parsfile(path, edits)` | Edit vehicle params, I/O channels, dataset links (in place, backup) |
| `run_solver(simfile)` | Headless CarSim-only solver run → result paths + log |
| `generate_simfile(dataset, out)` | Produce a co-sim `simfile` from a dataset |
| `scaffold_cosim(dataset, kind, out_dir)` | Generate `.slx` (vs_sf wired) **or** `.m` driver + simfile + a `run_cosim.m`; return the path for the MATLAB MCP to run |
| `read_results(path)` | Parse output channels (.erd/.mat) → time series + summary stats |
| `run_cosim_headless(...)` | **Optional fallback** — shell out to `matlab -batch` for unattended batch when MATLAB MCP is inconvenient |

## Co-simulation recipe (agent-orchestrated)
1. `carsim.scaffold_cosim(...)` → writes `run_cosim.m` + `model.slx` + `simfile.sim`
2. MATLAB MCP `run_matlab_file("run_cosim.m")` → runs co-sim in the warm MATLAB, saves `results.mat`
3. `carsim.read_results("results.mat")` → parsed channels + stats

## Config (env vars)
- `CARSIM_ROOT=E:\Carsim2024`
- `CARSIM_DB=<working database>` (auto-detected from registry/Documents, overridable)
- `MATLAB_EXE=E:\Program Files\MATLAB\R2023b\bin\matlab.exe`
- CarSim Simulink solver dir (`Programs\solvers\Matlab`) added to MATLAB path inside generated scripts.

## Scope notes / caveats
- Parameter editing is reliable for **numeric vehicle params and I/O channel config** in parsfiles,
  and for swapping **existing** dataset links (incl. choosing among existing suspension datasets).
  Authoring a *new* suspension/vehicle dataset from scratch still requires the CarSim GUI.
- GUI pixel-automation is out of scope except the one-time database/simfile bootstrap.

## Deliverables
1. `E:\desktop\file\train\carsim-mcp\` — server (`server.py`), MATLAB templates, README, venv
2. Registration in Claude Code MCP config so `carsim` tools load
3. `E:\desktop\file\train\carsim-matlab.md` — agent-facing usage guide
4. Verification: `carsim_info` green → launch CarSim → set up one built-in Simulink example → run co-sim through the MCP + MATLAB MCP → confirm numeric results
