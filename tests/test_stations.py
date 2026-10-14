"""Station names from Wikidata: matching keys, English labels, the area box, the cache."""
import json

from suumo import stations
from suumo.catalog import station_area
from tests.helpers import rec, write


def test_keys_match_the_ways_names_are_written():
    assert stations.key("市ヶ谷") == stations.key("市ケ谷駅") == stations.key("市が谷")
    assert stations.key("浦安駅 (千葉県)") == "浦安" and stations.key("三ノ輪橋停留場") == "三ノ輪橋"
    assert stations.key("駒澤大学") == stations.key("駒沢大学駅")
    assert stations._english("Futako-Tamagawa Station") == "Futako-Tamagawa"
    assert stations._english("Ōtsuka Station (Tokyo)") == "Ōtsuka"
    assert stations._english("Shin‐Yokohama Station") == "Shin-Yokohama"   # U+2010: typed as '-'
    assert stations._english("Zushi·Hayama Station") == "Zushi·Hayama"
    assert stations._english("閻魔大王") is None and stations._english("Category:Kinuta-hommura") is None
    assert stations.key("江曾島") == stations.key("江曽島") and stations.key("江の島") == stations.key("江ノ島")


def test_area_leaves_out_far_places():
    points = [(35.63, 139.58), (35.69, 139.69), (33.11, 139.79), (35.5, 139.7)]  # 八丈島 is over 1° away
    west, south, east, north = station_area(points)
    assert (round(west, 2), round(south, 2), round(east, 2), round(north, 2)) == (139.43, 35.35, 139.85, 35.84)
    assert station_area([]) is None


class FakeSession:
    headers = {}

    def __init__(self, rows):
        self.rows, self.calls = rows, 0

    def get(self, url, params, headers, timeout):
        self.calls += 1
        rows = self.rows

        class R:
            def raise_for_status(self):
                pass

            def json(self):
                return {"results": {"bindings": [{k: {"value": v} for k, v in r.items()} for r in rows]}}
        return R()


def test_update_fills_the_cache_once_per_prefecture(tmp_path, monkeypatch):
    rows = [{"ja": "大島駅", "kana": "おおじまえき", "en": "Ōjima Station"},
            {"ja": "大島駅", "kana": "おおじまえき", "en": "Ojima Station (Tokyo)"},
            {"ja": "練馬駅", "en": "Nerima Station"}]
    session = FakeSession(rows)
    monkeypatch.setattr(stations.requests, "Session", lambda: session)
    rail = tmp_path / "rail.json"
    rail.write_text(json.dumps({"stations": [["大島", 35.69, 139.83, []]]}, ensure_ascii=False))
    cache = tmp_path / "stations.json"
    data = tmp_path / "data"
    write(data, [rec(1, stations=[{"line": "都営新宿線", "name": "大島", "walk": 5}])])
    write(data, [rec(2, area_code="12227", stations=[{"line": "バス", "name": "どこか", "walk": 5}])], pref="chiba")
    for _ in range(2):
        stations.update(cache, rail, ["tokyo", "chiba"], data, log=lambda *a: None)
    assert session.calls == 1 and json.loads(cache.read_text())["_prefs"] == ["tokyo"]  # chiba: no known station
    assert stations.names(cache) == {"大島": ["おおじま", "Ojima"], "練馬": [None, "Nerima"]}
