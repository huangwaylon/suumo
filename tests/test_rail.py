"""Railway lines and stations for the map (suumo.rail), from synthetic N02 features."""
from suumo import rail


def section(op, line, coords):
    return {"properties": {"N02_003": line, "N02_004": op}, "geometry": {"coordinates": coords}}


def station(name, line, group, coords):
    return {"properties": {"N02_003": line, "N02_004": "x", "N02_005": name, "N02_005c": group, "N02_005g": group},
            "geometry": {"coordinates": coords}}


def test_simplify_keeps_the_shape():
    straight = [[139.0 + i * 0.001, 35.0] for i in range(10)]
    assert rail.simplify(straight) == [straight[0], straight[-1]]
    corner = [[139.0, 35.0], [139.01, 35.0], [139.01, 35.01]]
    assert rail.simplify(corner) == corner


def test_lines_get_official_colours_and_stations_one_marker():
    sections = [section("東京地下鉄", "4号線丸ノ内線", [[139.70, 35.69], [139.71, 35.69]]),
                section("東京地下鉄", "4号線丸ノ内線分岐線", [[139.66, 35.69], [139.67, 35.69]]),
                section("東日本旅客鉄道", "山手線", [[139.70, 35.69], [139.70, 35.70]]),
                section("どこか鉄道", "遠い線", [[130.0, 33.0], [130.1, 33.0]])]                 # outside REGION
    stations = [station("新宿", "4号線丸ノ内線", "1", [[139.700, 35.690], [139.702, 35.690]]),
                station("新宿", "山手線", "1", [[139.700, 35.692], [139.700, 35.694]])]
    out = rail.build_rail(sections, stations, {"丸ノ内線": "#F62E36"})
    assert [(op, line, colour) for op, line, colour, _ in out["lines"]] == [
        ("東京地下鉄", "丸ノ内線", "#F62E36"),
        ("東京地下鉄", "丸ノ内線分岐線", "#F62E36"),  # a branch: its line's colour
        ("東日本旅客鉄道", "山手線", rail.OPERATOR_COLOURS["東日本旅客鉄道"])]
    assert out["stations"] == [["新宿", 35.692, 139.701, ["丸ノ内線", "山手線"]]]          # one marker, both lines
