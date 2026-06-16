"""CarSim MCP server.

Thin MCP wrapper around carsim_core. Owns the CarSim domain (parsfiles, headless
solver, simfile generation, co-sim scaffolding, result parsing). MATLAB execution
for the primary co-sim path is delegated to the official MATLAB MCP; this server
only generates the driver scripts and (optionally) runs them unattended.

Run:  python server.py        (stdio transport)
"""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP

import carsim_core as cc

mcp = FastMCP("carsim")


@mcp.tool()
def carsim_info() -> dict:
    """Resolve and existence-check every path the CarSim MCP relies on (CarSim
    root, GUI exe, CLI solver, Simulink solver dir, vs_sf mex, MATLAB exe, working
    database). Use this first to confirm the toolchain is wired up. Returns a dict
    with per-path {path, exists} plus a top-level 'ok' flag."""
    return cc.resolve_paths()


@mcp.tool()
def launch_gui(dataset: str | None = None) -> dict:
    """Launch the CarSim GUI (CarSim_64.exe), non-blocking. Optionally pass a
    dataset/cpar path to open. Returns the new process pid."""
    return cc.launch_gui(dataset)


@mcp.tool()
def list_examples() -> dict:
    """List shipped CPAR archives (Resources\\CPAR_Archives) and Simulink example
    models (DATA\\Extensions\\Simulink). Returns {cpar_archives, simulink_models,
    n_cpar, n_models}."""
    return cc.list_examples()


@mcp.tool()
def read_parsfile(path: str) -> dict:
    """Parse a CarSim parsfile into {path, keywords, raw_lines}. 'keywords' maps
    each KEYWORD to a list of its value-strings (occurrences preserved in order)."""
    return cc.read_parsfile(path)


@mcp.tool()
def write_parsfile(path: str, edits: dict, mode: str = "set",
                   occurrence: int = 1) -> dict:
    """Edit scalar keyword values in a parsfile. 'edits' = {KEYWORD: new_value}.
    Replaces the chosen occurrence of each keyword (appends if absent), writing a
    .bak backup. occurrence: 1-based (default 1 = first); occurrence=0 replaces ALL
    occurrences -- use this for keywords that legitimately repeat (IDIFF, per-axle
    PARSFILE links). For TABLE blocks use set_table; for links use set_link. Returns
    {changed, added, backup}."""
    return cc.write_parsfile(path, edits, mode=mode, occurrence=occurrence)


@mcp.tool()
def run_solver(simfile: str, timeout: int = 120) -> dict:
    """Run the headless CarSim solver (no MATLAB) on a simfile via the CLI solver
    wrapper. Returns {returncode, stdout_tail, stderr_tail, output_dir,
    declared_outputs}."""
    return cc.run_solver(simfile, timeout=timeout)


@mcp.tool()
def generate_simfile(out_path: str, run_parsfile: str | None = None,
                     template_simfile: str | None = None) -> dict:
    """Create/refresh a co-sim simfile at out_path by templating from a
    GUI-generated simfile.sim (template_simfile), optionally swapping the
    referenced parsfile to run_parsfile. Returns {simfile, parsfile,
    templated_from}."""
    return cc.generate_simfile(out_path, run_parsfile=run_parsfile,
                               template_simfile=template_simfile)


@mcp.tool()
def scaffold_cosim(out_dir: str, simfile: str, kind: str = "slx",
                   model: str | None = None, controller_call: str | None = None,
                   stop_time: str = "5") -> dict:
    """Generate a ready-to-run co-sim package in out_dir. kind='slx' wires a
    Simulink model containing the CarSim S-Function (defaults to a copy of the
    shipped example.mdl). kind='mfile' wraps a MATLAB .m controller call (must
    assign a variable 'results'). Writes run_cosim.m. Hand the returned 'runner_m'
    to the MATLAB MCP (run_matlab_file) to execute. Returns {runner_m, model,
    simfile, results_path, solver_dir}."""
    return cc.scaffold_cosim(out_dir, simfile, kind=kind, model=model,
                             controller_call=controller_call, stop_time=stop_time)


@mcp.tool()
def read_results(path: str, max_channels: int = 50, pattern: str | None = None,
                 names_only: bool = False) -> dict:
    """Parse a results file (.mat, or a CarSim ERD .vs/.vsb) into per-channel
    summaries {len, min, max, final, unit}. A full CarSim run can have 1000+
    channels, so use pattern= to keep only matching names (case-insensitive
    substring or regex, e.g. pattern='Vx' or 'Ay|AVz|Yaw'); use names_only=True to
    list channel names+units without reading the binary (fast discovery). Returns
    {source, n, n_returned, truncated, channels|names}."""
    return cc.read_results(path, max_channels=max_channels, pattern=pattern,
                           names_only=names_only)


@mcp.tool()
def run_cosim_headless(runner_m: str, timeout: int = 600) -> dict:
    """OPTIONAL FALLBACK. Run a generated co-sim driver via `matlab -batch`
    (cold, unattended). The primary path is to run runner_m through the official
    MATLAB MCP instead (warm, visible session). Returns {returncode, cosim_ok,
    stdout_tail, stderr_tail, results_path}."""
    return cc.run_cosim_headless(runner_m, timeout=timeout)


# --------------------------------------------------------------------------- #
# Database navigation: browse/find the CarSim GUI libraries from code
# --------------------------------------------------------------------------- #

@mcp.tool()
def list_libraries() -> dict:
    """List the CarSim GUI libraries (DATA subfolders that hold datasets, e.g.
    Suspensions, Powertrain, Vehicles, Steering, Tires, IO_Channels). Each entry
    has {name, n_datasets, categories}. Start here to discover where a parameter
    lives. Returns {db, n, libraries}."""
    return cc.list_libraries()


@mcp.tool()
def browse_library(library: str, category: str | None = None,
                   limit: int = 300) -> dict:
    """List datasets inside a library (optionally a category subfolder), each with
    its GUI identity {dataset, category, library} and file path. e.g.
    browse_library('Powertrain', 'Motor') or browse_library('Vehicles'). Feed a
    returned 'file' to get_dataset to read/edit it. Returns {library, category, n,
    datasets}."""
    return cc.browse_library(library, category=category, limit=limit)


@mcp.tool()
def find_dataset(query: str, limit: int = 50) -> dict:
    """Fuzzy-search the whole database by GUI identity text (library/dataset/
    category names). Answers 'where do I set X', e.g. find_dataset('motor'),
    find_dataset('sprung mass'), find_dataset('jounce'). Returns {query, n,
    results:[{file, library, dataset, category}]}."""
    return cc.find_dataset(query, limit=limit)


# --------------------------------------------------------------------------- #
# Structured dataset read/write + keyword dictionary
# --------------------------------------------------------------------------- #

@mcp.tool()
def get_dataset(path: str, annotate: bool = True, max_tables: int = 40) -> dict:
    """Read a dataset parsfile into structured form: GUI identity + scalar 'params'
    (each {value, desc?, unit?}) + 'tables' (each {method, rows, desc?, unit?}).
    With annotate=True, keywords are tagged with meaning + unit from the local
    keyword dictionary (build it once via build_keyword_dictionary). This is the
    high-level read for editing vehicle/suspension/powertrain/motor parameters.
    Returns {path, identity, n_params, params, n_tables, tables}."""
    return cc.get_dataset(path, annotate=annotate, max_tables=max_tables)


@mcp.tool()
def set_dataset(path: str, edits: dict, backup: bool = True) -> dict:
    """Edit scalar keyword values in a dataset (edits = {KEYWORD: new_value}) and
    refresh its #Modified stamp so the change shows in the GUI list. Writes a .bak
    backup. e.g. set_dataset(massfile, {'M_SU': '1500', 'H_CG_SU': '530'}).
    Returns {changed, added, backup, modified_stamp}."""
    return cc.set_dataset(path, edits, backup=backup)


@mcp.tool()
def describe_keyword(keyword: str) -> dict:
    """Look up a parsfile keyword's meaning + unit in the local keyword dictionary
    (e.g. describe_keyword('M_SU') -> mass of sprung mass, kg). If unknown, returns
    similar keyword names. Build the dictionary once with build_keyword_dictionary.
    Returns {keyword, known, desc?, unit?, similar?}."""
    return cc.describe_keyword(keyword)


@mcp.tool()
def build_keyword_dictionary(refresh: bool = True, max_files: int = 0) -> dict:
    """Build the local keyword dictionary (keywords.json) by harvesting CarSim's
    own echo files (DATA\\Results\\**\\*_echo.par), mapping each keyword to its
    unit + description. Run once per install (and after upgrading CarSim).
    refresh=False just reports the existing dictionary; max_files>0 does a quick
    partial build. The dictionary is a LOCAL artifact (CarSim's proprietary text),
    not redistributed. Returns {path, n_keywords, n_echo_files, refreshed}."""
    return cc.build_keyword_dictionary(refresh=refresh, max_files=max_files)


# --------------------------------------------------------------------------- #
# Table editing + link/assembly layer + dataset creation
# --------------------------------------------------------------------------- #

@mcp.tool()
def set_table(path: str, keyword: str, rows: list, method: str | None = None,
              occurrence: int = 1) -> dict:
    """Replace the rows of a CarSim TABLE block (KEYWORD [method] ... ENDTABLE). Most
    chassis/powertrain/battery PHYSICS is tables: motor torque maps, tire Pacejka
    carpets, battery OCV-vs-SOC, spring/damper curves. 'rows' is a list of rows, each
    a string ("0, 1") or a list ([0, 1]). method overrides the interpolation token
    (LINEAR/STEP/SPLINE...) if given. occurrence picks the Nth table block (1-based)
    if the keyword repeats. Re-reads to verify. Returns {keyword, n_rows, method,
    backup, verified}. (set_dataset only edits scalars; this owns tables.)"""
    return cc.set_table(path, keyword, rows, method=method, occurrence=occurrence)


@mcp.tool()
def get_links(path: str) -> dict:
    """List a dataset's subsystem links (the PARSFILE lines) with their slot labels
    and resolved identity -- the 'what does this assembly contain' view. A Vehicle
    Assembly links steering/suspension/tires/sprung-mass/powertrain/brakes; an EV
    powertrain links HEV(battery+motors)/PMC/diffs. Returns {path, datadir, n,
    links:[{index, slot, parsfile, exists, library, dataset, category}]}."""
    return cc.get_links(path)


@mcp.tool()
def set_link(path: str, new_parsfile: str, index: int | None = None,
             slot: str | None = None) -> dict:
    """Swap ONE subsystem link to a different dataset -- the core 'assemble a vehicle'
    op (e.g. drop an EV powertrain into a vehicle, point a powertrain at a different
    battery/motor dataset). Identify the link by index (from get_links) or by slot
    substring ('Powertrain','Tire','Steering'). new_parsfile is a DATADIR-relative
    path (e.g. 'Powertrain\\4wd\\4WD_<id>.par'). Rewrites the PARSFILE line + refreshes
    the matching #BlueLink. (set_dataset can't target one of N repeated PARSFILE
    lines; this can.) Returns {changed_index, slot, old, new, backup}."""
    return cc.set_link(path, new_parsfile, index=index, slot=slot)


@mcp.tool()
def resolve_assembly(path: str, max_depth: int = 3) -> dict:
    """Recursively expand a dataset's link tree -- the full vehicle composition:
    Vehicle Assembly -> Powertrain -> HEV(battery/motors) -> tables. Returns a nested
    {slot, path, library, dataset, children:[...]} tree (bounded by max_depth). Use to
    understand or audit what a vehicle is built from before editing."""
    return cc.resolve_assembly(path, max_depth=max_depth)


@mcp.tool()
def clone_dataset(src_path: str, out_path: str | None = None,
                  new_dataset: str | None = None,
                  new_category: str | None = None) -> dict:
    """Copy a dataset to a NEW .par with a fresh #FileID + identity, so you can create
    datasets instead of only editing shipped (read-only) ones. new_dataset/
    new_category override the #DataSet/#Category and #FullDataName. out_path defaults
    to the source folder with a fresh '<prefix>_<uuid>.par' name. Returns {path,
    file_id, identity}."""
    return cc.clone_dataset(src_path, out_path=out_path, new_dataset=new_dataset,
                            new_category=new_category)


@mcp.tool()
def consolidate_run(assembly_path: str, procedure_path: str | None = None,
                    out_path: str | None = None) -> dict:
    """EXPERIMENTAL / inspection. Flatten a Vehicle Assembly (+ optional Procedure)
    into one parsfile by recursively inlining every PARSFILE link. NOTE: a naive
    link-order inline does NOT satisfy CarSim's parse-ORDER for complex vehicles (a
    table can be referenced before its cross-link definition), so the result is often
    NOT directly runnable -- for a runnable headless config, edit a baked
    DATA\\Results\\<run>\\Run_all.par instead, or use the GUI 'Generate Files' after
    set_link. Useful for inspecting/diffing the full inlined config. Returns {path,
    n_lines, n_inlined, runnable, note}."""
    return cc.consolidate_run(assembly_path, procedure_path=procedure_path,
                              out_path=out_path)


if __name__ == "__main__":
    mcp.run()
