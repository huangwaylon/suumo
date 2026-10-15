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
    (data / "fx.json").write_text('{"date": "2026-10-13", "jpy_per_usd": 158.12}')
    manifest = build(data, geo, out, today=date(2026, 10, 6), log=QUIET)
    assets = {"index.html", "app.js", "filter.js", "i18n.js", "style.css", "sw.js", ".nojekyll"}
    assert assets <= {p.name for p in out.iterdir()}
    assert re.search(r'const VERSION = "[0-9a-f]{12}";', (out / "sw.js").read_text())  # stamped: a new page, new cache
    assert json.loads((out / "data/index.json").read_text()) == manifest
    index = json.loads((out / "data/p/tokyo.json").read_text())   # one index per prefecture
    [[slug, name, n, share]] = manifest["prefs"]
    assert (slug, name, n) == ("tokyo", "東京都", len(index["columns"]["id"])) and manifest["saved"] == {}
    assert 0 < share <= 1   # the share with their listing page fetched
    assert manifest["fx"] == {"date": "2026-10-13", "jpy_per_usd": 158.12}   # the page's USD prices
    cols = index["columns"]
    assert len(cols["id"]) < 46                               # 46 listings, duplicates folded
    assert index["areas"] == [["13219", "狛江市", "東京都", "shi"]]
    assert {t: (lat, lng) for t, lat, lng in index["towns"]}["東京都狛江市岩戸北３"] == (35.63, 139.58)
    ids_ = [sum(cols["id"][:n + 1]) for n in range(len(cols["id"]))]   # stored as deltas
    row = ids_.index(20205670)
    assert cols["image"][row] == 4 and cols["price"][row] == 12_000_000   # the usual photo path: its number
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
def test_age_counts_months(tmp_path):
    recs = [rec(1, built="2019-10"), rec(2, built="2020-06"), rec(3, "new_house", built="2027-03"),
            rec(4, built="2025-12"), rec(5, built=None)]
    assert ids(tmp_path, recs, {"sort": "age_asc"}, {"ageMax": 0}, {"ageMax": 6}) == [
        ["3", "4", "2", "1", "5"],   # new homes are 0; then by month built, newest first; unknown last
        ["4", "3"],                  # 10 months old is 0 years (新着順: newest id first)
        ["4", "3", "2"],             # 2 is 6 years 4 months old, 1 is 7 years
    ]


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


def test_every_element_the_page_looks_up_exists():
    app = (FILTER_JS.parent / "app.js").read_text()
    html = (FILTER_JS.parent / "index.html").read_text() + app   # (some are in the templates app.js writes)
    assert {i for i in re.findall(r'\$\("([\w-]+)"\)', app) if f'id="{i}"' not in html} == set()


def test_every_record_field_the_page_reads_is_exported():
    from suumo.export import FIELD_ORDER
    app = (FILTER_JS.parent / "app.js").read_text()
    extras = {"others", "history", "new_date", "bus_stop"}   # site.listing_record adds these
    not_records = {"json", "left", "bottom"}                 # r is also a fetch response and a box in app.js
    assert set(re.findall(r"\br\.([a-z_][a-z0-9_]*)", app)) - not_records - extras - set(FIELD_ORDER) == set()


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


@needs_node
def test_bus_and_car_access_keep_their_minutes_and_km(tmp_path):
    recs = [rec(1, stations=[{"line": "小田急線", "name": "喜多見", "bus": 8, "walk": 2},
                             {"line": "ＪＲ常磐線", "name": "牛久", "car_km": 2.4}])]
    assert loaded(tmp_path, recs, "stations") == [[["喜多見", None, 8, None], ["牛久", None, None, 2.4]]]  # bus; car


@needs_node
def test_photo_paths_are_shortened_and_restored(tmp_path):
    url = "https://img01.suumo.com/jj/resizeImage?src=gazo%2Fbukken%2F030%2F{}%2Fimg%2F{}%2F{}%2F{}_{}&w=192&h=144"
    recs = [rec(20205670, image=url.format("N010000", "670", 20205670, 20205670, "0004.jpg")),
            rec(20205671, image=url.format("N001000", "71", 20205671, 20205671, "00015.jpg")),
            rec(20205672, image="https://img01.suumo.com/jj/resizeImage?src=gazo%2Fbukken%2Fsystem%2Fimg%2FNo.jpg"),
            rec(20205673)]
    # the usual path is a number (items keep it; the page expands it with Filter.photo), others a shortened path
    assert loaded(tmp_path, recs, "image") == [4, "030/N001000/71/00015.jpg", "gazo/bukken/system/img/No.jpg", None]
    script = ('const F = require(process.argv[1]); process.stdout.write(JSON.stringify([[0, "20205670"],'
              '[4, "20205670"], ["030/N001000/71/00015.jpg", "20205671"], ["gazo/bukken/system/img/No.jpg", "1"],'
              '["https://x.jp/a.jpg", "1"]].map(([v, id]) => F.photo(v, id))))')
    out = subprocess.run([NODE, "-e", script, str(FILTER_JS)], capture_output=True, text=True, check=True)
    assert json.loads(out.stdout) == [   # SUUMO's path again (photo 0 is a photo)
        "gazo/bukken/030/N010000/img/670/20205670/20205670_0000.jpg",
        "gazo/bukken/030/N010000/img/670/20205670/20205670_0004.jpg",
        "gazo/bukken/030/N001000/img/71/20205671/20205671_00015.jpg", "gazo/bukken/system/img/No.jpg",
        "https://x.jp/a.jpg"]


def loaded(tmp_path, recs, field):
    """One field of every item of the built Tokyo index, as filter.js loads it."""
    script = """
    const Filter = require(process.argv[1]);
    const db = Filter.load(JSON.parse(require("fs").readFileSync(process.argv[2], "utf8")));
    process.stdout.write(JSON.stringify(db.items.map((it) => it[process.argv[3]] ?? null)));
    """
    site_queries(tmp_path, recs, [])
    out = subprocess.run([NODE, "-e", script, str(FILTER_JS), str(tmp_path / "site/data/p/tokyo.json"), field],
                         capture_output=True, text=True, check=True)
    return json.loads(out.stdout)


@needs_node
def test_saved_listings_stay_themselves_and_ended_ones_only_in_the_saved_list(tmp_path):
    recs = [rec(1, dup_key="aa"), rec(2, dup_key="aa", has_detail=True), rec(7, dup_key="aa"),
            rec(3, removed_at="2026-10-05"), rec(4)]
    out = site_queries(tmp_path, recs, [{"query": {}}, {"saved": True}],
                       saved={"used_condo:1", "used_condo:3"})
    assert out[0]["ids"] == ["4", "2", "1"]      # 1 is saved: not folded into 2 (7 is); 3 ended: not in search
    assert out[1] == ["1", "3"]                   # the saved list, the ended one included
    assert (tmp_path / "site/data/l/used_condo/3.json").exists()


@needs_node
def test_since_drops_and_reposts(tmp_path):
    events = {"20261014T090000": [{"kind": "new", "type": "used_condo", "id": "2", "payload": {}}],
              "20261015T100000": [{"kind": "new", "type": "used_condo", "id": "3", "payload": {}},
                                  {"kind": "new", "type": "used_condo", "id": "5", "payload": {}},
                                  {"kind": "price_changed", "type": "used_condo", "id": "1",
                                   "payload": {"price": 40_000_000, "old_price": 50_000_000}},
                                  {"kind": "price_changed", "type": "used_condo", "id": "4",
                                   "payload": {"price": 45_000_000, "old_price": 50_000_000}}]}
    recs = [rec(1, price=40_000_000), rec(2), rec(3), rec(4, price=45_000_000),
            rec(5, dup_key="x", has_detail=True), rec(6, dup_key="x")]   # 5: another agent's copy of 6
    queries = [{"since": 2026101409}, {"sort": "drop_desc", "dropsOnly": True}, {"newOnly": True}]
    out = site_queries(tmp_path, recs, [{"query": q} for q in queries], events=events)
    assert out[0]["ids"] == ["3", "4", "1"]   # new or cheaper after 10/14 09:00 (2 was seen then)
    assert out[1]["ids"] == ["1", "4"]        # -20% before -10%
    assert out[2]["ids"] == ["3", "2"]        # 5 re-posts a property already listed: not new


@pytest.mark.parametrize("code,name,group", [
    ("13104", "新宿区", "ku"), ("13219", "狛江市", "shi"), ("13305", "西多摩郡", "gun"),
    ("14103", "横浜市西区", "横浜市"), ("11101", "さいたま市西区", "さいたま市"), ("12320", "印旛郡", "gun"),
    ("08201", "水戸市", "shi"),
])
def test_area_groups(code, name, group):
    from suumo.site import _area_group
    assert _area_group(code, name) == group


def test_same_station_name_in_two_prefectures_is_two_stations(tmp_path):
    from tests.helpers import write
    data = tmp_path / "data"
    futako = {"line": "東急田園都市線", "name": "二子玉川", "walk": 9}
    write(data, [rec(1, stations=[{"line": "都営新宿線", "name": "小川町", "walk": 3}, futako])])
    write(data, [rec(2, area_code="11343", stations=[{"line": "東武東上線", "name": "小川町", "walk": 5}])],
          areas={"11343": "比企郡"}, pref="saitama")
    write(data, [rec(3, area_code="14131", stations=[futako])], areas={"14131": "川崎市川崎区"}, pref="kanagawa")
    (tmp_path / "rail.json").write_text(json.dumps({"lines": [], "stations": [   # where the railway data has them
        ["小川町", 35.6949, 139.7676, []], ["小川町", 36.0567, 139.2614, []], ["二子玉川", 35.6118, 139.6267, []]]}))
    manifest = build(data, tmp_path / "no-geo.json", tmp_path / "site", today=date(2026, 10, 6), log=QUIET,
                     rail_path=tmp_path / "rail.json")

    def stations(pref):
        return json.loads((tmp_path / f"site/data/p/{pref}.json").read_text())["stations"]
    assert set(stations("tokyo")) == {"小川町（東京）", "二子玉川"}
    assert stations("saitama") == ["小川町（埼玉）"]
    assert stations("kanagawa") == ["二子玉川"]          # one station near the border: one name
    assert [p[0] for p in manifest["prefs"]][0] == "tokyo"          # Tokyo first


def test_rail_sections_are_joined_into_paths_with_a_coarse_copy(tmp_path):
    from tests.helpers import write
    write(tmp_path / "data", [rec(1)])
    a, b = [[139.0, 35.0], [139.01, 35.0004], [139.02, 35.0]], [[139.03, 35.001], [139.025, 35.0002], [139.02, 35.0]]
    far = [[140.0, 36.0], [140.1, 36.1]]
    rail = {"attribution": "x", "operators": [["op", "Op", "#000", "op"]], "stations": [],
            "lines": [[0, "線", "Line", "#123456", [a, far, b]]]}   # a and b meet at 139.02 (b runs backwards)
    (tmp_path / "rail.json").write_text(json.dumps(rail))
    build(tmp_path / "data", tmp_path / "no-geo.json", tmp_path / "site", today=date(2026, 10, 6), log=QUIET,
          rail_path=tmp_path / "rail.json")
    [[op, name, en, colour, paths, coarse]] = json.loads((tmp_path / "site/data/rail.json").read_text())["lines"]
    assert (op, name, en, colour) == (0, "線", "Line", "#123456")
    assert len(paths) == len(coarse) == 2                       # a + b joined; far on its own
    joined = next(p for p in paths if len(p) > 2)
    assert {tuple(joined[0]), tuple(joined[-1])} == {(139.0, 35.0), (139.03, 35.001)} and [139.02, 35.0] in joined
    for p, c in zip(paths, coarse, strict=True):
        assert len(c) <= len(p) and (c[0], c[-1]) == (p[0], p[-1])   # coarser, same ends
    assert len(next(c for c in coarse if c[0] in (a[0], b[0]))) < len(joined)   # the small bends dropped
    assert json.loads((tmp_path / "rail.json").read_text()) == rail   # geo/rail.json itself is unchanged


def test_rail_stations_take_the_listings_spelling():
    from suumo.site import listing_spelling
    used = {name: {"tokyo"} for name in ("阿佐ケ谷", "江曽島", "藤沢（長野県）", "藤沢")}   # station_uses()
    rail = {"stations": [["阿佐ヶ谷", 35.7, 139.6, []], ["江曾島", 36.5, 139.9, []], ["藤沢", 35.3, 139.5, []],
                         ["新宿", 35.69, 139.7, []]]}
    assert [s[0] for s in listing_spelling(rail, used)["stations"]] == ["阿佐ケ谷", "江曽島", "藤沢", "新宿"]


@needs_node
def test_flags_from_the_listing_page(tmp_path):
    house = {"layout": "4LDK", "floor_m2": None, "building_m2": 95.0, "land_m2": 100.0}
    recs = [rec(1, direction="南東", floor=12, floors_above=12, handover="即引渡可", deal_type="仲介"),
            rec(2, direction="北", floor=1, floors_above=25, features=["東南向き", "即引渡し可"],
                deal_type="売主・仲介手数料0"),
            rec(3, floor=3, floors_above=5, features=["最上階・上階なし"], deal_type="販売提携(媒介)",
                handover="2027年3月予定"),
            rec(4, "used_house", parking={"status": "カースペース"}, deal_type="売主", **house),
            rec(5, "used_house", parking={"status": "無"}, features=["駐車２台可"], **house),
            rec(6, "used_house", parking={"status": "無"}, **house),
            rec(7, parking={"status": "空有"})]
    flags = ["south", "top", "upper", "nofee", "ready", "tower", "parking"]
    got = ids(tmp_path, recs, *[{f: True} for f in flags], {})
    assert dict(zip(flags, got, strict=False)) == {
        "south": ["2", "1"],                 # direction 南東, or a south tag
        "top": ["3", "1"],                   # floor == floors_above, or the tag
        "upper": ["3", "1"],                 # condos from the 2nd floor
        "nofee": ["4", "2"],                 # the seller itself ('仲介手数料0' is not a broker)
        "ready": ["2", "1"],
        "tower": ["2"],                      # 20 floors or more
        "parking": ["5", "4"],               # houses: a car space, or a parking tag; condos never
    }
    assert got[-1] == ["7", "6", "5", "4", "3", "2", "1"]


@needs_node
def test_no_commission_from_any_agent_of_the_property(tmp_path):
    recs = [rec(1, dup_key="a", has_detail=True, deal_type="仲介"), rec(2, dup_key="a", deal_type="仲介", price=1),
            rec(3, dup_key="a", deal_type="売主"), rec(4, deal_type="仲介")]
    [hits] = site_queries(tmp_path, recs, [{"query": {"nofee": True}}])
    assert hits["ids"] == ["1"]          # 3 sells it without commission: the property counts as no-commission
    others = json.loads((tmp_path / "site/data/l/used_condo/1.json").read_text())["others"]
    assert [o["deal_type"] for o in others] == ["売主", "仲介"]    # the agent without commission first


@needs_node
def test_monthly_fees(tmp_path):
    recs = [rec(1, mgmt_fee=12000, repair_fee=10000), rec(2, mgmt_fee=15000, repair_fee=12000, other_monthly=500),
            rec(3, mgmt_fee=9000), rec(4, "used_house", layout="4LDK", floor_m2=None, building_m2=95.0),
            rec(5, mgmt_fee=8000, repair_fee=6000)]
    assert ids(tmp_path, recs, {"feesMax": 25000}, {"sort": "fees_asc"}) == [
        ["5", "4", "1"],                     # condos up to ¥25,000 a month (3: repair fund unknown); houses pass
        ["5", "1", "2", "3", "4"],           # unknown and houses last
    ]
    assert loaded(tmp_path, recs, "fees") == [22000, 27500, None, 14000, None]   # yen (stored as ¥100)


@needs_node
def test_unit_and_size_sorts_keep_kinds_apart(tmp_path):
    house = {"layout": "4LDK", "floor_m2": None}
    recs = [rec(1, price=50_000_000, floor_m2=50.0), rec(2, "used_house", price=30_000_000, building_m2=100.0,
                                                        land_m2=200.0, **house),
            rec(3, "land", price=40_000_000, land_m2=400.0, **LAND), rec(4, price=90_000_000, floor_m2=60.0),
            rec(5, "used_house", price=20_000_000, building_m2=55.0, land_m2=90.0, **house)]
    assert ids(tmp_path, recs, {"sort": "unit_asc"}, {"sort": "unit_asc", "types": ["used_house"]},
               {"sort": "size_desc"}, {"sort": "size_desc", "types": ["land"]}) == [
        ["3", "1", "4", "2", "5"],           # land 10万/㎡, condos 100万 and 150万; houses last
        ["2", "5"],                          # only houses: by their own unit price
        ["2", "4", "5", "1", "3"],           # buildings by floor area, land last
        ["3"],
    ]


@needs_node
def test_counts_of_variants_in_one_pass(tmp_path):
    events = {"20261015T100000": [{"kind": "new", "type": "used_condo", "id": "1", "payload": {}},
                                  {"kind": "price_changed", "type": "used_condo", "id": "2",
                                   "payload": {"price": 40_000_000, "old_price": 50_000_000}}]}
    recs = [rec(1), rec(2, price=40_000_000), rec(3, layout="1K"), rec(4, "land", land_m2=90.0, **LAND),
            rec(5, stations=[{"line": "小田急線", "name": "喜多見", "walk": 12}])]
    patches = {"fresh": {"newOnly": True}, "cheaper": {"dropsOnly": True}, "land": {"types": ["land"]},
               "kitami": {"stations": ["喜多見"]}, "none": {}}
    [c] = site_queries(tmp_path, recs, [{"query": {"plan": 8, "walk": 10}, "counts": patches}], events=events)
    # plan 2LDK+ and 10 min: 1, 2 (3 is a 1K, 4 land, 5 is 12 min away); 土地 lets 4 through, 喜多見 none
    assert c == {"total": 2, "fresh": 1, "cheaper": 1, "land": 1, "kitami": 0, "none": 2}


# Natural search: a few wards and stations, in kanji, kana and English
NL_AREAS = {"13113": "渋谷区", "13112": "世田谷区", "13103": "港区", "13104": "新宿区", "14103": "横浜市西区"}
NL_STATIONS = {"渋谷": ["しぶや", "Shibuya"], "新宿": ["しんじゅく", "Shinjuku"],
               "二子玉川": ["ふたこたまがわ", "Futako-Tamagawa"], "自由ケ丘": ["じゆうがおか", "Jiyugaoka"],
               "勝どき": ["かちどき", "Kachidoki"]}
NL_OPTS = {"places": {"渋谷区": "Shibuya", "世田谷区": "Setagaya", "港区": "Minato", "新宿区": "Shinjuku"},
           "features": {"ペット相談": "Pets negotiable", "角住戸": "Corner unit", "角地": "Corner lot"}, "year": 2026}


def nl_records():
    st = lambda name, walk=5: [{"line": "線", "name": name, "walk": walk}]  # noqa: E731
    return [rec(1, area_code="13113", area="渋谷区", stations=st("渋谷"), features=["ペット相談", "角住戸"],
                name="パークタワー渋谷"),
            rec(2, area_code="13113", area="渋谷区", stations=st("渋谷", 12), layout="1LDK"),
            rec(3, area_code="13112", area="世田谷区", stations=st("二子玉川"), layout="2LDK"),
            rec(4, area_code="13112", area="世田谷区", stations=st("自由が丘"), name="自由が丘レジデンス"),
            rec(5, area_code="13103", area="港区", stations=st("勝どき"), name="パークタワー勝どき"),
            rec(6, area_code="13104", area="新宿区", stations=st("新宿")),
            rec(7, "land", area_code="13112", area="世田谷区", stations=st("二子玉川"), land_m2=200.0,
                features=["角地"], **LAND),
            rec(8, "used_house", area_code="13112", area="世田谷区", stations=st("自由が丘"), layout="4LDK",
                floor_m2=None, building_m2=100.0, land_m2=170.0),
            rec(9, area_code="14103", area="横浜市西区", stations=st("横浜")),
            rec(10, area_code="13104", area="新宿区", stations=st("新宿"), name="自由が丘　レジデンス")]  # spaced


def nl(tmp_path, requests):
    return site_queries(tmp_path, nl_records(), [{**r, "opts": NL_OPTS} for r in requests], areas=NL_AREAS,
                        stations=NL_STATIONS)


@needs_node
def test_suggestions(tmp_path):
    texts = ["渋谷", "しぶや", "3LDK futako", "パークタ", "ペット", "x", "自由が丘 ", "minato", "勝どき", "自由が丘レ"]
    out = dict(zip(texts, nl(tmp_path, [{"suggest": t} for t in texts]), strict=True))
    brief = {t: [(c["kind"], c["value"], c["count"]) for c in cs] for t, cs in out.items()}
    assert brief["渋谷"] == [("area", "13113", 2), ("station", "渋谷", 2), ("name", "パークタワー渋谷", 1)]
    assert brief["しぶや"] == [("station", "渋谷", 2)]                     # kana: stations' readings
    assert brief["3LDK futako"] == [("station", "二子玉川", 2)]             # the word after the last space
    assert brief["パークタ"] == [("name", "パークタワー渋谷", 1), ("name", "パークタワー勝どき", 1)]   # shorter first
    assert brief["ペット"] == [("word", "ペット相談", 1)]
    assert brief["x"] == [] and brief["自由が丘 "] == []                    # too short; nothing typed yet
    assert brief["minato"] == [("area", "13103", 1)]                        # English area names
    assert out["勝どき"][0] == {"kind": "station", "value": "勝どき", "label": "勝どき", "count": 1}
    assert brief["自由が丘レ"] == [("name", "自由が丘レジデンス", 2)]   # spelled with and without a space: one


CONDOS, HOUSES = {"used_condo", "new_condo"}, {"used_house", "new_house"}
SHIBUYA_STATION = {"field": "stations", "value": "渋谷", "label": "渋谷"}
NL_CASES = [  # text, patch (types as sets), and where it matters: alt of the first area token, rest, ignored
    ("渋谷 3LDK 6000万以下 駅徒歩10分 ペット可 築10年以内",
     {"areas": ["13113"], "plan": 11, "priceMax": 60_000_000, "walk": 10, "features": ["ペット相談"], "ageMax": 10},
     {"alt": SHIBUYA_STATION}),
    ("世田谷区 戸建て 土地50坪以上", {"areas": ["13112"], "types": HOUSES, "landMin": 165}, {}),
    ("3LDK under 8000万 near Shibuya pet",
     {"plan": 11, "priceMax": 80_000_000, "stations": ["渋谷"], "features": ["ペット相談"]}, {}),
    ("new condo Minato 2LDK", {"types": {"new_condo"}, "areas": ["13103"], "plan": 8}, {}),
    ("南向き 角部屋 タワマン", {"south": True, "features": ["角住戸"], "tower": True, "types": CONDOS}, {}),
    ("渋谷駅 徒歩5分", {"stations": ["渋谷"], "walk": 5}, {}),
    ("5000万〜8000万", {"priceMin": 50_000_000, "priceMax": 80_000_000}, {}),
    ("5000〜8000万円", {"priceMin": 50_000_000, "priceMax": 80_000_000}, {}),
    ("1億2000万以下", {"priceMax": 120_000_000}, {}),
    ("1.5億まで", {"priceMax": 150_000_000}, {}),
    ("8000万から", {"priceMin": 80_000_000}, {}),
    ("70㎡以上 2LDK", {"sizeMin": 70, "plan": 8}, {}),
    ("over 100 sqm", {"sizeMin": 100}, {}),
    ("築浅 マンション", {"ageMax": 5, "types": CONDOS}, {}),
    ("新築 戸建て 世田谷", {"types": {"new_house"}, "areas": ["13112"]}, {}),
    ("中古マンション 1981年以降", {"types": {"used_condo"}, "post1981": True}, {}),
    ("2000年以降 中古", {"types": {"used_condo", "used_house"}}, {"ignored": ["2000年以降"]}),
    ("ふたこたまがわ 駅近", {"stations": ["二子玉川"], "walk": 5}, {}),
    ("jiyugaoka 2ldk", {"stations": ["自由が丘"], "plan": 8}, {}),
    ("パークタワー勝どき", {}, {"rest": "パークタワー勝どき"}),
    ("勝どき パークタワー", {"stations": ["勝どき"]}, {"rest": "パークタワー"}),
    ("新宿区 1LDK 仲介手数料なし 即入居", {"areas": ["13104"], "plan": 5, "nofee": True, "ready": True}, {}),
    ("2ldk under 5000万 10 min walk", {"plan": 8, "priceMax": 50_000_000, "walk": 10}, {}),
    ("築30年以上", {}, {"ignored": ["築30年以上"]}),
    ("土地 30坪 渋谷区", {"types": {"land"}, "landMin": 99, "areas": ["13113"]}, {}),
    ("港区・渋谷区 6000以下", {"areas": ["13103", "13113"], "priceMax": 60_000_000}, {}),
    ("Shibuya-ku 80m2", {"areas": ["13113"], "sizeMin": 80}, {}),
    ("新宿", {"areas": ["13104"]}, {"alt": {"field": "stations", "value": "新宿", "label": "新宿"}}),
    # beyond the table: a later number wins, kana is a station, English names like kanji, a corner of land
    ("徒歩10分 徒歩7分", {"walk": 7}, {"ignored": ["徒歩10分"]}),
    ("しぶや", {"stations": ["渋谷"]}, {}),
    ("shibuya", {"areas": ["13113"]}, {"alt": SHIBUYA_STATION}),
    ("土地 角地", {"types": {"land"}, "features": ["角地"]}, {}),
    ("横浜", {"areas": ["14103"]}, {"alt": {"field": "stations", "value": "横浜", "label": "横浜"}}),  # the city
    ("8000万〜5000万 バス10分", {"priceMin": 50_000_000, "priceMax": 80_000_000}, {"ignored": ["バス10分"]}),
    # English amounts (M = million, B = billion; m2 stays a size), walk and floor phrases
    ("under 100M", {"priceMax": 100_000_000}, {}),
    ("2LDK under ¥80M within 10 min walk", {"plan": 8, "priceMax": 80_000_000, "walk": 10}, {}),
    ("50-80M 100m2", {"priceMin": 50_000_000, "priceMax": 80_000_000, "sizeMin": 100}, {}),
    ("over 1.2B", {"priceMin": 1_200_000_000}, {}),
    ("up to 90 million", {"priceMax": 90_000_000}, {}),
    ("south facing top floor", {"south": True, "top": True}, {}),
    ("in 5 min walk", {"walk": 5}, {}),
]


@needs_node
def test_natural_search(tmp_path):
    out = nl(tmp_path, [{"parse": text} for text, _, _ in NL_CASES])
    for (text, patch, extra), got in zip(NL_CASES, out, strict=True):
        p = {k: set(v) if k == "types" else v for k, v in got["patch"].items()}
        assert p == patch, text
        assert got["rest"] == extra.get("rest", ""), text
        assert got["ignored"] == extra.get("ignored", []), text
        if "alt" in extra:
            assert next(t for t in got["tokens"] if t["field"] == "areas")["alt"] == extra["alt"], text


@needs_node
def test_a_parse_applied_to_the_query(tmp_path):
    before = {"types": ["used_condo", "land"], "areas": ["13103"], "features": ["角住戸"], "walk": 20, "text": "x"}
    [a, b] = nl(tmp_path, [{"parse": "世田谷 戸建て 徒歩9分 ペット パーク", "query": before},
                           {"parse": "渋谷 徒歩7分", "query": {}}])
    q = a["query"]
    assert set(q["types"]) == HOUSES                             # types are replaced
    assert q["areas"] == ["13103", "13112"] and q["features"] == ["角住戸", "ペット相談"]   # sets add up
    assert q["walk"] == 9 and q["text"] == "パーク"              # any number; text = what wasn't understood
    assert b["ids"] == ["1"]                                     # 渋谷区 within 7 min (2 is 12 min away)


def hidden_recs():
    return [rec(1, features=["南向き", "ペット相談"]), rec(2, features=["即引渡し可"], direction="北"),
            rec(3, "land", land_m2=200.0, features=["建築条件なし", "土地50坪以上"], **LAND),
            rec(4, features=["最上階・上階なし"]), rec(5, "land", land_m2=120.0, build_condition=True, **LAND)]


@needs_node
def test_tags_a_condition_covers_are_hidden_and_old_links_still_work(tmp_path):
    old = [{"features": ["南向き"]}, {"features": ["即引渡可"]}, {"features": ["最上階・上階なし"]},
           {"features": ["建築条件なし"], "types": ["land"]}, {"features": ["土地50坪以上"], "types": ["land"]}]
    got = [r["ids"] for r in site_queries(tmp_path, hidden_recs(), [{"query": q, "upgrade": True} for q in old])]
    assert got == [["1"], ["2"], ["4"], ["3"], ["3"]]     # an old link: its tags as the conditions they are now
    index = json.loads((tmp_path / "site/data/p/tokyo.json").read_text())
    assert index["features"] == ["ペット相談"]                    # only tags no condition covers are offered
    assert "南向き" in json.loads((tmp_path / "site/data/l/used_condo/1.json").read_text())["features"]


@needs_node
def test_upgrade_turns_old_tags_into_conditions(tmp_path):
    site_queries(tmp_path, [rec(1)], [])
    script = """
    const Filter = require(process.argv[1]);
    const q = Object.assign(Filter.emptyQuery(), {features: ["全室南向き", "即引渡し可", "最上階・上階なし",
      "建築条件なし", "土地100坪以上", "土地50坪以上", "ペット相談"], landMin: 200});
    process.stdout.write(JSON.stringify(Filter.upgrade(q)));
    """
    out = json.loads(subprocess.run([NODE, "-e", script, str(FILTER_JS)], capture_output=True, text=True,
                                    check=True).stdout)
    assert out["features"] == ["ペット相談"] and out["landMin"] == 331
    assert out["south"] and out["ready"] and out["top"] and out["noCondition"]


@needs_node
def test_conditions_on_an_index_older_than_the_page(tmp_path, monkeypatch):
    import suumo.site as site
    monkeypatch.setattr(site, "HIDDEN_TAGS", set())                # an old index: the tags, no flag bits
    monkeypatch.setattr(site, "FLAGS", {**site.FLAGS, "south": 0, "ready": 0, "top": 0})
    got = ids(tmp_path, hidden_recs(), {"south": True}, {"ready": True}, {"top": True}, {"features": ["南向き"]})
    assert got == [[], [], [], ["1"]]                             # a flag it doesn't have: no listing has it


@needs_node
def test_text_search_finds_areas_by_their_english_name(tmp_path):
    recs = [rec(1), rec(2, area_code="13113", area="渋谷区")]
    got = site_queries(tmp_path, recs, [{"query": {"text": "komae"}}, {"query": {"text": "SHIBUYA"}}],
                       areas={"13219": "狛江市", "13113": "渋谷区"}, places={"狛江市": "Komae", "渋谷区": "Shibuya"})
    assert [r["ids"] for r in got] == [["1"], ["2"]]


@needs_node
def test_listings_in_a_box_keep_the_results_order(tmp_path):
    towns = {"東京都狛江市東和泉": [35.63, 139.58], "東京都狛江市岩戸北": [35.64, 139.57],
             "東京都狛江市和泉本町": [35.70, 139.60]}
    at = lambda i, town, price: rec(i, address=f"東京都狛江市{town}", price=price)  # noqa: E731
    recs = [at(1, "東和泉", 30_000_000), at(2, "岩戸北", 20_000_000), at(3, "和泉本町", 10_000_000),
            *[at(10 + k, "駒井町", 40_000_000 + k) for k in range(30)]]   # (no coordinates: never in a box)
    boxes = [[35.6, 139.5, 35.65, 139.59], [35.0, 139.0, 36.0, 140.0], [35.64, 139.57, 35.64, 139.57],
             [35.7, 139.61, 35.8, 139.7]]
    got = site_queries(tmp_path, recs, [{"query": {"sort": "price_asc"}, "box": box} for box in boxes] +
                       [{"query": {"sort": "price_asc", "priceMax": 30_000_000}, "box": boxes[1]}], towns=towns)
    # edges are in; cheapest first, as searched (a few of many results, or most of them)
    assert got == [["2", "1"], ["3", "2", "1"], ["2"], [], ["3", "2", "1"]]
