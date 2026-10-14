"""Derived listing fields from data/: plan rank, sizes, age, stations, flags, duplicate groups, 新着/値下げ."""
import pytest

from suumo.catalog import load, plan_rank
from tests.helpers import TODAY, rec, write


def snap(tmp_path, *recs, **kw):
    write(tmp_path, recs, **kw)
    return load(tmp_path, TODAY)


@pytest.mark.parametrize("layout,rank", [
    ("ワンルーム", 3), ("1K", 3), ("1DK", 4), ("1LDK", 5), ("2DK", 7), ("2LDK", 8), ("3LDK+S（納戸）", 11),
    ("2LDK・2LDK+S・4LDK", 14), ("1LDK+2S（納戸）～3LDK", 11), ("3LDK：1号棟・2号棟／WIC", 11), ("4DK", 13),
    (None, None),
])
def test_plan_rank(layout, rank):
    assert plan_rank(layout) == rank


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
    assert condo.age_months == 545 and not condo.post_1981 and condo.leasehold and condo.unit_price == 800_000
    assert condo.stations == [("狛江", 3), ("喜多見", None)]  # bus access, no bus stops
    assert house.plan == 14 and house.size == 95.0 and house.land == 120.0 and house.age_months == 0
    assert house.post_1981 and house.unit_price is None                              # a price range
    assert land.plan is None and land.size is None and land.conditional and land.pref == "tokyo"


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
    assert [bool(s.drop(by_id[k])) for k in "123"] == [False, True, False]  # 3 went up
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


def test_bus_stops_are_told_from_stations_by_the_railway_nearby(tmp_path):
    bus = {"line": "しずてつジャストライン", "name": "丸子橋入口", "walk": 7}         # SUUMO gives only the bus company
    far = {"line": "20", "name": "体育館前", "walk": 3}  # a tram stop of that name is far away
    write(tmp_path, [rec(1, stations=[bus, {"line": "静岡鉄道", "name": "新静岡", "walk": 9}, far]),
                     rec(2, stations=[{"line": "静岡鉄道", "name": "日吉町", "walk": 4}]),
                     rec(3, stations=[{"line": "ＪＲ東海道本線", "name": "静岡", "walk": 6}])])
    rail = [["新静岡", 34.97, 138.39, []], ["日吉町", 34.97, 138.38, []], ["静岡", 34.97, 138.39, []],
            ["体育館前", 35.0, 135.0, []]]
    item = load(tmp_path, TODAY, rail_stations=rail).items[0]
    assert item.stations == [("新静岡", 9)] and item.stops == [("丸子橋入口", 7), ("体育館前", 3)]
    assert load(tmp_path, TODAY).items[0].stops == []  # no railway data: only SUUMO's own バス marks count


def test_one_spelling_per_station(tmp_path):
    st = lambda *names: [{"line": "線", "name": n, "walk": 5} for n in names]  # noqa: E731
    s = snap(tmp_path, rec(1, stations=st("荻窪", "市ケ谷")), rec(2, stations=st("荻窪駅", "市ヶ谷")),
             rec(3, stations=st("荻窪", "藤沢（長野県）")), rec(4, stations=st("市ケ谷", "藤沢")),
             rec(5, stations=[{"line": "JR中央線", "name": "荻窪駅", "bus": 8}]))
    by_id = {i.rec["id"]: i for i in s.items}
    assert by_id["2"].stations == [("荻窪", 5), ("市ケ谷", 5)]
    assert [st["name"] for st in by_id["2"].rec["stations"]] == ["荻窪", "市ケ谷"]   # the listing page's names too
    assert by_id["3"].stations == [("荻窪", 5), ("藤沢（長野県）", 5)]                # SUUMO's own split name stays
    assert by_id["5"].bus == {"荻窪": 8}
