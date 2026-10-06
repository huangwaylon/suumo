"""The static site build, and parity between site/filter.js (run in Node) and the catalog's rules."""
import json
import random
import shutil
import subprocess
from dataclasses import replace
from datetime import date
from pathlib import Path

import pytest

from suumo.catalog import Query, load
from suumo.site import build
from tests.test_catalog import rec, write

FIXTURE = Path(__file__).parent / "fixtures" / "data"
FILTER_JS = Path(__file__).parent.parent / "site" / "filter.js"
TODAY = date(2026, 10, 20)
NODE = shutil.which("node")

RUNNER = """
const Filter = require(process.argv[1]);
const fs = require("fs");
const db = Filter.load(JSON.parse(fs.readFileSync(process.argv[2], "utf8")));
const queries = JSON.parse(fs.readFileSync(process.argv[3], "utf8"));
const out = queries.map((q) => {
  const hits = Filter.search(db, Object.assign(Filter.emptyQuery(), q));
  return {keys: hits.map((h) => h.item.key), others: hits.map((h) => h.others.length),
          count: Filter.count(db, Object.assign(Filter.emptyQuery(), q))};
});
process.stdout.write(JSON.stringify(out));
"""


def js_query(q: Query):
    return {"types": list(q.types), "areas": list(q.areas), "stations": list(q.stations), "walk": q.walk_max,
            "priceMin": q.price_min, "priceMax": q.price_max, "rooms": list(q.rooms), "sizeMin": q.size_min,
            "landMin": q.land_min, "ageMax": q.age_max, "post1981": q.post_1981, "features": list(q.features),
            "freehold": q.freehold_only, "noCondition": q.no_condition, "newOnly": q.new_only,
            "dropsOnly": q.drops_only, "sort": q.sort}


def run_js(index_path, queries, tmp_path):
    qfile = tmp_path / "queries.json"
    qfile.write_text(json.dumps([js_query(q) for q in queries], ensure_ascii=False))
    out = subprocess.run([NODE, "-e", RUNNER, str(FILTER_JS), str(index_path), str(qfile)],
                         capture_output=True, text=True, check=True)
    return json.loads(out.stdout)


def test_build_writes_index_listings_and_assets(tmp_path):
    data = tmp_path / "data"
    shutil.copytree(FIXTURE, data)
    geo = tmp_path / "towns.json"
    geo.write_text(json.dumps({"東京都狛江市岩戸北３": [35.63, 139.58]}, ensure_ascii=False))
    out = tmp_path / "site"
    index = build(data, geo, out, today=date(2026, 10, 6), log=lambda *_: None)
    assert {"index.html", "app.js", "filter.js", "style.css", ".nojekyll"} <= {p.name for p in out.iterdir()}
    snap = load(data, date(2026, 10, 6))
    assert len(index["rows"]) == len(snap.items)
    town = dict((t, (lat, lng)) for t, lat, lng in index["towns"])
    assert town["東京都狛江市岩戸北３"] == (35.63, 139.58)
    row = dict(zip(index["columns"], next(r for r in index["rows"] if r[0] == "20205670"), strict=True))
    assert row["image"] == "030/N010000/670/20205670_0004.jpg".replace("20205670_", "") and row["price"] == 12_000_000
    detail = json.loads((out / "data/l/used_condo/20205670.json").read_text())
    assert detail["url"].startswith("https://suumo.jp/") and detail["land_rights"] == "定期借地権"
    dropped = json.loads((out / "data/l/used_condo/20635014.json").read_text())
    assert dropped["history"] and dropped["history"][0][1] > dropped["history"][0][2]


@pytest.mark.skipif(not NODE, reason="node not installed")
def test_site_filter_matches_the_catalog(tmp_path):
    rnd = random.Random(11)
    types = ["used_condo", "new_house", "used_house", "land", "new_condo"]
    stations = [("小田急線", "狛江"), ("小田急線", "喜多見"), ("京王線", "柴崎"), ("ＪＲ南武線", "登戸"),
                ("小田急線", "登戸")]
    tags = ["ペット相談", "角住戸", "南向き"]
    recs, events = [], []
    for n in range(400):
        t = rnd.choice(types)
        sts = [{"line": ln, "name": nm, **({"bus": 5} if rnd.random() < 0.1 else {}), "walk": rnd.randint(1, 25)}
               for ln, nm in rnd.sample(stations, rnd.randint(0, 3))]
        recs.append(rec(n + 1, t, area_code=rnd.choice(["13219", "13208"]),
                        price=rnd.choice([None, rnd.randint(20, 120) * 1_000_000]),
                        price_max=rnd.choice([None, None, 130_000_000]),
                        layout=None if t == "land" else rnd.choice(["1LDK", "2LDK", "3LDK+S", "4LDK", "5DK", None]),
                        floor_m2=rnd.choice([None, 45.0, 70.5, 95.0]), land_m2=rnd.choice([None, 80.0, 150.0]),
                        built=rnd.choice([None, "1975-03", "1981-06", "2001-01", "2020-12"]), stations=sts,
                        features=rnd.sample(tags, rnd.randint(0, 2)), has_detail=rnd.random() < 0.7,
                        land_rights=rnd.choice([None, "所有権", "定期借地権"]), build_condition=rnd.random() < 0.1,
                        dup_key=rnd.choice([None, None, "a", "b", "c"]),
                        first_seen=rnd.choice(["2026-10-01", "2026-10-05"])))
        if rnd.random() < 0.2:
            events.append({"kind": "new", "type": t, "id": str(n + 1), "payload": {}})
        if rnd.random() < 0.2:
            p = recs[-1].get("price") or 50_000_000
            events.append({"kind": "price_changed", "type": t, "id": str(n + 1),
                           "payload": {"price": p, "old_price": p + rnd.choice([-1, 1]) * 1_000_000}})
    by_type = {}
    for r in recs:
        by_type.setdefault(r["type"], []).append(r)
    data = tmp_path / "data"
    write(data, areas={"13219": "狛江市", "13208": "調布市"}, events={"20261015T040000": events}, **by_type)
    snap = load(data, TODAY)
    out = tmp_path / "site"
    build(data, tmp_path / "none.json", out, today=TODAY, log=lambda *_: None)
    queries = [Query()] + [Query(sort=s) for s in ("price_asc", "price_desc", "size_desc", "walk_asc", "age_asc",
                                                   "unit_asc")]
    for _ in range(300):
        q = Query().set(
            types=rnd.sample(types, rnd.randint(0, 2)), areas=rnd.sample(["13219", "13208"], rnd.randint(0, 1)),
            stations=rnd.sample(["狛江", "喜多見", "柴崎", "登戸"], rnd.randint(0, 2)),
            walk_max=rnd.choice([None, 5, 10, 15]), price_max=rnd.choice([None, 50_000_000, 80_000_000]),
            price_min=rnd.choice([None, 40_000_000]), rooms=rnd.sample([1, 2, 3, 4], rnd.randint(0, 2)),
            size_min=rnd.choice([None, 60]), land_min=rnd.choice([None, 100]), age_max=rnd.choice([None, 20, 45]),
            post_1981=rnd.random() < 0.2, features=rnd.sample(tags, rnd.randint(0, 1)),
            freehold_only=rnd.random() < 0.2, no_condition=rnd.random() < 0.2, new_only=rnd.random() < 0.15,
            drops_only=rnd.random() < 0.15,
            sort=rnd.choice(["new", "price_asc", "price_desc", "size_desc", "walk_asc", "age_asc", "unit_asc"]))
        queries.append(q)
    js = run_js(out / "data" / "index.json", queries, tmp_path)
    for q, got in zip(queries, js, strict=True):
        hits = snap.search(q)
        assert got["keys"] == [h.key for h in hits], q
        assert got["others"] == [len(h.others) for h in hits], q
        assert got["count"] == snap.count(q), q


@pytest.mark.skipif(not NODE, reason="node not installed")
def test_text_search_on_the_site(tmp_path):
    data = tmp_path / "data"
    shutil.copytree(FIXTURE, data)
    out = tmp_path / "site"
    build(data, tmp_path / "none.json", out, today=date(2026, 10, 6), log=lambda *_: None)
    runner = RUNNER
    qfile = tmp_path / "q.json"
    qfile.write_text(json.dumps([{"text": "パークビュー"}, {"text": "狛江駅"}, {"text": "ﾊﾟｰｸﾋﾞｭｰ 2階"}],
                                ensure_ascii=False))
    res = json.loads(subprocess.run([NODE, "-e", runner, str(FILTER_JS), str(out / "data/index.json"), str(qfile)],
                                    capture_output=True, text=True, check=True).stdout)
    snap = load(data, date(2026, 10, 6))
    park = {i.key for i in snap.items if "パークビュー" in (i.rec.get("name") or "")}
    assert res[0]["keys"] and set(res[0]["keys"]) <= park
    assert res[1]["count"] >= snap.count(replace(Query(), stations=("狛江",))) > 0   # station names are searchable
    assert res[2]["keys"] and set(res[2]["keys"]) < set(res[0]["keys"])          # half-width kana, all words
