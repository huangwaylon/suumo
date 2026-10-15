"""Polite HTTP client: one request at a time, delay + jitter, retries with backoff, and an automatic slow-down.

The delay is a floor between request starts. When SUUMO pushes back (an error status, a network error, or a run of
slow responses) the delay doubles, up to MAX_DELAY, and eases back to the base delay over the following successes.
A request that never reached SUUMO (the proxy failed, no connection could be made, the name didn't resolve) slows
down too but counts in `net_errors`, not `slowdowns`; when every attempt failed that way, `get` raises NetworkError.
"""
import random
import time

import requests
from urllib3.exceptions import NewConnectionError

BASE = "https://suumo.jp"
USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) suumo-personal-crawler/0.2"
MAX_DELAY = 10.0      # seconds; the slow-down never waits longer than this between requests
SLOW_SECONDS = 5.0    # a response slower than this counts as slow...
SLOW_RUN = 3          # ...and this many in a row slow the crawl down
EASE = 0.95           # after each fast success the delay moves this factor back toward the base
RETRIES = 4           # attempts per request


class NetworkError(RuntimeError):
    """No attempt reached SUUMO: our network (or proxy) is down. Callers don't hold it against the page."""


def unreachable(e):
    """Whether a requests exception means the request never reached SUUMO: a proxy error, or a connection that
    couldn't be made (NewConnectionError, incl. name resolution). Timeouts, resets, decoding errors: maybe SUUMO."""
    if isinstance(e, requests.exceptions.ProxyError):
        return True
    return isinstance(e, requests.ConnectionError) and isinstance(
        getattr(e.args[0] if e.args else None, "reason", None), NewConnectionError)


class Client:
    def __init__(self, delay=1.5, log=print):
        self.base = self.delay = delay
        self.log = log
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": USER_AGENT, "Accept-Language": "ja,en;q=0.8"})
        self._last = 0.0
        self._slow = 0
        self.requests_made = 0
        self.bytes_downloaded = 0
        self.slowdowns = 0
        self.pushbacks = 0    # 429 / 403: SUUMO asking us to slow down (a 5xx or a network error may be a hiccup)
        self.net_errors = 0   # attempts that never reached SUUMO (they slow down, but aren't in slowdowns)

    def _slow_down(self, why, count=True):
        new = min(MAX_DELAY, max(self.delay * 2, self.base * 2))
        if new > self.delay:
            self.log(f"  ~ {why}: slowing down, delay {self.delay:.2f}s -> {new:.2f}s")
            self.delay = new
            self.slowdowns += count

    def _ease(self):
        if self.delay > self.base:
            self.delay = max(self.base, self.delay * EASE)

    def get(self, path):
        """GET BASE+path and return decoded HTML. Raises FileNotFoundError when it's gone: 404/410, or a redirect
        (SUUMO sends an ended listing to its building's /library/ page; redirects aren't followed, so only the paths
        we chose are fetched), with the status and the redirect's Location as its message. NetworkError when no
        attempt reached SUUMO, RuntimeError for other failures."""
        url = BASE + path
        offline = 0  # attempts that never reached SUUMO
        for attempt in range(1, RETRIES + 1):
            wait = self.delay + random.uniform(0, self.delay * 0.5) - (time.monotonic() - self._last)
            if wait > 0:
                time.sleep(wait)
            self._last = start = time.monotonic()
            backoff = min(60, 5 * 2 ** (attempt - 1))
            try:
                r = self.session.get(url, timeout=40, allow_redirects=False)
                self.requests_made += 1
                if r.status_code == 200:
                    self.bytes_downloaded += len(r.content)
                    if time.monotonic() - start > SLOW_SECONDS:
                        self._slow += 1
                        if self._slow >= SLOW_RUN:
                            self._slow_down(f"{SLOW_RUN} slow responses")
                            self._slow = 0
                    else:
                        self._slow = 0
                        self._ease()
                    return r.content.decode("utf-8", errors="replace")
                if r.status_code in (404, 410) or 300 <= r.status_code < 400:
                    raise FileNotFoundError(f"{r.status_code} {r.headers.get('Location', '')}".strip())
                err = f"HTTP {r.status_code}"
                self.pushbacks += r.status_code in (403, 429)
                self._slow_down(err)  # 429, 403 (blocked?), 5xx: SUUMO is pushing back
                retry_after = r.headers.get("Retry-After", "")
                if retry_after.isdigit():
                    backoff = max(backoff, min(600, int(retry_after)))
            except requests.RequestException as e:
                err = repr(e)
                if unreachable(e):
                    offline += 1
                    self.net_errors += 1
                    self._slow_down("network unreachable", count=False)
                else:
                    self._slow_down("network error")
            if attempt == RETRIES:  # no point waiting after the last attempt
                self.log(f"  ! {err} on {url} (attempt {attempt}/{RETRIES})")
                break
            self.log(f"  ! {err} on {url} (attempt {attempt}/{RETRIES}), sleeping {backoff}s")
            time.sleep(backoff)
        if offline == RETRIES:
            raise NetworkError(f"network unreachable ({RETRIES} attempts): {url}")
        raise RuntimeError(f"failed after {RETRIES} attempts: {url}")
