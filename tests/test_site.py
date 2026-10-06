"""The static site: what the build writes, and the search rules in site/filter.js (run in Node)."""
import json
import shutil
from datetime import date

import pytest

from suumo.site import build
from tests.helpers import FIXTURES, NODE, QUIET, rec, site_queries

needs_node = pytest.mark.skipif(not NODE, reason="node not installed")
LAND = {"layout": None, "floor_m2": None, "built": None}


def ids(tmp_path, recs, *queries, **kw):
    return [r["ids"] for r in site_queries(tmp_path, recs, [{"query": q} for q in queries], **kw)]


def test_build_writes_index_listings_and_assets(tmp_path):
    data = tmp_path / "data"
    shutil.copytree(FIXTURES / "data", data)
    geo = tmp_path / "towns.json"
    geo.write_text(json.dumps({"東京都狛江市岩戸北３": [35.63, 139.58]}, ensure_ascii=False))
    out = tmp_path / "site"
    index = build(data, geo, out, today=date(2026, 10, 6), log=QUIET)
    assert {"index.html", "app.js", "filter.js", "style.css", ".nojekyll"} <= {p.name for p in out.iterdir()}
    assert len(index["rows"]) == 46
    assert {t: (lat, lng) for t, lat, lng in index["towns"]}["東京都狛江市岩戸北３"] == (35.63, 139.58)
    row = dict(zip(index["columns"], next(r for r in index["rows"] if r[0] == "20205670"), strict=True))
    assert row["image"] == "030/N010000/670/0004.jpg" and row["price"] == 12_000_000
    detail = json.loads((out / "data/l/used_condo/20205670.json").read_text())
    assert detail["url"].startswith("https://suumo.jp/") and detail["land_rights"] == "定期借地権"
    dropped = json.loads((out / "data/l/used_condo/20635014.json").read_text())
    assert dropped["history"][0][1] > dropped["history"][0][2]


@needs_node
def test_price_uses_the_range(tmp_path):
    recs = [rec(1, price=30_000_000), rec(2, price=45_000_000, price_max=60_000_000), rec(3, price=None),
            rec(4, price=80_000_000)]
    assert ids(tmp_path, recs, {"priceMax": 50_000_000, "sort": "price_asc"}, {"priceMin": 55_000_000}, {}) == [
        ["1", "2"],             # 2 has a plot from 4,500万
        ["4", "2"],             # 2 has a plot up to 6,000万
        ["4", "3", "2", "1"],   # no price filter: 価格未定 included
    ]


@needs_node
def test_building_conditions_and_land(tmp_path):
    recs = [rec(1, layout="1LDK", floor_m2=55.0), rec(2, layout="4LDK", floor_m2=80.0, built="1985-01"),
            rec(3, "new_house", layout="6LDK", floor_m2=None, building_m2=95.0, land_m2=110.0, built=None),
            rec(4, "land", land_m2=150.0, **LAND)]
    assert ids(tmp_path, recs, {"rooms": [4]}, {"rooms": [1, 4], "types": ["land"]}, {"sizeMin": 70},
               {"sizeMin": 70, "types": ["land", "new_house"]}, {"landMin": 120}, {"ageMax": 25}) == [
        ["3", "2"],
        ["4"],                  # building conditions don't exclude land chosen explicitly
        ["3", "2"],
        ["4", "3"],
        ["4"],
        ["3", "1"],             # 2005 -> 21 years; new counts as 0
    ]


@needs_node
def test_stations_and_walk(tmp_path):
    recs = [rec(1, stations=[{"line": "小田急線", "name": "狛江", "walk": 12},
                             {"line": "京王線", "name": "柴崎", "walk": 6}]),
            rec(2, stations=[{"line": "小田急線（新宿～相模大野）", "name": "狛江", "walk": 3}]),
            rec(3, stations=[{"line": "小田急バス", "name": "狛江第5小学校", "walk": 1, "bus_stop": True}]),
            rec(4, stations=[{"line": "小田急線", "name": "喜多見", "bus": 8, "walk": 2}]),
            rec(5, stations=[{"line": "ＪＲ南武線", "name": "狛江", "walk": 9}])]
    assert ids(tmp_path, recs, {"stations": ["狛江"]}, {"walk": 7}, {"stations": ["狛江"], "walk": 7},
               {"walk": 10, "sort": "walk_asc"}) == [
        ["5", "2", "1"],        # the same station on two lines is one choice
        ["2", "1"],             # bus access doesn't count as walking
        ["2"],                  # 1 is 12 min from 狛江
        ["2", "1", "5"],
    ]


@needs_node
def test_tags_rights_condition_quake_new_and_drops(tmp_path):
    events = {"20261015T040000": [{"kind": "new", "type": "used_condo", "id": "2", "payload": {}},
                                  {"kind": "price_changed", "type": "used_condo", "id": "2",
                                   "payload": {"price": 45_000_000, "old_price": 50_000_000}},
                                  {"kind": "price_changed", "type": "used_condo", "id": "3",
                                   "payload": {"price": 52_000_000, "old_price": 50_000_000}}]}
    recs = [rec(1, features=["ペット相談", "角住戸"], land_rights="所有権", built="1981-05"),
            rec(2, features=["ペット相談"], land_rights="定期借地権", built="1981-06"), rec(3),
            rec(4, "land", land_m2=100.0, build_condition=True, **LAND), rec(5, "land", land_m2=100.0, **LAND)]
    assert ids(tmp_path, recs, {"features": ["ペット相談"]}, {"features": ["ペット相談", "角住戸"]},
               {"freehold": True, "types": ["used_condo"]}, {"newOnly": True}, {"dropsOnly": True},
               {"post1981": True, "types": ["used_condo"]}, {"noCondition": True, "types": ["land"]},
               {"types": ["used_condo"]}, events=events) == [
        ["2", "1"], ["1"],
        ["3", "1"],             # unknown rights are kept
        ["2"],                  # 新着 comes from events, not first_seen
        ["2"],                  # 3 went up
        ["2", "3"], ["5"],     # (sorted 新着順: 2 is new)
        ["2", "3", "1"],        # 新着順: new first, then first seen, then newest id
    ]


@needs_node
def test_duplicates_fold_to_the_cheapest_matching_listing(tmp_path):
    recs = [rec(1, dup_key="aa", price=50_000_000), rec(2, dup_key="aa", price=49_800_000), rec(3, dup_key="bb")]
    every, pricey = site_queries(tmp_path, recs, [{"query": {}}, {"query": {"priceMin": 49_900_000}}])
    assert every == {"ids": ["3", "2"], "others": [[], ["1"]], "count": 2}
    assert pricey["ids"] == ["3", "1"]


@needs_node
@pytest.mark.parametrize("sort,expected", [
    ("new", ["3", "2", "1"]), ("price_asc", ["2", "1", "3"]), ("price_desc", ["3", "1", "2"]),
    ("size_desc", ["1", "3", "2"]), ("walk_asc", ["3", "1", "2"]), ("age_asc", ["2", "3", "1"]),
    ("unit_asc", ["1", "2", "3"]),
])
def test_sorts(tmp_path, sort, expected):
    walk = lambda w: [{"line": "小田急線", "name": "狛江", "walk": w}]  # noqa: E731
    recs = [rec(1, price=50_000_000, floor_m2=90.0, built="1990-01", stations=walk(8)),
            rec(2, price=30_000_000, floor_m2=50.0, built="2020-01", first_seen="2026-10-02", stations=walk(15)),
            rec(3, price=90_000_000, floor_m2=60.0, built="2010-01", first_seen="2026-10-02", stations=walk(2))]
    assert ids(tmp_path, recs, {"sort": sort}) == [expected]


@needs_node
def test_choice_counts(tmp_path):
    recs = [rec(1), rec(2, area_code="13208"), rec(3, "used_house", building_m2=90.0),
            rec(4, "land", land_m2=90.0, **LAND)]
    types, areas = site_queries(tmp_path, recs, [
        {"query": {"types": ["used_condo"], "rooms": [3]}, "facet": "types"},
        {"query": {"types": ["used_condo"], "areas": ["13219"]}, "facet": "areas"}],
        areas={"13219": "狛江市", "13208": "調布市"})
    assert types == {"used_condo": 2, "used_house": 1, "land": 1}   # choosing 土地 would add it
    assert areas == {"13219": 1, "13208": 1}


@needs_node
def test_text_search(tmp_path):
    recs = [rec(1, name="パークビュー狛江 ２階"), rec(2, name="パークビュー狛江"), rec(3, name="リバーサイド喜多見",
            stations=[{"line": "小田急線", "name": "喜多見", "walk": 4}])]
    assert ids(tmp_path, recs, {"text": "パークビュー"}, {"text": "ﾊﾟｰｸﾋﾞｭｰ 2階"}, {"text": "喜多見駅"}) == [
        ["2", "1"], ["1"], ["3"]]      # half-width kana normalized; every word must match; stations searchable
