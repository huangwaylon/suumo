"""Station names in kana and English, from Wikidata (CC0), cached in geo/stations.json.

SUUMO names stations in kanji only ("二子玉川"), so kana and English searches find nothing. One SPARQL query per
prefecture fetches every station and tram stop inside the box the stations named in its listings span (their
places from rail.json), with the kana reading (P1814) and the English label; keeping only stations in the box makes
白山 Tokyo's Hakusan, not a Shirayama elsewhere. The cache ({name: [kana, english]}) is filled once per prefecture;
delete it to refresh. Bus stops aren't in Wikidata and stay kanji only.
"""
import json
import re
import unicodedata
from collections import Counter
from pathlib import Path

import requests

from .catalog import listing_station_keys, station_area
from .catalog import station_key as key
from .geo import load_cache, save_cache

API = "https://query.wikidata.org/sparql"
QUERY = """SELECT ?ja ?kana ?en WHERE {
  SERVICE wikibase:box { ?s wdt:P625 ?at.
    bd:serviceParam wikibase:cornerSouthWest "Point(%f %f)"^^geo:wktLiteral;
                    wikibase:cornerNorthEast "Point(%f %f)"^^geo:wktLiteral. }
  VALUES ?kind { wd:Q55488 wd:Q2175765 }  # railway station, tram stop (not a subclass of station)
  ?s wdt:P31/wdt:P279* ?kind; rdfs:label ?ja FILTER(lang(?ja) = "ja")
  OPTIONAL { ?s wdt:P1814 ?kana } OPTIONAL { ?s rdfs:label ?en FILTER(lang(?en) = "en") }
}"""
USER_AGENT = "suumo-site/1.0 (https://github.com/huangwaylon/suumo)"  # Wikidata asks for a contact


def _english(label):
    """'Futako-Tamagawa Station' -> 'Futako-Tamagawa'; 'Shin‐Yokohama' -> 'Shin-Yokohama'. None for a label that
    isn't a name in Latin letters (vandalism, 'Category:...')."""
    name = re.sub(r"\s+(Station|Stop|station|stop)\b.*$", "", re.sub(r"\s*\(.*\)$", "", label)).strip()
    name = re.sub("[\u2010-\u2015]", "-", name)
    latin = all(c.isascii() or unicodedata.name(c, "").startswith("LATIN") or unicodedata.category(c)[0] in "PZ"
                for c in name)
    return name if latin and ":" not in name and re.search("[A-Za-z]", name) else None


def area(rail_stations, data_dir, pref):
    """The box the stations named in a prefecture's listings span (positions from rail.json), or None."""
    keys = listing_station_keys(json.loads(line) for f in (Path(data_dir) / pref).glob("*/*.jsonl")
                                for line in f.read_text(encoding="utf-8").splitlines())
    return station_area([(lat, lng) for name, lat, lng, _ in rail_stations for _ in range(keys[key(name)])])


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
        if "en" in row and (en := _english(row["en"]["value"])):
            english.setdefault(k, set()).add(en)
    return {k: [kana[k].most_common(1)[0][0] if k in kana else None,
                min(english.get(k, ()), key=lambda e: (len(e), e), default=None)] for k in kana.keys() | english.keys()}


def update(cache_path, rail_path, prefs, data_dir, log=print):
    """Fill the cache for the prefectures not in it yet. An error is logged and retried on the next call."""
    cache = load_cache(cache_path)
    todo = [p for p in prefs if p not in cache.get("_prefs", [])]
    if not todo or not Path(rail_path).exists():
        return
    rail_stations = json.loads(Path(rail_path).read_text())["stations"]
    session = requests.Session()
    session.headers["User-Agent"] = USER_AGENT
    for pref in todo:
        box = area(rail_stations, data_dir, pref)
        if not box:
            continue  # no listings yet
        try:
            found = fetch(session, box)
        except (requests.RequestException, ValueError, KeyError) as e:
            log(f"  ! station names for {pref}: {e!r}")
            continue
        cache.update(found)
        cache["_prefs"] = sorted({*cache.get("_prefs", []), pref})
        log(f"station names: {len(found):,} in {pref}")
    save_cache(cache_path, cache)


def names(cache_path):
    """{key: [kana, english]} from the cache (empty if there is none)."""
    cache = load_cache(cache_path)
    cache.pop("_prefs", None)
    return cache
