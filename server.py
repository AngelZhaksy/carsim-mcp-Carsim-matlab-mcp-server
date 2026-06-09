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
def write_parsfile(path: str, edits: dict, mode: str = "set") -> dict:
    """Edit keyword values in a parsfile. 'edits' = {KEYWORD: new_value}. For
    mode='set', replaces the first occurrence of each keyword (appends if absent),
    writing a .bak backup. Returns {changed, added, backup}. Use this to change
    vehicle parameters, I/O import/export channel config, and dataset links."""
    return cc.write_parsfile(path, edits, mode=mode)


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
def read_results(path: str, max_channels: int = 50) -> dict:
    """Parse a results file (.mat saved by the co-sim driver, or a CarSim .erd)
    into per-channel summaries {len, min, max, final}. Returns {source, n,
    channels}."""
    return cc.read_results(path, max_channels=max_channels)


@mcp.tool()
def run_cosim_headless(runner_m: str, timeout: int = 600) -> dict:
    """OPTIONAL FALLBACK. Run a generated co-sim driver via `matlab -batch`
    (cold, unattended). The primary path is to run runner_m through the official
    MATLAB MCP instead (warm, visible session). Returns {returncode, cosim_ok,
    stdout_tail, stderr_tail, results_path}."""
    return cc.run_cosim_headless(runner_m, timeout=timeout)


if __name__ == "__main__":
    mcp.run()
