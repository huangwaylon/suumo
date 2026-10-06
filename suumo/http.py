"""Polite HTTP client: one request at a time, delay + jitter, retries with backoff, and an automatic slow-down.

The delay is a floor between request starts. When SUUMO pushes back (an error status, a network error, or a run of
slow responses) the delay doubles, up to MAX_DELAY, and eases back to the base delay over the following successes.
"""
import random
import time

import requests

BASE = "https://suumo.jp"
USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) suumo-personal-crawler/0.2"
MAX_DELAY = 10.0      # seconds; the slow-down never waits longer than this between requests
SLOW_SECONDS = 5.0    # a response slower than this counts as slow...
SLOW_RUN = 3          # ...and this many in a row slow the crawl down
EASE = 0.95           # after each fast success the delay moves this factor back toward the base
RETRIES = 4           # attempts per request


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

    def _slow_down(self, why):
        new = min(MAX_DELAY, max(self.delay * 2, self.base * 2))
        if new > self.delay:
            self.log(f"  ~ {why}: slowing down, delay {self.delay:.2f}s -> {new:.2f}s")
            self.delay = new
            self.slowdowns += 1

    def _ease(self):
        if self.delay > self.base:
            self.delay = max(self.base, self.delay * EASE)

    def get(self, path):
        """GET BASE+path and return decoded HTML. Raises FileNotFoundError on 404 (listing gone)."""
        url = BASE + path
        for attempt in range(1, RETRIES + 1):
            wait = self.delay + random.uniform(0, self.delay * 0.5) - (time.monotonic() - self._last)
            if wait > 0:
                time.sleep(wait)
            self._last = start = time.monotonic()
            backoff = min(60, 5 * 2 ** (attempt - 1))
            try:
                r = self.session.get(url, timeout=40)
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
                if r.status_code in (404, 410):
                    raise FileNotFoundError(url)
                err = f"HTTP {r.status_code}"
                self._slow_down(err)  # 429, 403 (blocked?), 5xx: SUUMO is pushing back
                retry_after = r.headers.get("Retry-After", "")
                if retry_after.isdigit():
                    backoff = max(backoff, min(600, int(retry_after)))
            except requests.RequestException as e:
                err = repr(e)
                self._slow_down("network error")
            self.log(f"  ! {err} on {url} (attempt {attempt}/{RETRIES}), sleeping {backoff}s")
            time.sleep(backoff)
        raise RuntimeError(f"failed after {RETRIES} attempts: {url}")
