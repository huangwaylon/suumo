// Service worker: repeat visits show the search indexes at once (from this browser's cache) while a fresh copy is
// fetched; the page is told when one changed. The page, scripts and data/index.json come from the network first
// (the cache only when offline), so code and data stay in step. VERSION is set by the build (site.py) from the
// page's files: a new version drops every cached file, except the photos.
// Photos (SUUMO's img01.suumo.com) are cache-first, in a cache of their own kept across versions and capped by
// count (see below), least recently used out first; the favorites' photos are pinned (the page sends them) in
// another cache that is never trimmed.
const VERSION = "__VERSION__";
const CACHE = `suumo-${VERSION}`;
const SWR = /\/data\/(p\/[^/]+|rail)\.json$/;  // stale-while-revalidate: the big data files
const PHOTOS = "suumo-photos", PINS = "suumo-pins", KEEP = [PHOTOS, PINS];

self.addEventListener("install", () => self.skipWaiting());
self.addEventListener("activate", (e) => e.waitUntil((async () => {
  for (const key of await caches.keys()) if (key !== CACHE && !KEEP.includes(key)) await caches.delete(key);
  await self.clients.claim();
})()));

self.addEventListener("fetch", (e) => {
  const url = new URL(e.request.url);
  if (e.request.method === "GET" && url.hostname === "img01.suumo.com") { e.respondWith(photo(e)); return; }
  if (e.request.method !== "GET" || url.origin !== location.origin || url.pathname.includes("/data/l/")) return;
  e.respondWith(SWR.test(url.pathname) ? staleWhileRevalidate(e) : networkFirst(e.request));
});

async function networkFirst(request) {
  const cache = await caches.open(CACHE);
  try {
    const response = await fetch(request, { cache: "no-cache" });
    if (response.ok) await cache.put(request, response.clone());
    return response;
  } catch (err) {
    return (await cache.match(request)) || Promise.reject(err);
  }
}

async function staleWhileRevalidate(e) {
  const cache = await caches.open(CACHE), cached = await cache.match(e.request);
  const fresh = fetch(e.request, { cache: "no-cache" }).then(async (response) => {
    if (!response.ok) return response;
    const before = cached?.headers.get("etag") || cached?.headers.get("last-modified");
    const after = response.headers.get("etag") || response.headers.get("last-modified");
    if (cached && before && before === after) return response;  // unchanged (a 304): not written again (Tokyo: 10 MB)
    await cache.put(e.request, response.clone());
    if (cached && before !== after) {  // the page has an older copy: tell it
      for (const client of await self.clients.matchAll()) client.postMessage({ type: "updated", url: e.request.url });
    }
    return response;
  });
  if (!cached) return fresh;
  e.waitUntil(fresh.catch(() => {}));
  return cached;
}

// ---------- photos ----------
// SUUMO's image server sends no CORS headers, so a cached photo is an opaque response: its size can't be read and
// browsers count each as several MB of quota (Chrome ~7 MB). So the cache is capped by count (PHOTO_MAX entries,
// pins included), and an opaque entry, which may be an error page, is refetched after PHOTO_DAYS.
const PHOTO_MAX = 100, PIN_MAX = 30, PHOTO_DAYS = 7;
const INDEX = new URL("__photo-index", self.registration.scope).href;  // [[url, used, fetched], ...] oldest use first
let photoState = null;  // (one load per worker: concurrent requests share it)
function photos() {
  return (photoState ||= (async () => {
    const cache = await caches.open(PHOTOS), used = new Map();
    try { for (const [url, at, got] of (await (await cache.match(INDEX))?.json()) || []) used.set(url, [at, got]); } catch { /* none yet */ }
    const keys = new Set((await cache.keys()).map((r) => r.url).filter((u) => u !== INDEX));
    for (const url of used.keys()) if (!keys.has(url)) used.delete(url);
    for (const url of keys) if (!used.has(url)) used.set(url, [0, 0]);  // (stored before an index: least recent)
    const order = [...used].sort((a, b) => a[1][0] - b[1][0]);
    used.clear();
    for (const [url, v] of order) used.set(url, v);
    return { cache, used, saving: null };
  })().catch((err) => { photoState = null; throw err; }));
}
// The order is written back shortly after a change (one write for a burst of photos).
function saveIndex(st) {
  return (st.saving ||= new Promise((ok) => setTimeout(ok, 1000)).then(() => {
    st.saving = null;
    return st.cache.put(INDEX, new Response(JSON.stringify([...st.used].map(([u, [a, g]]) => [u, a, g]))));
  }));
}
function touch(st, url, fetched) {
  const prev = st.used.get(url);
  st.used.delete(url);
  st.used.set(url, [Date.now(), fetched ?? prev?.[1] ?? Date.now()]);
}
async function trim(st) {
  const room = PHOTO_MAX - (await (await caches.open(PINS)).keys()).length;
  for (const url of st.used.keys()) {
    if (st.used.size <= Math.max(0, room)) break;
    st.used.delete(url);
    await st.cache.delete(url);
  }
}
const cacheable = (res) => res.ok || res.type === "opaque";  // (an opaque one can't say; "error" never)
async function photo(e) {
  try {
    const pinned = await (await caches.open(PINS)).match(e.request);
    if (pinned) return pinned;
    const st = await photos(), url = e.request.url, hit = await st.cache.match(e.request);
    if (hit && Date.now() - (st.used.get(url)?.[1] || 0) < PHOTO_DAYS * 864e5) {
      touch(st, url);
      e.waitUntil(saveIndex(st).catch(() => {}));
      return hit;
    }
    const res = await fetch(e.request);
    // thumbnails (w=120: the gallery strip, map popups) aren't kept: the count cap is for card and viewer photos
    if (cacheable(res) && new URL(url).searchParams.get("w") !== "120") {
      e.waitUntil(st.cache.put(e.request, res.clone())
        .then(() => { touch(st, url, Date.now()); return trim(st); })
        .then(() => saveIndex(st))
        .catch(() => { st.used.delete(url); }));  // (quota: not counted)
    }
    return res;
  } catch {
    return fetch(e.request);
  }
}
// The page pins a favorite's photos ({type: "pin", urls}) and lets them go ({type: "unpin", urls}); pins beyond
// PIN_MAX are left to the ordinary cache. A pin request is answered on its port: {pinned: [the urls now pinned],
// full: no room for more}.
const isPhotoUrl = (u) => { try { return typeof u === "string" && new URL(u).hostname === "img01.suumo.com"; } catch { return false; } };
self.addEventListener("message", (e) => {
  const { type, urls } = e.data || {};
  if ((type !== "pin" && type !== "unpin") || !Array.isArray(urls)) return;
  e.waitUntil((async () => {
    const pins = await caches.open(PINS), pinned = [];
    let full = false;
    for (const url of urls.filter(isPhotoUrl)) {
      if (type === "unpin") { await pins.delete(url); continue; }
      if (await pins.match(url)) { pinned.push(url); continue; }
      if ((await pins.keys()).length >= PIN_MAX) { full = true; continue; }
      try {
        const res = await fetch(url, { mode: "no-cors" });
        if (cacheable(res)) { await pins.put(url, res); pinned.push(url); }
      } catch { /* offline or quota: not pinned, the page asks again next visit */ }
    }
    e.ports?.[0]?.postMessage({ pinned, full });
    const st = await photos().catch(() => null);
    if (st) await trim(st).catch(() => {});
  })().catch(() => { e.ports?.[0]?.postMessage({ pinned: [], full: false }); }));
});
