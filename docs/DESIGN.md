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

## Tool surface (CarSim MCP) — 17 tools
| Tool | Purpose |
|---|---|
| `carsim_info` | Resolve & sanity-check paths/version; the "is it wired up" probe |
| `launch_gui(dataset?)` | Open `CarSim_64.exe`, optionally on a dataset |
| `list_examples()` | Discover shipped CPAR archives + Simulink example models |
| `list_libraries()` | List GUI libraries (DATA subfolders) + dataset counts + categories |
| `browse_library(library, category?)` | List datasets in a library/category with GUI identity + paths |
| `find_dataset(query)` | Fuzzy-search the whole DB by GUI identity text ("where do I set X") |
| `get_dataset(path)` | Structured read: identity + scalar params + tables, annotated with meaning+unit |
| `set_dataset(path, edits)` | Edit scalar params; bump `#Modified`; auto `.bak`; clears read-only |
| `describe_keyword(kw)` | Keyword → meaning + unit from the local dictionary |
| `build_keyword_dictionary()` | Harvest CarSim echo files → `keywords.json` (local, per-install) |
| `read_parsfile(path)` | Low-level parse keyword/value parsfile lines into a dict |
| `write_parsfile(path, edits)` | Low-level edit vehicle params, I/O channels, dataset links (in place, backup) |
| `run_solver(simfile)` | Headless CarSim-only solver run → result paths + log |
| `generate_simfile(dataset, out)` | Produce a co-sim `simfile` from a dataset |
| `scaffold_cosim(dataset, kind, out_dir)` | Generate `.slx` (vs_sf wired) **or** `.m` driver + simfile + a `run_cosim.m`; return the path for the MATLAB MCP to run |
| `read_results(path)` | Parse output channels (.erd/.mat) → time series + summary stats |
| `run_cosim_headless(...)` | **Optional fallback** — shell out to `matlab -batch` for unattended batch when MATLAB MCP is inconvenient |

## Database navigation layer (added 2026-06-12)
CarSim's GUI is closed-source, but every GUI dataset is a plain-text `.par` under
`DATA\<Library>\<Category>\`, carrying its own GUI breadcrumb (`#FullDataName A`B`C` near
the top; `#Library/#DataSet/#Category/#FileID/#Modified` block near the end). The navigation
tools (`list_libraries`/`browse_library`/`find_dataset`/`get_dataset`/`set_dataset`) turn that
into a searchable, editable catalog — so the agent edits suspension/mass/powertrain/motor
parameters **from code**, no GUI pixel-automation.

The keyword **meaning + unit** dictionary is harvested from CarSim's own solver echo files
(`DATA\Results\**\*_echo.par`, lines like `M_SU 1370 ; kg ! Mass of unladen sprung mass`).
`build_keyword_dictionary()` unions them into `keywords.json`. That file contains CarSim's
proprietary documentation text, so it is a **local, `.gitignore`d artifact** — generated per
install, never redistributed.

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
- Parameter editing is reliable for **any scalar keyword** in any dataset (suspension geometry,
  sprung mass, motor/engine maps, I/O channels) via `get_dataset`/`set_dataset`, and for swapping
  **existing** dataset links. Authoring a *brand-new* dataset from scratch still benefits from the
  GUI (then reference/edit it via the MCP).
- `CarSim_64.exe` has **no documented CLI to open a specific screen** (it opens to the last Run
  Control), so "open the suspension/IO window" is intentionally handled by editing the underlying
  parsfiles directly — matching the pure-code, headless design.
- GUI pixel-automation is out of scope.

## Deliverables
1. `E:\desktop\file\train\carsim-mcp\` — server (`server.py`), MATLAB templates, README, venv
2. Registration in Claude Code MCP config so `carsim` tools load
3. `E:\desktop\file\train\carsim-matlab.md` — agent-facing usage guide
4. Verification: `carsim_info` green → launch CarSim → set up one built-in Simulink example → run co-sim through the MCP + MATLAB MCP → confirm numeric results
