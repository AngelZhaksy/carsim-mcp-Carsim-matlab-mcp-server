"""Standalone checks for the pure helpers in carsim_core (no MCP runtime needed).

Run:  .venv\\Scripts\\python.exe smoke_test.py
"""

import tempfile
from pathlib import Path

import carsim_core as cc


def test_resolve_paths():
    info = cc.resolve_paths()
    for key in ("gui_exe", "cli_solver", "simulink_solver_dir", "vs_sf_mex", "matlab_exe"):
        assert info[key]["exists"], f"{key} missing: {info[key]['path']}"
    print("resolve_paths: ok")


def test_parsfile():
    with tempfile.TemporaryDirectory() as d:
        f = Path(d) / "veh.par"
        f.write_text("! a CarSim parsfile\nOPT_GROUND_CONTACT 1\nM_S 1653\n", encoding="latin-1")
        parsed = cc.read_parsfile(str(f))
        assert parsed["keywords"]["M_S"][0] == "1653", parsed["keywords"]
        res = cc.write_parsfile(str(f), {"M_S": "1700", "NEW_KW": "42"})
        assert "M_S" in res["changed"] and "NEW_KW" in res["added"], res
        assert Path(res["backup"]).exists()
        reparsed = cc.read_parsfile(str(f))
        assert reparsed["keywords"]["M_S"][0] == "1700"
        assert reparsed["keywords"]["NEW_KW"][0] == "42"
    print("parsfile: ok")


def test_examples():
    ex = cc.list_examples()
    names = {m["name"] for m in ex["simulink_models"]}
    assert "example.mdl" in names, "example.mdl not found"
    assert "abs_brake_slx.slx" in names, "abs_brake_slx.slx not found"
    assert ex["n_cpar"] > 50, ex["n_cpar"]
    print(f"examples: ok ({ex['n_cpar']} cpar, {ex['n_models']} models)")


def test_scaffold_slx():
    with tempfile.TemporaryDirectory() as d:
        # Fake simfile path is fine; scaffolding only writes the driver text.
        fake_sim = str(Path(d) / "simfile.sim")
        Path(fake_sim).write_text("PARSFILE foo.par\n", encoding="latin-1")
        res = cc.scaffold_cosim(d, fake_sim, kind="slx")
        text = Path(res["runner_m"]).read_text(encoding="utf-8")
        assert "@SOLVER_DIR@" not in text and "@SIMFILE@" not in text, "placeholders left"
        assert fake_sim.replace("\\", "\\") in text or "simfile.sim" in text
        assert Path(res["model"]).exists(), "default model copy missing"
    print("scaffold(slx): ok")


def test_scaffold_mfile():
    with tempfile.TemporaryDirectory() as d:
        fake_sim = str(Path(d) / "simfile.sim")
        Path(fake_sim).write_text("PARSFILE foo.par\n", encoding="latin-1")
        res = cc.scaffold_cosim(d, fake_sim, kind="mfile",
                                controller_call="results = sbw_controller(simfile, stop_time);")
        text = Path(res["runner_m"]).read_text(encoding="utf-8")
        assert "sbw_controller" in text and "@CONTROLLER_CALL@" not in text
    print("scaffold(mfile): ok")


if __name__ == "__main__":
    test_resolve_paths()
    test_parsfile()
    test_examples()
    test_scaffold_slx()
    test_scaffold_mfile()
    print("\nALL SMOKE TESTS PASSED")
