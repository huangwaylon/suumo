"""Railway lines and stations for the site's maps, cached in geo/rail.json (git-tracked).

Geometry and stations: 国土数値情報 鉄道データ N02 (MLIT, CC BY 4.0), every operator in Japan. Line colours and
English names: 公共交通オープンデータセンター (ODPT, odpt:Railway / odpt:Operator) from its open, basic and challenge
APIs (keys ODPT_TOKEN and ODPT_CHALLENGE_TOKEN in .env; the challenge data is licensed for the ODPT challenge only),
else a colour per operator. Lines are simplified (about 10 m) so the browser loads them quickly. Rebuild with
`python -m suumo rail` when MLIT publishes a new year (N02_URL).

  {"attribution": ..., "operators": [[name, english, colour, short name]],
   "lines": [[operator index, name, english, colour, [[[lng, lat], ...], ...]]],
   "stations": [[name, lat, lng, [line index, ...]]]}
"""
import io
import json
import re
import zipfile
from collections import Counter, defaultdict

import requests

from .archive import write_atomic

N02_URL = "https://nlftp.mlit.go.jp/ksj/gml/data/N02/N02-24/N02-24_GML.zip"
ODPT_APIS = (  # (base, key setting): open, basic licence, challenge licence
    ("https://api-public.odpt.org/api/v4/", None),
    ("https://api.odpt.org/api/v4/", "ODPT_TOKEN"),
    ("https://api-challenge.odpt.org/api/v4/", "ODPT_CHALLENGE_TOKEN"),
)
TOLERANCE = 0.0001  # degrees (~10 m): points closer to the line than this are dropped
ATTRIBUTION = "国土数値情報（鉄道データ）（国土交通省）, 公共交通オープンデータセンター"
# ODPT operator ids whose title differs from N02's operator name
OPERATORS = {"JR-East": "東日本旅客鉄道", "JR-Central": "東海旅客鉄道", "JR-West": "西日本旅客鉄道",
             "JR-Shikoku": "四国旅客鉄道", "JR-Kyushu": "九州旅客鉄道", "JR-Hokkaido": "北海道旅客鉄道",
             "Keikyu": "京浜急行電鉄", "TokyoMetro": "東京地下鉄", "Toei": "東京都", "YokohamaMunicipal": "横浜市",
             "Choshi": "銚子電気鉄道", "Jomo": "上毛電気鉄道", "Kominato": "小湊鐵道", "SendaiMunicipal": "仙台市"}
# N02 line names that ODPT titles differently, per operator
LINES = {("東日本旅客鉄道", "中央線"): "中央線快速", ("東日本旅客鉄道", "総武線"): "総武本線",
         ("東日本旅客鉄道", "東北線"): "宇都宮線", ("東日本旅客鉄道", "根岸線"): "京浜東北線・根岸線",
         ("東日本旅客鉄道", "川越線"): "埼京線・川越線", ("東日本旅客鉄道", "赤羽線"): "埼京線・川越線",
         ("東武鉄道", "東上本線"): "東上線", ("東武鉄道", "野田線"): "東武アーバンパークライン",
         ("京浜急行電鉄", "本線"): "京急本線", ("相模鉄道", "本線"): "相鉄本線",
         ("相模鉄道", "相鉄いずみ野線"): "いずみ野線"}
OPERATOR_COLOURS = {  # brand colours, for lines ODPT has none for
    "東日本旅客鉄道": "#2e8b57", "東海旅客鉄道": "#f08300", "西日本旅客鉄道": "#0072bc", "九州旅客鉄道": "#e60012",
    "北海道旅客鉄道": "#38b48b", "四国旅客鉄道": "#00acd1", "東武鉄道": "#0f6eb4", "西武鉄道": "#2a5caa",
    "京成電鉄": "#1a4d9e", "京浜急行電鉄": "#e5171f", "京王電鉄": "#dd0077", "小田急電鉄": "#1e8bc3",
    "東急電鉄": "#e60012", "相模鉄道": "#004e9e", "東京地下鉄": "#149dd3", "東京都": "#3a8a3a",
    "横浜市": "#00a95f", "首都圏新都市鉄道": "#d71920",
}
OTHER = "#7d8693"
SHORT = {"九州旅客鉄道": "JR九州", "北海道旅客鉄道": "JR北海道", "四国旅客鉄道": "JR四国", "東京都": "都営",
         "東京地下鉄": "東京メトロ", "横浜市": "横浜市営地下鉄", "近畿日本鉄道": "近鉄", "名古屋鉄道": "名鉄",
         "大阪市高速電気軌道": "Osaka Metro"}  # names people use, where ODPT gives none


def simplify(points, tol=TOLERANCE):
    """Douglas-Peucker: the points that keep the line within tol of the original."""
    if len(points) < 3:
        return points
    keep, stack = {0, len(points) - 1}, [(0, len(points) - 1)]
    while stack:
        a, b = stack.pop()
        (x1, y1), (x2, y2) = points[a], points[b]
        dx, dy = x2 - x1, y2 - y1
        norm = (dx * dx + dy * dy) ** 0.5 or 1e-12
        far, at = 0, None
        for i in range(a + 1, b):
            d = abs(dy * points[i][0] - dx * points[i][1] + x2 * y1 - y2 * x1) / norm
            if d > far:
                far, at = d, i
        if at is not None and far > tol:
            keep.add(at)
            stack += [(a, at), (at, b)]
    return [points[i] for i in sorted(keep)]


def line_title(n02_name):
    """'4号線丸ノ内線' -> '丸ノ内線' (how ODPT titles it)."""
    return re.sub(r"^\d+号線", "", n02_name)


def odpt(keys, log=print):
    """From every ODPT API there's a key for: {(N02 operator, line title): (colour, english)},
    {N02 operator: (english, ODPT's shorter name: JR東日本 for 東日本旅客鉄道)}."""
    lines, operators = {}, {}
    for base, setting in ODPT_APIS:
        if setting and not keys.get(setting):
            continue
        params = {"acl:consumerKey": keys[setting]} if setting else {}
        try:
            ops = requests.get(base + "odpt:Operator", params=params, timeout=60).json()
            titles = {o["owl:sameAs"]: o.get("dc:title") for o in ops}
            for o in ops:
                name = OPERATORS.get(o["owl:sameAs"].split(":")[-1], o.get("dc:title"))
                operators[name] = (o.get("odpt:operatorTitle", {}).get("en"), o.get("dc:title") or name)
            for r in requests.get(base + "odpt:Railway", params=params, timeout=60).json():
                op = OPERATORS.get(r["odpt:operator"].split(":")[-1], titles.get(r["odpt:operator"]))
                colour, english = lines.get((op, r["dc:title"]), (None, None))
                lines[(op, r["dc:title"])] = (r.get("odpt:color") or colour,
                                              r.get("odpt:railwayTitle", {}).get("en") or english)
        except (requests.RequestException, ValueError, KeyError, TypeError, AttributeError) as e:
            log(f"  ! ODPT {base}: {e!r}")
    return lines, operators


def build_rail(sections, stations, lines_info, operators_en):
    """N02 GeoJSON features -> the rail.json structure (pure: tests call it with small inputs)."""
    by_line = defaultdict(list)
    for f in sections:
        p = f["properties"]
        by_line[(p["N02_004"], p["N02_003"])].append(
            [[round(x, 5), round(y, 5)] for x, y in simplify(f["geometry"]["coordinates"])])
    size = Counter(op for op, _ in by_line)
    ops = sorted(size, key=lambda o: (-size[o], o))  # most lines first (the operator list on the map)
    op_index = {o: i for i, o in enumerate(ops)}
    lines, line_index = [], {}
    for (op, name), parts in sorted(by_line.items(), key=lambda kv: (op_index[kv[0][0]], kv[0][1])):
        title = line_title(name)
        known = LINES.get((op, title), title)
        info = lines_info.get((op, known)) or lines_info.get((op, known.removesuffix("分岐線")))  # a branch: its line's
        colour, english = info or (None, None)
        line_index[(op, name)] = len(lines)
        lines.append([op_index[op], title, english, colour or OPERATOR_COLOURS.get(op, OTHER), parts])
    # one marker per station (N02_005g groups a station's records on each line), at the middle of its platforms
    groups = defaultdict(lambda: {"name": None, "points": [], "lines": set()})
    for f in stations:
        p, coords = f["properties"], f["geometry"]["coordinates"]
        g = groups[p.get("N02_005g") or p["N02_005c"]]
        g["name"] = p["N02_005"]
        g["points"].append(coords[len(coords) // 2])
        if (p["N02_004"], p["N02_003"]) in line_index:
            g["lines"].add(line_index[(p["N02_004"], p["N02_003"])])
    out_stations = [[g["name"], round(sum(y for _, y in g["points"]) / len(g["points"]), 5),
                     round(sum(x for x, _ in g["points"]) / len(g["points"]), 5), sorted(g["lines"])]
                    for _, g in sorted(groups.items())]
    operators = [[o, *(operators_en.get(o) or (None, None))[:1], OPERATOR_COLOURS.get(o, OTHER),
                  SHORT.get(o) or (operators_en.get(o) or (None, o))[1]] for o in ops]
    return {"attribution": ATTRIBUTION, "operators": operators, "lines": lines, "stations": out_stations}


def update(path, keys, log=print):
    """Download N02 (about 13 MB), add ODPT's colours and names, write path. keys: {setting: key}."""
    r = requests.get(N02_URL, timeout=300)
    r.raise_for_status()
    with zipfile.ZipFile(io.BytesIO(r.content)) as z:
        def layer(kind):
            name = next(n for n in z.namelist() if n.startswith("UTF-8/") and n.endswith(f"_{kind}.geojson"))
            return json.loads(z.read(name))["features"]
        sections, stations = layer("RailroadSection"), layer("Station")
    rail = build_rail(sections, stations, *odpt(keys, log))
    write_atomic(path, (json.dumps(rail, ensure_ascii=False, separators=(",", ":")) + "\n").encode())
    own = sum(1 for line in rail["lines"] if line[3] not in OPERATOR_COLOURS.values() and line[3] != OTHER)
    log(f"rail: {len(rail['operators'])} operators, {len(rail['lines'])} lines ({own} in their own colour), "
        f"{len(rail['stations'])} stations → {path}")
    return rail
