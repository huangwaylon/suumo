"""Derived listing fields from data/: rooms, sizes, age, stations, flags, duplicate groups, 新着/値下げ."""
import pytest

from suumo.catalog import line_name, load, parse_rooms
from tests.helpers import TODAY, rec, write


def snap(tmp_path, *recs, **kw):
    write(tmp_path, recs, **kw)
    return load(tmp_path, TODAY)


@pytest.mark.parametrize("layout,rooms", [
    ("3LDK", {3}), ("3LDK+S（納戸）", {3}), ("ワンルーム", {1}), ("2LDK・2LDK+S・4LDK", {2, 4}),
    ("1LDK+2S（納戸）～3LDK", {1, 2, 3}), ("3LDK：1号棟・2号棟／WIC", {3}), ("4DK", {4}), ("1K", {1}), (None, set()),
])
def test_parse_rooms(layout, rooms):
    assert parse_rooms(layout) == rooms


def test_line_name_drops_section():
    assert line_name("小田急線（新宿～相模大野）") == "小田急線"


def test_derived_fields(tmp_path):
    s = snap(tmp_path,
             rec(1, built="1981-05", land_rights="定期借地権", floor_m2=50.0, price=40_000_000,
                 stations=[{"line": "小田急線（新宿～相模大野）", "name": "狛江", "walk": 3},
                           {"line": "小田急線", "name": "喜多見", "bus": 8, "walk": 2},
                           {"line": "小田急バス", "name": "狛江第5小学校", "walk": 1, "bus_stop": True}]),
             rec(2, "new_house", layout="3LDK～4LDK", floor_m2=None, building_m2=95.0, land_m2=110.0,
                 land_m2_max=120.0, price_max=60_000_000, built=None),
             rec(3, "land", layout="3LDK", floor_m2=None, land_m2=100.0, built=None, build_condition=True))
    condo, house, land = ({i.type: i for i in s.items}[t] for t in ("used_condo", "new_house", "land"))
    assert condo.age == 45 and not condo.post_1981 and condo.leasehold and condo.unit_price == 800_000
    assert condo.stations == [("狛江", "小田急線", 3), ("喜多見", "小田急線", None)]  # bus access, no bus stops
    assert house.rooms == {3, 4} and house.size == 95.0 and house.land == 120.0 and house.age == 0
    assert house.post_1981 and house.unit_price is None                              # a price range
    assert land.rooms == frozenset() and land.size is None and land.conditional


def test_new_drops_and_history_come_from_events(tmp_path):
    events = {"20261015T040000": [{"kind": "new", "type": "used_condo", "id": "2", "payload": {}},
                                  {"kind": "price_changed", "type": "used_condo", "id": "2",
                                   "payload": {"price": 45_000_000, "old_price": 50_000_000}},
                                  {"kind": "price_changed", "type": "used_condo", "id": "3",
                                   "payload": {"price": 52_000_000, "old_price": 50_000_000}}],
              "20260901T040000": [{"kind": "new", "type": "used_condo", "id": "1", "payload": {}}]}
    s = snap(tmp_path, rec(1), rec(2), rec(3), events=events)
    by_id = {i.rec["id"]: i for i in s.items}
    assert [s.is_new(by_id[k]) for k in "123"] == [False, True, False]   # 1 was new too long ago
    assert [s.is_dropped(by_id[k]) for k in "123"] == [False, True, False]  # 3 went up
    assert s.history["used_condo:2"] == [("2026-10-15", 50_000_000, 45_000_000)]


def test_land_listed_as_new_house_is_kept_once(tmp_path):
    land = rec(7, "land", layout=None, floor_m2=None, land_m2=100.0, url="https://suumo.jp/tochi/tokyo/nc_7/")
    s = snap(tmp_path, {**land, "type": "new_house", "price_excludes_building": True}, land)
    assert [i.key for i in s.items] == ["land:7"]
    alone = snap(tmp_path / "b", {**land, "type": "new_house", "price_excludes_building": True})
    assert alone.items[0].conditional


def test_duplicate_groups_need_two_listings(tmp_path):
    s = snap(tmp_path, rec(1, dup_key="aa"), rec(2, dup_key="aa"), rec(3, dup_key="bb"))
    assert list(s.groups) == ["aa"] and [i.rec["id"] for i in s.groups["aa"]] == ["1", "2"]
