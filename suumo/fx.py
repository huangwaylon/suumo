"""The yen-to-dollar rate for the site's USD prices: the ECB's daily reference rate (via frankfurter.dev, free, no
key), checked at most once a day into data/fx.json. Yen stays the source of truth; the site only divides by it."""
import json
import os
import time
from datetime import date
from pathlib import Path

import requests

from .archive import write_atomic

API = "https://api.frankfurter.dev/v1/latest?base=USD&symbols=JPY"


def update(path, log=print):
    """Refresh path ({"jpy_per_usd", "date": the rate's}) unless it was checked today (its mtime: an unchanged rate
    leaves the file, and git, alone). A failure is logged and the old rate kept."""
    path = Path(path)
    if path.exists() and date.fromtimestamp(path.stat().st_mtime) == date.today():
        return json.loads(path.read_text())
    try:
        r = requests.get(API, timeout=30)
        r.raise_for_status()
        body = r.json()
        fx = {"jpy_per_usd": float(body["rates"]["JPY"]), "date": body["date"]}
    except (requests.RequestException, ValueError, KeyError) as e:
        log(f"exchange rate: {e!r} (keeping the last one)")
        return json.loads(path.read_text()) if path.exists() else None
    text = json.dumps(fx, sort_keys=True) + "\n"
    if path.exists() and path.read_text() == text:
        os.utime(path, (time.time(), time.time()))  # checked today
    else:
        write_atomic(path, text.encode())
        log(f"exchange rate: 1 USD = {fx['jpy_per_usd']} JPY ({fx['date']})")
    return fx
