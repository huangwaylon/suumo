"""Export of page records: the main files' page fields and the page files (no network)."""
import json

from suumo.export import export, merge
from suumo.pipeline import store_page
from suumo.scope import Target
from tests.test_records import _db, condo


def test_export_writes_page_files_and_page_fields_deterministically(tmp_path):
    db = _db(tmp_path, [1, 2, 3], queue=False)
    for lid in (1, 2):
        store_page(db, "used_condo", str(lid), condo(lid), "2026-11-20T06:00:00+09:00")
    db.x("UPDATE listings SET status='removed', removed_at='2026-11-19T05:00:00+09:00' WHERE id='2'")
    db.commit()
    data = tmp_path / "data"
    target = [Target("tokyo", "used_condo", None)]
    export(db, target, data)
    main = [json.loads(x) for x in (data / "tokyo/used_condo/13219.jsonl").read_text().splitlines()]
    assert [r["id"] for r in main] == ["1", "3"]
    one = main[0]
    assert one["page_date"] == "2026-11-20" and one["photos"] == len(condo(1)["img"]["l"])
    assert 35 < one["lat"] < 36 and 139 < one["lng"] < 140      # 狛江市, inside 東京都's box
    assert "page_date" not in main[1] and "lat" not in main[1]  # no record yet
    pages = [json.loads(x) for x in (data / "tokyo/pages/used_condo/13219.jsonl").read_text().splitlines()]
    assert [p["id"] for p in pages] == ["1"] and set(pages[0]) <= {"id", "img", "texts", "env", "agent", "dates"}
    assert pages[0]["img"] == condo(1)["img"] and "agent" in pages[0]
    removed = (data / "tokyo/pages/removed/used_condo.jsonl").read_text()
    assert json.loads(removed)["id"] == "2"   # a saved ended listing keeps its page
    before = {p: p.read_bytes() for p in data.rglob("*.jsonl")}
    export(db, target, data)                  # unchanged state: identical files
    assert {p: p.read_bytes() for p in data.rglob("*.jsonl")} == before
    db.x("UPDATE listings SET list_json=json_set(list_json, '$.price', 2) WHERE id='1'")
    db.commit()
    export(db, target, data)                  # a price change rewrites the main file, not the page file
    pages_file, main_file = data / "tokyo/pages/used_condo/13219.jsonl", data / "tokyo/used_condo/13219.jsonl"
    assert pages_file.read_bytes() == before[pages_file] and main_file.read_bytes() != before[main_file]


def test_coordinates_outside_the_prefecture_are_not_exported(tmp_path):
    db = _db(tmp_path, [1], queue=False)
    r = condo(1)
    r["ll"] = {"lat": 43.06, "lng": 141.35, "kind": "map"}   # Sapporo, for a Tokyo listing
    store_page(db, "used_condo", "1", r, "2026-11-20T06:00:00+09:00")
    out = merge(db.listing("used_condo", "1"))
    assert "lat" not in out and "lng" not in out and out["page_date"] == "2026-11-20"
