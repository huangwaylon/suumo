"""Search-result and area-page parsers against real SUUMO pages saved in tests/fixtures (Komae, 2026-10-05).
Listing pages: tests/test_extract.py."""
import pytest

from suumo.parse import parse_areas, parse_list_page
from tests.helpers import fixture_html

KOMAE = {"code": "13219", "name": "狛江市"}


def page(name):
    return fixture_html(name)


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


def test_a_page_without_a_hit_count_has_none():
    html = page("list_used_condo_komae.html.gz").replace("pagination_set-hit", "x").replace("hitbox", "x")
    assert parse_list_page(html, "used_condo", KOMAE)[0] is None

