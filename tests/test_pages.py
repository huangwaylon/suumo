"""Parsers against real SUUMO pages saved in tests/fixtures (Komae, 2026-10-05)."""
from pathlib import Path

import pytest

from suumo.archive import Archive
from suumo.detail import parse_detail
from suumo.parse import parse_areas, parse_list_page

FIX = Path(__file__).parent / "fixtures"
KOMAE = {"code": "13219", "name": "狛江市"}


def page(name):
    return Archive.read(FIX / name)


def test_area_page_counts_include_comma_formatted_numbers():
    areas = {a["code"]: a for a in parse_areas(page("area_page_used_condo.html.gz"), "ms/chuko", "tokyo")}
    assert areas["13102"] == {"code": "13102", "name": "中央区", "slug": "sc_chuo", "expected": 2257}
    assert areas["13219"]["slug"] == "sc_komae" and areas["13219"]["expected"] == 67
    assert all(a["slug"] for a in areas.values() if a["expected"])


@pytest.mark.parametrize("type_key, hits", [("used_condo", 67), ("new_house", 77), ("new_condo", 1)])
def test_list_pages_parse_every_listing(type_key, hits):
    got_hits, recs = parse_list_page(page(f"list_{type_key}_komae.html.gz"), type_key, KOMAE)
    assert got_hits == hits and len(recs) == hits and len({r["id"] for r in recs}) == hits
    assert all(r.get("stations") and r.get("town") and r["path"].endswith(f"nc_{r['id']}/") for r in recs)
    assert sum("price" in r for r in recs) >= hits * 0.9


def test_condo_listing_page():
    d, meta = parse_detail(page("detail_used_condo_20205670.html.gz"))
    assert d["mgmt_fee"] == 146000 and d["repair_fee"] == 26000
    assert (d["floor"], d["structure"], d["floors_above"]) == (3, "RC", 5)
    assert d["land_rights"] == "定期借地権" and "借地期間残存36年" in d["land_rights_note"]
    assert [s["name"] for s in d["stations"]] == ["喜多見", "狛江"]
    assert "南向き" in d["features"] and d["deal_type"] == "仲介"
    assert meta == {"info_date": "2026-10-01", "next_update": "2026-10-09"}


def test_house_listing_page():
    d, _ = parse_detail(page("detail_used_house_20779514.html.gz"))
    assert (d["structure"], d["floors_above"], d["coverage_pct"], d["far_pct"]) == ("木造", 2, 40, 80)
    assert d["road"]["dir"] == "東" and d["road"]["width_m"] == 5.0
    assert d["stations"][2] == {"line": "小田急バス", "name": "狛江第5小学校", "bus_stop": True, "walk": 3}
    assert d["reform"]["date"] == "2019-06"


def test_land_listing_page():
    d, _ = parse_detail(page("detail_land_20862637.html.gz"))
    assert d["build_condition"] is True and (d["coverage_pct"], d["far_pct"]) == (60, 200)
    assert d["road"] == {"width_m": 10.0, "setback_m2": 48.24, "text": d["road"]["text"]}
