"""suumo/snapshot.py: data/'s prefecture folders as one deterministic asset, its pointer, the gate."""
import io
import json
import tarfile

import pytest
import zstandard

from suumo import snapshot
from tests.helpers import rec, write


def data_dir(tmp_path, n=3, pref="tokyo"):
    d = tmp_path / "data"
    write(d, [rec(i) for i in range(n)], pref=pref)
    (d / pref / "pages" / "used_condo").mkdir(parents=True)
    (d / pref / "pages" / "used_condo" / "13219.jsonl").write_text('{"id": "0", "img": {}}\n', encoding="utf-8")
    (d / "events").mkdir(exist_ok=True)
    (d / "events" / "20261020T050000-s00.json").write_text("[]", encoding="utf-8")
    (d / "fx.json").write_text("{}", encoding="utf-8")
    return d


def pointer(d, out):
    size, sha = snapshot.pack(d, out)
    return {"tag": "t", "asset": out.name, "size": size, "sha256": sha, "groups": snapshot.manifest(d)}


def test_manifest_groups_prefecture_children_and_counts_records(tmp_path):
    m = snapshot.manifest(data_dir(tmp_path))
    assert set(m) == {"tokyo/used_condo", "tokyo/pages", "tokyo/areas.json"}   # not events/, not fx.json
    assert m["tokyo/used_condo"]["records"] == 3 and "records" not in m["tokyo/pages"]
    assert snapshot.records_by_pref(m) == {"tokyo": 3}


def test_pack_is_deterministic_and_unpack_round_trips(tmp_path):
    d = data_dir(tmp_path)
    p = pointer(d, tmp_path / "a.tar.zst")
    assert snapshot.pack(d, tmp_path / "b.tar.zst") == (p["size"], p["sha256"])   # same data, same bytes
    out = tmp_path / "out"
    (out / "events").mkdir(parents=True)
    (out / "events" / "keep.json").write_text("[]")
    (out / "osaka").mkdir()   # a prefecture the snapshot doesn't have: replaced, not merged
    assert snapshot.unpack(tmp_path / "a.tar.zst", out, p) == p["groups"]
    assert not (out / "osaka").exists() and (out / "events" / "keep.json").exists()
    assert (out / "tokyo" / "used_condo" / "13219.jsonl").read_text() == \
        (d / "tokyo" / "used_condo" / "13219.jsonl").read_text()


def test_unpack_refuses_a_snapshot_that_isnt_the_pointers(tmp_path):
    d = data_dir(tmp_path)
    p = pointer(d, tmp_path / "a.tar.zst")
    with pytest.raises(snapshot.SnapshotError, match="sha256"):
        snapshot.unpack(tmp_path / "a.tar.zst", tmp_path / "out", {**p, "sha256": "0" * 64})
    out = tmp_path / "out2"
    (out / "tokyo").mkdir(parents=True)
    (out / "tokyo" / "mine.json").write_text("1")
    with pytest.raises(snapshot.SnapshotError, match="differs"):
        snapshot.unpack(tmp_path / "a.tar.zst", out,
                        {**p, "groups": {**p["groups"], "tokyo/land": {"files": 1, "sha256": "x"}}})
    assert sorted(x.name for x in out.iterdir()) == ["tokyo"]                 # left as it was, no scratch folder
    assert (out / "tokyo" / "mine.json").read_text() == "1"


def test_unpack_refuses_a_truncated_snapshot(tmp_path):
    d = data_dir(tmp_path)
    p = pointer(d, tmp_path / "a.tar.zst")
    blob = (tmp_path / "a.tar.zst").read_bytes()[:-50]
    (tmp_path / "cut.tar.zst").write_bytes(blob)
    cut = {**p, "size": len(blob), "sha256": snapshot.sha256_file(tmp_path / "cut.tar.zst")}
    with pytest.raises(snapshot.SnapshotError):
        snapshot.unpack(tmp_path / "cut.tar.zst", tmp_path / "out", cut)


def test_only_prefecture_folders_are_snapshotted(tmp_path):
    d = data_dir(tmp_path)
    (d / "canary").mkdir()
    (d / "canary" / "candidates.json").write_text("{}")
    (d / "p").mkdir()                                                         # not a prefecture slug
    assert {k.split("/")[0] for k in snapshot.manifest(d)} == {"tokyo"}


@pytest.mark.parametrize("name, kind", [("../evil", tarfile.REGTYPE), ("/abs/x", tarfile.REGTYPE),
                                        ("tokyo/link", tarfile.SYMTYPE), ("events/x.json", tarfile.REGTYPE),
                                        ("top.json", tarfile.REGTYPE)])
def test_unpack_refuses_unsafe_members(tmp_path, name, kind):
    raw = io.BytesIO()
    with tarfile.open(fileobj=raw, mode="w") as tar:
        info = tarfile.TarInfo(name)
        info.type = kind
        if kind == tarfile.SYMTYPE:
            info.linkname = "/etc/passwd"
            tar.addfile(info)
        else:
            info.size = 2
            tar.addfile(info, io.BytesIO(b"{}"))
    blob = zstandard.ZstdCompressor().compress(raw.getvalue())
    path = tmp_path / "bad.tar.zst"
    path.write_bytes(blob)
    p = {"asset": "bad", "size": len(blob), "sha256": snapshot.sha256_file(path), "groups": {}}
    with pytest.raises(snapshot.SnapshotError, match="unexpected member"):
        snapshot.unpack(path, tmp_path / "out", p)
    assert not (tmp_path / "evil").exists()


def g(**prefs):
    return {f"{p}/used_condo": {"files": 1, "records": n, "sha256": "x"} for p, n in prefs.items()}


def test_check_the_gate():
    assert snapshot.check(g(tokyo=1000, osaka=500), g(tokyo=900, osaka=500), {"tokyo", "osaka"}) == []
    assert snapshot.check({}, g(tokyo=10), {"tokyo"}) == []                     # the first snapshot: presence only
    assert snapshot.check(g(tokyo=1000), g(tokyo=10), {"tokyo", "osaka"}) == [
        "osaka: no records", "tokyo: 1000 -> 10 records"]
    assert snapshot.check(g(tokyo=1000), g(tokyo=600), {"tokyo"}) == []          # -40%: allowed
    assert snapshot.check(g(tokyo=5000, tottori=150), g(tokyo=5000, tottori=50), {"tokyo", "tottori"}) == []


def test_pointer_round_trip(tmp_path):
    p = {"tag": "t", "asset": "a", "size": 1, "sha256": "s", "groups": {"b": {}, "a": {}}}
    snapshot.write_pointer(tmp_path, p)
    assert snapshot.load_pointer(tmp_path) == p
    assert json.loads((tmp_path / "snapshot.json").read_text()) == p
    assert snapshot.load_pointer(tmp_path / "none") is None


def test_the_command_packs_skips_unchanged_gates_and_unpacks(tmp_path, monkeypatch, capsys):
    from suumo import cli

    d = data_dir(tmp_path)
    scope = tmp_path / "scope.toml"
    scope.write_text('[[scope]]\npref = "tokyo"\n')

    def main(*args):
        monkeypatch.setattr("sys.argv", ["suumo", "--data", str(d), "--scope", str(scope), "snapshot", *args])
        cli.main()

    main("pack", str(tmp_path / "data-1.tar.zst"), "--tag", "suumo-data")
    p = snapshot.load_pointer(d)
    assert (p["tag"], p["asset"]) == ("suumo-data", "data-1.tar.zst") and p["groups"] == snapshot.manifest(d)
    with pytest.raises(SystemExit) as e:                       # nothing changed: no new asset
        main("pack", str(tmp_path / "data-2.tar.zst"), "--tag", "suumo-data")
    assert e.value.code == cli.SNAPSHOT_UNCHANGED and not (tmp_path / "data-2.tar.zst").exists()
    write(d, [rec(i) for i in range(300)])                     # 300 records, then a half-empty export
    main("pack", str(tmp_path / "data-3.tar.zst"), "--tag", "suumo-data")
    write(d, [rec(i) for i in range(10)])
    with pytest.raises(SystemExit, match="gate refuses"):
        main("pack", str(tmp_path / "data-4.tar.zst"), "--tag", "suumo-data")
    assert snapshot.load_pointer(d)["asset"] == "data-3.tar.zst"
    main("pack", str(tmp_path / "data-4.tar.zst"), "--tag", "suumo-data", "--force")
    assert "gate: tokyo: 300 -> 10 records" in capsys.readouterr().out
    import shutil
    shutil.rmtree(d / "tokyo")
    main("unpack", str(tmp_path / "data-4.tar.zst"))
    assert snapshot.manifest(d) == snapshot.load_pointer(d)["groups"]
