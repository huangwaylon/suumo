"""Railway lines and stations for the site's map, cached in geo/rail.json (git-tracked).

Geometry and stations: 国土数値情報 鉄道データ N02 (MLIT, CC BY 4.0), every operator. Line colours: the official
ones from 公共交通オープンデータセンター (ODPT, odpt:Railway; Tokyo Metro, Toei and a few others), else a colour
per operator. Kept to REGION (Tokyo and the prefectures around it) and simplified (about 10 m) so the browser
loads it quickly. Rebuild with `python -m suumo rail` when MLIT publishes a new year (N02_URL).

  {"lines": [[operator, line, colour, [[[lng, lat], ...], ...]]], "stations": [[name, lat, lng, [line, ...]]]}
"""
import io
import json
import re
import zipfile
from collections import defaultdict

import requests

from .archive import write_atomic

N02_URL = "https://nlftp.mlit.go.jp/ksj/gml/data/N02/N02-24/N02-24_GML.zip"
ODPT_PUBLIC = "https://api-public.odpt.org/api/v4/odpt:Railway"   # Toei (CC BY 4.0, no key)
ODPT = "https://api.odpt.org/api/v4/odpt:Railway"                 # the rest (ODPT basic licence, ODPT_TOKEN)
REGION = (137.3, 34.5, 141.0, 37.2)  # west, south, east, north: Tokyo, Kanagawa ... Nagano, Shizuoka
TOLERANCE = 0.0001                    # degrees (~10 m): points closer to the line than this are dropped
ATTRIBUTION = "国土数値情報（鉄道データ）（国土交通省）, 公共交通オープンデータセンター"
OPERATOR_COLOURS = {  # brand colours, for lines ODPT has none for
    "東日本旅客鉄道": "#2e8b57", "東海旅客鉄道": "#f08300", "東武鉄道": "#0f6eb4", "西武鉄道": "#2a5caa",
    "京成電鉄": "#1a4d9e", "京浜急行電鉄": "#e5171f", "京王電鉄": "#dd0077", "小田急電鉄": "#1e8bc3",
    "東急電鉄": "#e60012", "相模鉄道": "#004e9e", "東京地下鉄": "#149dd3", "東京都": "#3a8a3a",
    "横浜市": "#00a95f", "首都圏新都市鉄道": "#d71920",
}
OTHER = "#7d8693"


def _inside(pt):
    w, s, e, n = REGION
    return w <= pt[0] <= e and s <= pt[1] <= n


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


def odpt_colours(token=None, log=print):
    """ODPT line title -> colour, from the railways that have one."""
    colours = {}
    for url, params in ((ODPT_PUBLIC, {}), (ODPT, {"acl:consumerKey": token})):
        if url == ODPT and not token:
            continue
        try:
            r = requests.get(url, params=params, timeout=60)
            r.raise_for_status()
            colours.update({x["dc:title"]: x["odpt:color"] for x in r.json() if x.get("odpt:color")})
        except (requests.RequestException, ValueError, KeyError) as e:
            log(f"  ! ODPT colours from {url}: {e!r}")
    return colours


def build_rail(sections, stations, colours):
    """N02 GeoJSON features -> the rail.json structure (pure: tests call it with small inputs)."""
    by_line = defaultdict(list)
    for f in sections:
        coords = f["geometry"]["coordinates"]
        if any(_inside(p) for p in coords):
            p = f["properties"]
            by_line[(p["N02_004"], p["N02_003"])].append([[round(x, 5), round(y, 5)] for x, y in simplify(coords)])
    def colour(op, title):  # a branch (丸ノ内線分岐線) takes its line's colour
        return colours.get(title) or colours.get(title.removesuffix("分岐線")) or OPERATOR_COLOURS.get(op, OTHER)

    lines = [[op, line_title(name), colour(op, line_title(name)), parts]
             for (op, name), parts in sorted(by_line.items())]
    # one marker per station (N02_005g groups a station's records on each line), at the middle of its platforms
    groups = defaultdict(lambda: {"name": None, "points": [], "lines": set()})
    for f in stations:
        coords = f["geometry"]["coordinates"]
        mid = coords[len(coords) // 2]
        if not _inside(mid):
            continue
        p = f["properties"]
        g = groups[p.get("N02_005g") or p["N02_005c"]]
        g["name"] = p["N02_005"]
        g["points"].append(mid)
        g["lines"].add(line_title(p["N02_003"]))
    out_stations = [[g["name"], round(sum(y for _, y in g["points"]) / len(g["points"]), 5),
                     round(sum(x for x, _ in g["points"]) / len(g["points"]), 5), sorted(g["lines"])]
                    for _, g in sorted(groups.items())]
    return {"attribution": ATTRIBUTION, "lines": lines, "stations": out_stations}


def update(path, token=None, log=print):
    """Download N02 (about 13 MB), cut it to REGION and write path."""
    r = requests.get(N02_URL, timeout=300)
    r.raise_for_status()
    with zipfile.ZipFile(io.BytesIO(r.content)) as z:
        def layer(kind):
            name = next(n for n in z.namelist() if n.startswith("UTF-8/") and n.endswith(f"_{kind}.geojson"))
            return json.loads(z.read(name))["features"]
        sections, stations = layer("RailroadSection"), layer("Station")
    rail = build_rail(sections, stations, odpt_colours(token, log))
    write_atomic(path, (json.dumps(rail, ensure_ascii=False, separators=(",", ":")) + "\n").encode())
    log(f"rail: {len(rail['lines'])} lines, {len(rail['stations'])} stations → {path}")
    return rail
