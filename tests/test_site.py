"""The static site: what the build writes, and the search rules in site/filter.js (run in Node)."""
import json
import re
import shutil
import subprocess
from datetime import date

import pytest

from suumo.site import _name, build
from tests.helpers import FILTER_JS, FIXTURES, NODE, QUIET, rec, site_queries

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
    assert {"index.html", "app.js", "filter.js", "i18n.js", "style.css", ".nojekyll"} <= {p.name for p in out.iterdir()}
    cols = index["columns"]
    assert len(cols["id"]) < 46                               # 46 listings, duplicates folded
    assert index["prefs"] == ["東京都"] and index["areas"] == [["13219", "狛江市", 0]]
    assert {t: (lat, lng) for t, lat, lng in index["towns"]}["東京都狛江市岩戸北３"] == (35.63, 139.58)
    ids_ = [sum(cols["id"][:n + 1]) for n in range(len(cols["id"]))]   # stored as deltas
    row = ids_.index(20205670)
    assert cols["image"][row] == "030/N010000/670/0004.jpg" and cols["price"][row] == 12_000_000
    detail = json.loads((out / "data/l/used_condo/20205670.json").read_text())
    assert detail["url"].startswith("https://suumo.jp/") and detail["land_rights"] == "定期借地権"


@pytest.mark.parametrize("name,shown", [
    ("パークビュー狛江", "パークビュー狛江"), ("『MAC目黒コート』TVモニター付…", "MAC目黒コート"),
    ("柿の木坂２（都立大学駅） 4億8000万円", None), ("【HEBEL HAUS】●太陽光発電", None),
    ("リバーサイド喜多見…", "リバーサイド喜多見"),
])
def test_ad_copy_is_not_shown_as_a_name(name, shown):
    assert _name({"name": name}) == shown


@needs_node
def test_duplicates_are_one_property(tmp_path):
    recs = [rec(1, dup_key="aa", price=50_000_000), rec(2, dup_key="aa", price=50_000_000, has_detail=True),
            rec(3, dup_key="bb")]
    [every] = site_queries(tmp_path, recs, [{"query": {}}])
    assert every == {"ids": ["3", "2"], "others": [0, 1], "count": 2}   # the one with its page fetched represents


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
    recs = [rec(1, layout="2DK", floor_m2=55.0), rec(2, layout="2LDK", floor_m2=80.0, built="1985-01"),
            rec(3, "new_house", layout="4LDK", floor_m2=None, building_m2=95.0, land_m2=110.0, built=None),
            rec(4, "land", land_m2=150.0, **LAND)]
    assert ids(tmp_path, recs, {"plan": 8}, {"plan": 8, "types": ["land"]}, {"sizeMin": 70},
               {"sizeMin": 70, "types": ["land", "new_house"]}, {"landMin": 120}, {"ageMax": 25}) == [
        ["3", "2"],             # 2LDK以上: not the 2DK
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
        ["2", "3"], ["5"],      # (sorted 新着順: 2 is new)
        ["2", "3", "1"],        # 新着順: new first, then first seen, then newest id
    ]


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
def test_choice_counts_in_one_pass(tmp_path):
    recs = [rec(1, layout="3LDK"), rec(2, area_code="13208", layout="2DK"), rec(3, "used_house", building_m2=90.0),
            rec(4, "land", land_m2=90.0, **LAND)]
    choices = {"plan": [7, 11], "priceMax": [40_000_000, 60_000_000]}
    [f] = site_queries(tmp_path, recs, [{"query": {"types": ["used_condo"], "plan": 11}, "choices": choices}],
                       areas={"13219": "狛江市", "13208": "調布市"})
    assert f["total"] == 1
    assert f["types"] == {"used_condo": 1, "used_house": 1, "land": 1}   # choosing 土地 would add it
    assert f["areas"] == {"13219": 1}                                    # 2 (調布) is a 2DK
    assert f["plan"] == {"7": 2, "11": 1}                                # other plan choices for the condos
    assert f["priceMax"] == {"40000000": 0, "60000000": 1}


@needs_node
def test_text_search(tmp_path):
    recs = [rec(1, name="パークビュー狛江 ２階"), rec(2, name="パークビュー狛江"),
            rec(3, name="リバーサイド喜多見", stations=[{"line": "小田急線", "name": "喜多見", "walk": 4}])]
    assert ids(tmp_path, recs, {"text": "パークビュー"}, {"text": "ﾊﾟｰｸﾋﾞｭｰ 2階"}, {"text": "ぱーくびゅー"},
               {"text": "喜多見駅"}) == [
        ["2", "1"], ["1"], ["2", "1"], ["3"]]   # half-width kana, hiragana, every word must match, stations


@needs_node
def test_every_string_has_an_english_translation():
    script = """
    global.window = {}; require(process.argv[1]);
    const keys = (o, p = "") => Object.entries(o).flatMap(([k, v]) =>
      v && typeof v === "object" && !Array.isArray(v) ? keys(v, p + k + ".") : [p + k]);
    const ja = new Set(keys(window.I18N.ja)), en = new Set(keys(window.I18N.en));
    const missing = [...ja].filter((k) => !en.has(k) && !k.startsWith("values."));
    process.stdout.write(JSON.stringify(missing));
    """
    i18n = FILTER_JS.parent / "i18n.js"
    out = subprocess.run([NODE, "-e", script, str(i18n)], capture_output=True, text=True, check=True)
    assert json.loads(out.stdout) == []


def test_every_string_the_page_uses_exists():
    app = (FILTER_JS.parent / "app.js").read_text()
    i18n = (FILTER_JS.parent / "i18n.js").read_text()
    ja = i18n[i18n.index("ja: {"):i18n.index("en: {")]
    used = set(re.findall(r'\bt\("(\w+)', app)) | set(re.findall(r'data-t(?:-label|-placeholder)?="(\w+)"',
                                                               (FILTER_JS.parent / "index.html").read_text()))
    assert {k for k in used if not re.search(rf"\b{k}:", ja)} == set()


@needs_node
def test_stations_by_kana_and_english(tmp_path):
    names = {"二子玉川": ["ふたこたまがわ", "Futako-Tamagawa"], "自由ケ丘": ["じゆうがおか", "Jiyūgaoka"],
             "玉川学園前": ["たまがわがくえんまえ", "Tamagawagakuen-mae"]}
    recs = [rec(1, stations=[{"line": "東急田園都市線", "name": "二子玉川", "walk": 5}]),
            rec(2, stations=[{"line": "東急東横線", "name": "自由が丘", "walk": 7}]),
            rec(3, stations=[{"line": "小田急線", "name": "玉川学園前", "walk": 3}])]
    out = site_queries(tmp_path, recs, [{"station": "futakotamagawa"}, {"station": "ジユウ"}, {"station": "jiyugaoka"},
                                        {"station": "玉川"}, {"query": {"text": "ふたこたまがわ"}},
                                        {"query": {"text": "Jiyugaoka"}}], stations=names)
    assert out[:4] == [["二子玉川"], ["自由が丘"], ["自由が丘"], ["玉川学園前", "二子玉川"]]  # starts with it first
    assert out[4]["ids"] == ["1"] and out[5]["ids"] == ["2"]   # the text search finds readings too
