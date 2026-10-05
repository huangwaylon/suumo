"""Polite HTTP client: one request at a time, fixed delay + jitter, retries with backoff."""
import random
import time

import requests

BASE = "https://suumo.jp"
USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) suumo-personal-crawler/0.2"


class Client:
    def __init__(self, delay=1.5, max_retries=4):
        self.delay = delay
        self.max_retries = max_retries
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": USER_AGENT, "Accept-Language": "ja,en;q=0.8"})
        self._last = 0.0
        self.requests_made = 0
        self.bytes_downloaded = 0

    def get(self, path):
        """GET BASE+path and return decoded HTML. Raises FileNotFoundError on 404 (listing gone)."""
        url = BASE + path
        for attempt in range(1, self.max_retries + 1):
            wait = self.delay + random.uniform(0, self.delay * 0.5) - (time.monotonic() - self._last)
            if wait > 0:
                time.sleep(wait)
            self._last = time.monotonic()
            try:
                r = self.session.get(url, timeout=40)
                self.requests_made += 1
                if r.status_code == 200:
                    self.bytes_downloaded += len(r.content)
                    return r.content.decode("utf-8", errors="replace")
                if r.status_code in (404, 410):
                    raise FileNotFoundError(url)
                err = f"HTTP {r.status_code}"
            except requests.RequestException as e:
                err = repr(e)
            backoff = min(60, 5 * 2 ** (attempt - 1))
            print(f"  ! {err} on {url} (attempt {attempt}/{self.max_retries}), sleeping {backoff}s", flush=True)
            time.sleep(backoff)
        raise RuntimeError(f"failed after {self.max_retries} attempts: {url}")
