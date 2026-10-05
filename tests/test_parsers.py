"""Parser edge cases seen on real SUUMO pages."""
import pytest

from suumo.detail import _floor, _monthly_sum, _ratios, _road, _structure, _tenure
from suumo.parse import parse_prices, parse_station, town


@pytest.mark.parametrize("s, want", [
    ("8702万8000円", (87028000, 87028000)),
    ("1945万円※権利金含む130万円", (19450000, 19450000)),
    ("1億3180万円～1億5000万円", (131800000, 150000000)),
    ("4990万円・5490万円", (49900000, 54900000)),
    ("25億円", (2500000000, 2500000000)),
    ("未定", (None, None)),
])
def test_prices(s, want):
    assert parse_prices(s) == want


@pytest.mark.parametrize("s, want", [
    ("東京メトロ南北線「本駒込」歩1分", {"line": "東京メトロ南北線", "name": "本駒込", "walk": 1}),
    ("ＪＲ中央線「吉祥寺」バス12分停歩3分", {"line": "ＪＲ中央線", "name": "吉祥寺", "bus": 12, "walk": 3}),
    ("ＪＲ常磐線「亀有」バス6分亀有新道歩1分", {"line": "ＪＲ常磐線", "name": "亀有", "bus": 6, "walk": 1}),
    ("小田急線/経堂 徒歩6分", {"line": "小田急線", "name": "経堂", "walk": 6}),
])
def test_stations(s, want):
    assert parse_station(s) == want


@pytest.mark.parametrize("s, want", [
    ("RC22階地下1階建", ("RC", 22, 1)),
    ("11階/SRC15階建", ("SRC", 15, 0)),
    ("木造2階建（軸組工法）", ("木造", 2, 0)),
    ("木造 地上2階", ("木造", 2, 0)),
    ("2階建", (None, 2, 0)),
])
def test_structure(s, want):
    assert _structure(s) == want


@pytest.mark.parametrize("s, want", [
    ("所有権", "所有権"),
    ("一部地上権（旧）、借地期間残存65年9ヶ月、借地権割合32％", "地上権"),
    ("賃借権（旧）、借地期間残存66年3ヶ月", "旧法借地権"),
    ("一般定期借地権（賃借権）、借地期間残存36年", "定期借地権"),
    ("普通借地権", "普通借地権"),
])
def test_tenure(s, want):
    assert _tenure(s) == want


def test_misc():
    assert _floor("11階") == 11 and _floor("地下1階") == -1
    assert _ratios("建ペい率：50％、容積率：100％") == (50, 100) and _ratios("60％・150％") == (60, 150)
    assert _monthly_sum("町会費：100円／月、インターネット定額料金：770円／月、保証金：350万円") == 870
    assert _road("無、北4ｍ幅（接道幅8ｍ）") == {"dir": "北", "width_m": 4.0, "text": "無、北4ｍ幅（接道幅8ｍ）"}
    assert town("東京都品川区戸越３-9-16", "品川区") == "戸越３-9-16"
    assert town("千葉県勝浦市墨名", "勝浦市") == "墨名"
