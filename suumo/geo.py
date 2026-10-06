"""Map coordinates for listings, at town (丁目) level, from 国土地理院's address search.

Listings carry addresses, not coordinates, and SUUMO shows most only to the 丁目 ("東京都狛江市岩戸北３"). Each
distinct town is looked up once and cached in geo/towns.json (git-tracked, sorted): {town: [lat, lng] or null}.
Later runs only look up towns they haven't seen. One request at a time, GEO_DELAY apart.
"""
import json
import re
import time
from pathlib import Path

import requests

from .archive import write_atomic
from .http import USER_AGENT
from .scope import PREFS

API = "https://msearch.gsi.go.jp/address-search/AddressSearch"
GEO_DELAY = 1.0
SAVE_EVERY = 100  # towns between cache saves (an interrupted fill keeps its progress)

_block = re.compile(r"[0-9][0-9\-－]*.*$")   # 番地 / 号 in ASCII digits after the 丁目 (full-width digit)
_wide_block = re.compile(r"([０-９])[\-－‐番].*$")  # ...or in full-width digits: 下連雀３-４１－１２ -> 下連雀３
_wide_number = re.compile(r"[０-９]{2,}.*$")          # a 番地 without 丁目: 上代継６０－１ -> 上代継


def town_of(address, pref):
    """'東京都狛江市東和泉２-20-20' -> '東京都狛江市東和泉２'; prefixes the prefecture when SUUMO omits it."""
    if not address:
        return None
    town = _wide_number.sub("", _wide_block.sub(r"\1", _block.sub("", address))).rstrip("-－ ")
    name = PREFS.get(pref, "")
    if name and not town.startswith(name):
        town = name + town
    return town or None


def _municipality(town):
    """'東京都狛江市岩戸北３' -> '東京都狛江市' (to check that a result is in the right place)."""
    m = re.match(r"(.+?[都道府県].+?[市区町村])", town)
    return _same_spelling(m.group(1) if m else town)


def _same_spelling(s):
    """The geocoder's titles drop the 郡 (神奈川県足柄下郡箱根町 -> 神奈川県箱根町) and write ケ for ヶ."""
    return re.sub(r"^(.+?[都道府県])[^都道府県市区町村]+?郡", r"\1", s).replace("ヶ", "ケ")


def load_cache(path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def save_cache(path, cache):
    write_atomic(path, (json.dumps(cache, ensure_ascii=False, indent=0, sort_keys=True) + "\n").encode())


def lookup(session, town):
    """[lat, lng] of the first result in the same municipality, or None (not found / elsewhere)."""
    r = session.get(API, params={"q": town}, timeout=30)
    r.raise_for_status()
    for hit in r.json():
        if _same_spelling(hit["properties"]["title"]).startswith(_municipality(town)):
            lng, lat = hit["geometry"]["coordinates"]
            return [round(lat, 6), round(lng, 6)]
    return None


def towns_in(data_dir):
    """Every town of the active listings in data/."""
    towns = set()
    for pref_dir in Path(data_dir).iterdir():
        if not pref_dir.is_dir() or pref_dir.name == "events":
            continue
        for path in pref_dir.glob("*/*.jsonl"):
            if path.parent.name == "removed":
                continue
            for line in path.read_text(encoding="utf-8").splitlines():
                town = town_of(json.loads(line).get("address"), pref_dir.name)
                if town:
                    towns.add(town)
    return towns


def geocode(data_dir, cache_path, budget_seconds=None, log=print):
    """Look up the towns not in the cache yet. Errors are logged and retried on the next call."""
    cache = load_cache(cache_path)
    todo = sorted(towns_in(data_dir) - set(cache))
    if not todo:
        return 0
    log(f"geocoding {len(todo):,} new towns (~{len(todo) * GEO_DELAY / 60:.0f} min)")
    session = requests.Session()
    session.headers["User-Agent"] = USER_AGENT
    deadline = time.monotonic() + budget_seconds if budget_seconds else None
    done = failures = 0
    for town in todo:
        if deadline and time.monotonic() > deadline:
            break
        try:
            cache[town] = lookup(session, town)
            done += 1
        except (requests.RequestException, ValueError, KeyError) as e:
            failures += 1
            log(f"  ! {town}: {e!r}")
            if failures >= 10:
                log("  geocoding stopped after 10 errors; the rest is retried next time")
                break
        if done and done % SAVE_EVERY == 0:
            save_cache(cache_path, cache)
            log(f"  geocoded {done:,}/{len(todo):,}")
        time.sleep(GEO_DELAY)
    save_cache(cache_path, cache)
    missing = sum(1 for t in todo if t in cache and cache[t] is None)
    log(f"geocoded {done:,} towns ({missing} not found)")
    return done
