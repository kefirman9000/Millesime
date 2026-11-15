"""Checks for reorganizing a library, recognizing its layout and the time-left estimate.
Run from the repository folder:  python tests/test_reorganize.py   (no Qt needed)"""
import os, sys, shutil, tempfile
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import engine as E

def test_reorganize_and_undo():

    tmp = Path(tempfile.mkdtemp())
    card = tmp / "card" / "DCIM" / "100CANON"; card.mkdir(parents=True)
    lib = tmp / "Library"; lib.mkdir()

    def fake(name, date, size=40000):
        p = card / name
        p.write_bytes(b"\xff\xd8" + date.encode() + os.urandom(size))
        return p
    fake("IMG_0001.JPG", "2019:07:04 15:30:12")
    fake("IMG_0001.CR2", "2019:07:04 15:30:12")
    (card / "IMG_0001.xmp").write_text("xmp")
    fake("IMG_0002.CR2", "2019:07:05 10:00:00")
    fake("MVI_0003.MOV", "2019:07:05 11:00:00")
    # a second card with the same file name, different day -> collision when merged into one folder later
    card2 = tmp / "card2" / "DCIM"; card2.mkdir(parents=True)
    p = card2 / "IMG_0002.CR2"; p.write_bytes(b"\xff\xd8" + b"2019:07:05 12:00:00" + os.urandom(40000))

    s = dict(E.DEFAULT_SETTINGS, layout="year_month_day")
    items = list(E.gather([tmp / "card", tmp / "card2"], s, lib))
    store = E.CardStore(lib)
    for it in items:
        it.source_id, _n, it.source_rel = E.source_key(it.src)
        E.read_info(it, s)
    E.plan(items, s)
    idx = E.DestIndex(lib); idx.build()
    for it in items:
        E.process(it, idx)
        store.record(it, "card")
    idx.save(); idx.close(); store.close()
    E.save_profile(lib, s)
    # a hand-made folder that must be left alone
    (lib / "Photos" / "Events" / "Wedding").mkdir(parents=True)
    shutil.copy(card / "IMG_0001.JPG", lib / "Photos/Events/Wedding/W.JPG")

    def tree():
        return sorted(str(p.relative_to(lib)) for p in lib.rglob("*") if p.is_file() and ".millesime" not in p.parts)
    print("BEFORE:", *tree(), sep="\n  ")
    det = E.detect_layout(lib)
    print("DETECTED:", det)
    assert det["profile"]["layout"] == "year_month_day", det
    assert det["profile"]["camera_videos"] == "photos_sub"
    print("STORED:", E.read_profile(lib))

    def hashes():
        con = E.open_db(lib); r = dict(con.execute("SELECT rel, hash FROM files")); con.close(); return r
    h_before = hashes()

    new = dict(s, layout="year_month", raw_split="split")
    plan = E.plan_reorganize(lib, new)
    print("PLAN:", {k: v for k, v in plan.items() if k != "moves"})
    for m in plan["moves"]: print("  ", m["pairs"])
    res = E.run_reorganize(lib, plan, E.profile_of(s))
    print("RESULT:", {k: v for k, v in res.items() if k != "left"})
    after = tree(); print("AFTER:", *after, sep="\n  ")
    assert "Photos/2019/07 - July/RAW/IMG_0002.CR2" in after and "Photos/2019/07 - July/RAW/IMG_0002_1.CR2" in after
    assert "Photos/2019/07 - July/RAW/IMG_0001.xmp" in after, "sidecar went with RAW"
    assert "Photos/2019/07 - July/JPEG/IMG_0001.JPG" in after
    assert "Photos/Events/Wedding/W.JPG" in after
    assert not (lib / "Photos/2019/07 - July/2019-07-04").exists(), "empty day folders removed"
    h_after = hashes()
    assert sorted(h_before.values()) == sorted(v for k, v in h_after.items() if "Wedding" not in k), "checksums kept"
    con = E.open_db(lib); dests = [r[0] for r in con.execute("SELECT dest FROM card_files")]; con.close()
    assert all((lib / d).exists() for d in dests), dests
    assert E.read_profile(lib)["layout"] == "year_month"
    assert E.detect_layout(lib)["profile"]["raw_split"] == "split"

    # second plan should be a no-op
    p2 = E.plan_reorganize(lib, new); assert not p2["moves"], p2["moves"]

    # interrupted run: simulate journal with one entry whose move happened but DB didn't
    u = E.undo_reorganize(lib)
    print("UNDO:", {k: v for k, v in u.items() if k != "left"})
    assert tree() == sorted(set(tree())) and set(tree()) == set(["Photos/Events/Wedding/W.JPG"] + [x for x in h_before]), tree()
    assert E.read_profile(lib)["layout"] == "year_month_day"
    assert hashes().keys() >= h_before.keys()

    # crash simulation
    plan = E.plan_reorganize(lib, new)
    a, b = plan["moves"][0]["pairs"][0]
    (lib / ".millesime" / "moving.csv").write_text(f"move,{a},{b}\n")
    E._move_no_overwrite(lib / a, lib / b)
    fixed = E.reconcile_moves(lib); print("RECONCILED:", fixed)
    assert fixed == 1 and b in hashes()
    assert E.last_reorganize(lib)
    print("ETA demo:")
    eta = E.Eta()
    # slow ramp for 5 s then steady 100 MB/s, 10 GB total
    done = 0
    for t in range(0, 40):
        rate = 5e6 if t < 5 else 100e6
        done += rate
        est = eta.update(float(t), done, 10e9)
        if t % 4 == 0: print(f"  t={t:2d}s  {E.format_left(est)}")



def test_renamed_folders_stay():
    tmp = Path(tempfile.mkdtemp()); lib = tmp / "Lib"
    def put(rel, date):
        p = lib / rel; p.parent.mkdir(parents=True, exist_ok=True); p.write_bytes(b"\xff\xd8" + date.encode() + os.urandom(1000))
    # day folder renamed to an event, a month folder renamed, and a plain one
    put("Photos/2019/07 - July/2019-07-04 Smith Wedding/A.JPG", "2019:07:04 10:00:00")
    put("Photos/2019/08 - August Italy/2019-08-10/B.JPG", "2019:08:10 10:00:00")
    put("Photos/2019/09 - September/2019-09-01/C.JPG", "2019:09:01 10:00:00")
    put("Photos/2019/09 - September/2019-09-01/D.JPG", "2018:01:01 10:00:00")    # date doesn't match: hand-moved
    put("Videos/Holiday.mkv", "")                                                 # flat other video
    aliases = {"Photos/2019/07 - July/2019-07-04": "Photos/2019/07 - July/2019-07-04 Smith Wedding",
               "Photos/2019/08 - August": "Photos/2019/08 - August Italy"}
    E.open_db(lib).close()
    print(E.detect_layout(lib, aliases))
    s = dict(E.DEFAULT_SETTINGS, layout="year_month", other_videos="flat")
    p = E.plan_reorganize(lib, s, aliases)
    print({k: v for k, v in p.items() if k != "moves"})
    for m in p["moves"]: print("  ", m["pairs"][0])
    dests = {m["pairs"][0][0]: m["pairs"][0][1] for m in p["moves"]}
    assert dests["Photos/2019/08 - August Italy/2019-08-10/B.JPG"] == "Photos/2019/08 - August Italy/B.JPG"
    assert dests["Photos/2019/09 - September/2019-09-01/C.JPG"] == "Photos/2019/09 - September/C.JPG"
    assert p["renamed"] == 1 and p["mismatch"] == 1 and len(dests) == 2



if __name__ == "__main__":
    test_reorganize_and_undo()
    test_renamed_folders_stay()
    print("All checks passed")
