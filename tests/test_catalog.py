"""Search catalog over data/: filters, folding duplicates, sorts, facets, price history, reload."""
import json
import random
from datetime import date

import pytest

from suumo.catalog import Catalog, Query, indices, line_name, load, parse_rooms, signature

TODAY = date(2026, 10, 20)


def rec(i, type_="used_condo", **kw):
    r = {"id": str(i), "type": type_, "area_code": "13219", "area": "狛江市", "town": "東和泉",
         "price": 50_000_000, "layout": "3LDK", "floor_m2": 70.0, "built": "2005-04",
         "stations": [{"line": "小田急線", "name": "狛江", "walk": 5}],
         "url": f"https://suumo.jp/x/nc_{i}/", "first_seen": "2026-10-01"}
    r.update(kw)
    return {k: v for k, v in r.items() if v is not None}


def write(data, pref="tokyo", areas=None, events=None, removed=(), **by_type):
    d = data / pref
    d.mkdir(parents=True, exist_ok=True)
    lines = lambda rs: "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rs)  # noqa: E731
    for t, recs in by_type.items():
        for old in (d / t).glob("*.jsonl"):
            old.unlink()
        for area in {r["area_code"] for r in recs}:
            (d / t).mkdir(exist_ok=True)
            (d / t / f"{area}.jsonl").write_text(lines(r for r in recs if r["area_code"] == area))
    for t in {r["type"] for r in removed}:
        (d / "removed").mkdir(exist_ok=True)
        (d / "removed" / f"{t}.jsonl").write_text(lines(r for r in removed if r["type"] == t))
    (d / "areas.json").write_text(json.dumps(areas or {"13219": "狛江市"}, ensure_ascii=False))
    for run_id, evs in (events or {}).items():
        (data / "events").mkdir(exist_ok=True)
        (data / "events" / f"{run_id}.json").write_text(json.dumps(evs, ensure_ascii=False))


def snap_of(tmp_path, *recs, **kw):
    by_type = {}
    for r in recs:
        by_type.setdefault(r["type"], []).append(r)
    write(tmp_path, **kw, **by_type)
    return load(tmp_path, TODAY)


def ids(hits):
    return [h.item.rec["id"] for h in hits]


@pytest.mark.parametrize("layout,rooms", [
    ("3LDK", {3}), ("3LDK+S（納戸）", {3}), ("ワンルーム", {1}), ("2LDK・2LDK+S・4LDK", {2, 4}),
    ("1LDK+2S（納戸）～3LDK", {1, 2, 3}), ("3LDK：1号棟・2号棟／WIC", {3}), ("4DK", {4}), ("1K", {1}), (None, set()),
])
def test_parse_rooms(layout, rooms):
    assert parse_rooms(layout) == rooms


def test_line_name_drops_section():
    assert line_name("小田急線（新宿～相模大野）") == "小田急線"


def test_price_uses_the_range(tmp_path):
    s = snap_of(tmp_path, rec(1, price=30_000_000), rec(2, price=45_000_000, price_max=60_000_000),
                rec(3, price=None), rec(4, price=80_000_000))
    assert ids(s.search(Query(price_max=50_000_000))) == ["2", "1"]       # 2 has a plot from 4,500万
    assert ids(s.search(Query(price_min=55_000_000))) == ["4", "2"]       # 2 has a plot up to 6,000万
    assert len(s.search(Query())) == 4                                    # no price filter: 価格未定 included


def test_rooms_buckets_and_land(tmp_path):
    s = snap_of(tmp_path, rec(1, layout="1LDK"), rec(2, layout="4LDK"), rec(3, layout="6LDK"),
                rec(4, "land", layout=None, floor_m2=None, land_m2=100.0))
    assert ids(s.search(Query(rooms=(4,)))) == ["3", "2"]
    assert ids(s.search(Query(rooms=(1, 4), types=("land",)))) == ["4"]  # rooms don't apply to land


def test_size_land_and_age(tmp_path):
    s = snap_of(tmp_path, rec(1, floor_m2=55.0), rec(2, floor_m2=80.0, built="1985-01"),
                rec(3, "new_house", floor_m2=None, building_m2=95.0, land_m2=110.0, built=None),
                rec(4, "land", floor_m2=None, layout=None, land_m2=150.0, built=None))
    assert ids(s.search(Query(size_min=70))) == ["3", "2"]        # a building size implies a building...
    assert ids(s.search(Query(size_min=70, types=("land", "new_house")))) == ["4", "3"]  # ...unless land chosen
    assert ids(s.search(Query(land_min=120))) == ["4"]
    assert ids(s.search(Query(age_max=25))) == ["3", "1"]         # 2005 -> 21 years; new counts as 0
    assert ids(s.search(Query(age_max=45))) == ["3", "2", "1"]


def test_stations_and_walk(tmp_path):
    s = snap_of(tmp_path,
                rec(1, stations=[{"line": "小田急線", "name": "狛江", "walk": 12},
                                 {"line": "京王線", "name": "柴崎", "walk": 6}]),
                rec(2, stations=[{"line": "小田急線（新宿～相模大野）", "name": "狛江", "walk": 3}]),
                rec(3, stations=[{"line": "小田急バス", "name": "狛江第5小学校", "walk": 1, "bus_stop": True}]),
                rec(4, stations=[{"line": "小田急線", "name": "喜多見", "bus": 8, "walk": 2}]))
    assert ids(s.search(Query(stations=("狛江",)))) == ["2", "1"]
    assert ids(s.search(Query(walk_max=7))) == ["2", "1"]                  # bus access doesn't count
    assert ids(s.search(Query(stations=("狛江",), walk_max=7))) == ["2"]   # 1 is 12 min from 狛江
    assert s.facet_lines(Query()) == [("小田急線", 2), ("京王線", 1)]       # bus lines aren't offered
    assert s.facet_stations(Query(walk_max=10), "小田急線") == [("狛江", 1)]


def test_same_station_on_two_lines_is_one_choice(tmp_path):
    s = snap_of(tmp_path, rec(1, stations=[{"line": "小田急線", "name": "登戸", "walk": 4}]),
                rec(2, stations=[{"line": "ＪＲ南武線", "name": "登戸", "walk": 6}]))
    assert ids(s.search(Query(stations=("登戸",)))) == ["2", "1"]
    assert s.facet_stations(Query(), "ＪＲ南武線") == [("登戸", 2)]   # both are near 登戸


def test_features_freehold_new_and_drops(tmp_path):
    events = {"20261015T040000": [{"kind": "new", "type": "used_condo", "id": "2", "payload": {}},
                                  {"kind": "price_changed", "type": "used_condo", "id": "2",
                                   "payload": {"price": 45_000_000, "old_price": 50_000_000}},
                                  {"kind": "price_changed", "type": "used_condo", "id": "3",
                                   "payload": {"price": 52_000_000, "old_price": 50_000_000}}]}
    s = snap_of(tmp_path, rec(1, features=["ペット相談", "角住戸"], land_rights="所有権", has_detail=True),
                rec(2, features=["ペット相談"], land_rights="定期借地権", has_detail=True),
                rec(3), events=events)
    assert ids(s.search(Query(features=("ペット相談",)))) == ["2", "1"]
    assert ids(s.search(Query(features=("ペット相談", "角住戸")))) == ["1"]
    assert ids(s.search(Query(freehold_only=True))) == ["3", "1"]           # unknown rights kept
    assert ids(s.search(Query(new_only=True))) == ["2"]                    # from events, not first_seen
    assert ids(s.search(Query(sort="new"))) == ["2", "3", "1"]
    assert ids(s.search(Query(drops_only=True))) == ["2"]                  # 3 went up
    assert s.history["used_condo:2"] == [("2026-10-15", 50_000_000, 45_000_000)]
    assert s.facet_features(Query(features=("角住戸",)))[0] == ("ペット相談", 2)
    # 3's page isn't fetched: it doesn't match a tag condition, but alerts (lenient) let it through
    q = Query(features=("ペット相談",))
    assert s.undetailed(q) == 1
    assert [i.rec["id"] for i in s.items if s.matches(i, q, lenient=True)] == ["1", "2", "3"]


def test_quake_standard_and_build_condition(tmp_path):
    s = snap_of(tmp_path, rec(1, built="1981-05"), rec(2, built="1981-06"), rec(3, "new_house", built=None),
                rec(4, "land", layout=None, floor_m2=None, land_m2=100.0, build_condition=True),
                rec(5, "land", layout=None, floor_m2=None, land_m2=100.0))
    assert ids(s.search(Query(post_1981=True, types=("used_condo", "new_house")))) == ["3", "2"]
    assert ids(s.search(Query(no_condition=True, types=("land",)))) == ["5"]


def test_land_listed_as_new_house_is_kept_once(tmp_path):
    land = rec(7, "land", layout=None, floor_m2=None, land_m2=100.0, url="https://suumo.jp/tochi/tokyo/nc_7/")
    s = snap_of(tmp_path, {**land, "type": "new_house", "price_excludes_building": True}, land)
    assert [i.key for i in s.items] == ["land:7"]
    alone = snap_of(tmp_path / "b", {**land, "type": "new_house", "price_excludes_building": True})
    assert alone.items[0].conditional


def test_loosen_names_the_blocking_condition(tmp_path):
    s = snap_of(tmp_path, rec(1, price=60_000_000), rec(2, price=70_000_000, layout="2LDK"))
    q = Query(price_max=50_000_000, rooms=(3,))
    assert s.count(q) == 0
    assert s.loosen(q) == [("price_max", 1)]


def test_duplicates_fold_to_cheapest(tmp_path):
    s = snap_of(tmp_path, rec(1, dup_key="aa", price=50_000_000), rec(2, dup_key="aa", price=49_800_000),
                rec(3, dup_key="bb"))
    hits = s.search(Query())
    assert ids(hits) == ["3", "2"]
    assert [o.rec["id"] for o in hits[1].others] == ["1"]
    assert s.count(Query()) == 2
    # the representative is the cheapest *matching* listing
    assert ids(s.search(Query(price_min=49_900_000))) == ["3", "1"]


@pytest.mark.parametrize("sort,expected", [
    ("new", ["3", "2", "1"]), ("price_asc", ["2", "1", "3"]), ("price_desc", ["3", "1", "2"]),
    ("size_desc", ["1", "3", "2"]), ("walk_asc", ["3", "1", "2"]), ("age_asc", ["2", "3", "1"]),
    ("unit_asc", ["1", "2", "3"]),
])
def test_sorts(tmp_path, sort, expected):
    s = snap_of(tmp_path,
                rec(1, price=50_000_000, floor_m2=90.0, built="1990-01", first_seen="2026-10-01",
                    stations=[{"line": "小田急線", "name": "狛江", "walk": 8}]),
                rec(2, price=30_000_000, floor_m2=50.0, built="2020-01", first_seen="2026-10-02",
                    stations=[{"line": "小田急線", "name": "狛江", "walk": 15}]),
                rec(3, price=90_000_000, floor_m2=60.0, built="2010-01", first_seen="2026-10-02",
                    stations=[{"line": "小田急線", "name": "狛江", "walk": 2}]))
    assert ids(s.search(Query(sort=sort))) == expected


def test_land_stays_offered_while_building_conditions_are_set(tmp_path):
    s = snap_of(tmp_path, rec(1), rec(2, "land", layout=None, floor_m2=None, land_m2=90.0))
    q = Query(types=("used_condo",), rooms=(3,))
    assert dict(s.facet_types(q)) == {"used_condo": 1, "land": 1}   # choosing 土地 would add it


def test_facets_ignore_their_own_dimension(tmp_path):
    s = snap_of(tmp_path, rec(1), rec(2, area_code="13208"), rec(3, "used_house", building_m2=90.0),
                areas={"13219": "狛江市", "13208": "調布市"})
    q = Query(types=("used_condo",), areas=("13219",))
    assert s.facet_types(q) == [("used_condo", 1), ("used_house", 1)]
    assert s.facet_areas(q) == [("13208", 1), ("13219", 1)]


def test_removed_listings_are_found_by_key_only(tmp_path):
    gone = rec(9, removed_at="2026-10-10")
    s = snap_of(tmp_path, rec(1), removed=[gone])
    assert s.get("used_condo:9").removed and s.get("used_condo:1")
    assert ids(s.search(Query())) == ["1"]


def test_query_round_trip_and_tolerance():
    q = Query().set(types={"land", "used_condo"}, price_max=60_000_000, new_only=True, sort="price_asc")
    assert q.types == ("land", "used_condo")
    assert Query.from_dict(q.to_dict()) == q
    assert Query().to_dict() == {}
    assert Query.from_dict({"bogus": 1, "price_max": "x", "sort": "nope", "rooms": [3]}) == Query(rooms=(3,))
    assert Query.from_dict({"rooms": ["3", 9, 2], "types": ["land", "castle"], "areas": [1, "13219"]}) == \
        Query(rooms=(2,), types=("land",), areas=("13219",))
    assert Query(sort="price_asc").is_empty() and not q.is_empty()


def test_catalog_reloads_on_change(tmp_path):
    write(tmp_path, used_condo=[rec(1)])
    cat = Catalog(tmp_path, lambda: TODAY)
    assert cat.refresh() and len(cat.snap.items) == 1
    assert not cat.refresh()
    sig = signature(tmp_path)
    write(tmp_path, used_condo=[rec(1), rec(2)])
    assert signature(tmp_path) != sig
    assert cat.refresh() and len(cat.snap.items) == 2


def test_partial_events_file_is_skipped(tmp_path):
    write(tmp_path, used_condo=[rec(1)])
    (tmp_path / "events").mkdir()
    (tmp_path / "events" / "20261015T040000.json").write_text('[{"kind": ')
    s = load(tmp_path, TODAY)
    assert s.runs == [] and len(s.items) == 1


def test_bitset_index_agrees_with_the_reference_rules(tmp_path):
    """mask() (bitsets) and matches() (per listing) must agree on every query: random data, random queries."""
    rnd = random.Random(7)
    types = ["used_condo", "new_house", "used_house", "land", "new_condo"]
    stations = [("小田急線", "狛江"), ("小田急線", "喜多見"), ("京王線", "柴崎"), ("ＪＲ南武線", "登戸"),
                ("小田急線", "登戸")]
    tags = ["ペット相談", "角住戸", "南向き"]
    recs = []
    for n in range(300):
        t = rnd.choice(types)
        sts = [{"line": ln, "name": nm, **({"bus": 5} if rnd.random() < 0.1 else {}), "walk": rnd.randint(1, 25)}
               for ln, nm in rnd.sample(stations, rnd.randint(0, 3))]
        recs.append(rec(n, t, area_code=rnd.choice(["13219", "13208"]),
                        price=rnd.choice([None, rnd.randint(20, 120) * 1_000_000]),
                        price_max=rnd.choice([None, None, 130_000_000]),
                        layout=None if t == "land" else rnd.choice(["1LDK", "2LDK", "3LDK+S", "4LDK", "5DK", None]),
                        floor_m2=rnd.choice([None, 45.0, 70.0, 95.0]), land_m2=rnd.choice([None, 80.0, 150.0]),
                        built=rnd.choice([None, "1975-03", "1981-06", "2001-01", "2020-12"]), stations=sts,
                        features=rnd.sample(tags, rnd.randint(0, 2)), has_detail=rnd.random() < 0.7,
                        land_rights=rnd.choice([None, "所有権", "定期借地権"]), build_condition=rnd.random() < 0.1,
                        dup_key=rnd.choice([None, "a", "b", "c"])))
    s = snap_of(tmp_path, *recs, areas={"13219": "狛江市", "13208": "調布市"})
    for _ in range(400):
        q = Query().set(
            types=rnd.sample(types, rnd.randint(0, 2)), areas=rnd.sample(["13219", "13208"], rnd.randint(0, 1)),
            stations=rnd.sample(["狛江", "喜多見", "柴崎", "登戸"], rnd.randint(0, 2)),
            walk_max=rnd.choice([None, 5, 10, 15]), price_max=rnd.choice([None, 50_000_000, 80_000_000]),
            price_min=rnd.choice([None, 40_000_000]), rooms=rnd.sample([1, 2, 3, 4], rnd.randint(0, 2)),
            size_min=rnd.choice([None, 60]), land_min=rnd.choice([None, 100]), age_max=rnd.choice([None, 20, 45]),
            post_1981=rnd.random() < 0.2, features=rnd.sample(tags, rnd.randint(0, 1)),
            freehold_only=rnd.random() < 0.2, no_condition=rnd.random() < 0.2)
        for lenient in (False, True):
            expected = [i.idx for i in s.items if s.matches(i, q, lenient=lenient)]
            assert sorted(indices(s.mask(q, lenient=lenient))) == expected, q
