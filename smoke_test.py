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


def test_db_navigation():
    """Database navigation + structured read. Skipped if no CarSim DB present."""
    try:
        cc._db_dir()
    except FileNotFoundError:
        print("db_navigation: skipped (no CarSim database on this machine)")
        return
    libs = cc.list_libraries()
    assert libs["n"] > 0, "no libraries found"
    hits = cc.find_dataset("sprung mass", limit=5)
    if hits["n"]:
        f = hits["results"][0]["file"]
        ds = cc.get_dataset(f, annotate=True)
        assert ds["identity"]["library"], "identity not parsed"
        assert ds["n_params"] > 0, "no params parsed"
    print(f"db_navigation: ok ({libs['n']} libraries)")


def test_set_dataset_roundtrip():
    """set_dataset on a writable copy of a real dataset (handles read-only src)."""
    import os
    import shutil
    import stat
    try:
        cc._db_dir()
    except FileNotFoundError:
        print("set_dataset: skipped (no CarSim database)")
        return
    hits = cc.find_dataset("sprung mass", limit=1)
    if not hits["n"]:
        print("set_dataset: skipped (no sprung-mass dataset)")
        return
    with tempfile.TemporaryDirectory() as d:
        tmp = str(Path(d) / "ds.par")
        shutil.copy2(hits["results"][0]["file"], tmp)
        os.chmod(tmp, os.stat(tmp).st_mode | stat.S_IWRITE)
        res = cc.set_dataset(tmp, {"M_SU": "1500"})
        assert "M_SU" in res["changed"], res
        assert cc.get_dataset(tmp, annotate=False)["params"]["M_SU"]["value"] == "1500"
    print("set_dataset: ok")


def test_keyword_dictionary():
    """Keyword dictionary lookup. Skipped if not built yet."""
    d = cc._load_keyword_dict()
    if not d:
        print("keyword_dict: skipped (run build_keyword_dictionary() first)")
        return
    hit = cc.describe_keyword("M_SU")
    assert hit["known"], hit
    assert hit.get("unit") == "kg", hit
    print(f"keyword_dict: ok ({len(d)} keywords; M_SU = {hit.get('unit')})")


def test_links_and_table():
    """Link/assembly layer + set_table + clone_dataset. Skipped without a CarSim DB."""
    import os
    import shutil
    import stat
    try:
        db = cc._db_dir()
    except FileNotFoundError:
        print("links_table: skipped (no CarSim database)")
        return
    # get_links on a Vehicle Assembly (should expose a Powertrain slot)
    asm = cc.browse_library("Vehicles", "Assembly", limit=300)["datasets"]
    if not asm:
        print("links_table: skipped (no Vehicle Assembly datasets)")
        return
    links = cc.get_links(asm[0]["file"])
    assert links["n"] > 0, "assembly had no links"
    tree = cc.resolve_assembly(asm[0]["file"], max_depth=2)
    assert tree.get("children"), "resolve_assembly returned no children"

    with tempfile.TemporaryDirectory() as d:
        # clone_dataset gives a fresh #FileID
        cl = cc.clone_dataset(asm[0]["file"], out_path=str(Path(d) / "clone.par"),
                              new_dataset="smoke clone")
        assert cl["identity"]["dataset"] == "smoke clone", cl
        assert cl["file_id"] != Path(asm[0]["file"]).stem, "FileID not refreshed"

        # set_table on a dataset that has a table (Suspensions Jounce_Rebound)
        jr = cc.browse_library("Suspensions", "Jounce_Rebound", limit=5)["datasets"]
        if jr:
            tmp = str(Path(d) / "jr.par")
            shutil.copy2(jr[0]["file"], tmp)
            os.chmod(tmp, os.stat(tmp).st_mode | stat.S_IWRITE)
            ds = cc.get_dataset(tmp, annotate=False)
            tkey = next(iter(ds["tables"]), None)
            if tkey:
                res = cc.set_table(tmp, tkey, [[10, 0], [20, 5000]])
                assert res["verified"] and res["n_rows"] == 2, res
    print("links_table: ok (get_links/resolve_assembly/clone_dataset/set_table)")


if __name__ == "__main__":
    test_resolve_paths()
    test_parsfile()
    test_examples()
    test_scaffold_slx()
    test_scaffold_mfile()
    test_db_navigation()
    test_set_dataset_roundtrip()
    test_keyword_dictionary()
    test_links_and_table()
    print("\nALL SMOKE TESTS PASSED")
