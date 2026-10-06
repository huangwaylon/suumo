"""Station names from Wikidata: matching keys, English labels, the area box, the cache."""
import json

from suumo import stations


def test_keys_match_the_ways_names_are_written():
    assert stations.key("市ヶ谷") == stations.key("市ケ谷駅") == stations.key("市が谷")
    assert stations.key("浦安駅 (千葉県)") == "浦安" and stations.key("三ノ輪橋停留場") == "三ノ輪橋"
    assert stations.key("駒澤大学") == stations.key("駒沢大学駅")
    assert stations._english("Futako-Tamagawa Station") == "Futako-Tamagawa"
    assert stations._english("Ōtsuka Station (Tokyo)") == "Ōtsuka"


def test_box_leaves_out_far_islands():
    towns = {"東京都狛江市岩戸北３": [35.63, 139.58], "東京都新宿区西新宿２": [35.69, 139.69],
             "東京都八丈島八丈町大賀郷": [33.11, 139.79], "東京都青梅市": None, "神奈川県川崎市": [35.5, 139.7]}
    west, south, east, north = stations.box(towns, "tokyo")
    assert (round(west, 2), round(south, 2), round(east, 2), round(north, 2)) == (139.43, 35.48, 139.84, 35.84)
    assert stations.box(towns, "osaka") is None


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
    towns = tmp_path / "towns.json"
    towns.write_text(json.dumps({"東京都江東区大島３": [35.69, 139.83]}, ensure_ascii=False))
    cache = tmp_path / "stations.json"
    for _ in range(2):
        stations.update(cache, towns, ["tokyo"], log=lambda *a: None)
    assert session.calls == 1
    assert stations.names(cache) == {"大島": ["おおじま", "Ojima"], "練馬": [None, "Nerima"]}
