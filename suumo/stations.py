"""Station names in kana and English, from Wikidata (CC0), cached in geo/stations.json.

SUUMO names stations in kanji only ("二子玉川"), so kana and English searches find nothing. One SPARQL query per
prefecture fetches every station and tram stop inside the box its geocoded towns span, with the kana reading
(P1814) and the English label; keeping only stations in the box makes 白山 Tokyo's Hakusan, not a Shirayama
elsewhere. The cache ({name: [kana, english]}) is filled once per prefecture; delete it to refresh. Bus stops
aren't in Wikidata and stay kanji only.
"""
import json
import re
import unicodedata
from collections import Counter

import requests

from .archive import write_atomic
from .geo import load_cache
from .scope import PREFS

API = "https://query.wikidata.org/sparql"
QUERY = """SELECT ?ja ?kana ?en WHERE {
  SERVICE wikibase:box { ?s wdt:P625 ?at.
    bd:serviceParam wikibase:cornerSouthWest "Point(%f %f)"^^geo:wktLiteral;
                    wikibase:cornerNorthEast "Point(%f %f)"^^geo:wktLiteral. }
  ?s wdt:P31/wdt:P279* wd:Q55488; rdfs:label ?ja FILTER(lang(?ja) = "ja")
  OPTIONAL { ?s wdt:P1814 ?kana } OPTIONAL { ?s rdfs:label ?en FILTER(lang(?en) = "en") }
}"""
USER_AGENT = "suumo-site/1.0 (https://github.com/huangwaylon/suumo)"  # Wikidata asks for a contact
MARGIN = 0.15  # degrees around the towns: stations just over the border are named in listings too


def key(name):
    """The form names are matched in: '市ヶ谷', '市ケ谷駅', '市が谷' -> '市ケ谷'; '浦安駅 (千葉県)' -> '浦安'."""
    name = unicodedata.normalize("NFKC", name)
    for a, b in (("ヶ", "ケ"), ("ヵ", "ケ"), ("が", "ケ"), ("澤", "沢")):
        name = name.replace(a, b)
    return re.sub(r"\s*\(.*\)$", "", name).removesuffix("駅").removesuffix("停留場").strip()


def _english(label):
    """'Futako-Tamagawa Station' -> 'Futako-Tamagawa'."""
    return re.sub(r"\s+(Station|Stop|station|stop)\b.*$", "", re.sub(r"\s*\(.*\)$", "", label)).strip()


def box(towns, pref):
    """[west, south, east, north] around a prefecture's geocoded towns, leaving out far islands (>1° away)."""
    points = [p for t, p in towns.items() if p and t.startswith(PREFS[pref])]
    if not points:
        return None
    mid = sorted(points)[len(points) // 2]
    near = [p for p in points if abs(p[0] - mid[0]) < 1 and abs(p[1] - mid[1]) < 1]
    lats, lngs = [p[0] for p in near], [p[1] for p in near]
    return [min(lngs) - MARGIN, min(lats) - MARGIN, max(lngs) + MARGIN, max(lats) + MARGIN]


def fetch(session, area):
    """{key: [kana, english]} for the stations in area; the most common reading, the shortest English name."""
    r = session.get(API, params={"query": QUERY % tuple(area)}, headers={"Accept": "application/sparql-results+json"},
                    timeout=120)
    r.raise_for_status()
    kana, english = {}, {}
    for row in r.json()["results"]["bindings"]:
        k = key(row["ja"]["value"])
        if "kana" in row:
            kana.setdefault(k, Counter())[re.sub(r"(えき|ていりゅうじょう)$", "", row["kana"]["value"])] += 1
        if "en" in row:
            english.setdefault(k, set()).add(_english(row["en"]["value"]))
    return {k: [kana[k].most_common(1)[0][0] if k in kana else None,
                min(english.get(k, ()), key=lambda e: (len(e), e), default=None)] for k in kana.keys() | english.keys()}


def update(cache_path, towns_path, prefs, log=print):
    """Fill the cache for the prefectures not in it yet. An error is logged and retried on the next call."""
    cache = load_cache(cache_path)
    todo = [p for p in prefs if p not in cache.get("_prefs", [])]
    if not todo:
        return
    towns = load_cache(towns_path)
    session = requests.Session()
    session.headers["User-Agent"] = USER_AGENT
    for pref in todo:
        area = box(towns, pref)
        if not area:
            continue  # no towns geocoded yet
        try:
            found = fetch(session, area)
        except (requests.RequestException, ValueError, KeyError) as e:
            log(f"  ! station names for {pref}: {e!r}")
            continue
        cache.update(found)
        cache["_prefs"] = sorted({*cache.get("_prefs", []), pref})
        log(f"station names: {len(found):,} in {pref}")
    write_atomic(cache_path, (json.dumps(cache, ensure_ascii=False, indent=0, sort_keys=True) + "\n").encode())


def names(cache_path):
    """{key: [kana, english]} from the cache (empty if there is none)."""
    cache = load_cache(cache_path)
    cache.pop("_prefs", None)
    return cache
