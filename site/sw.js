// Service worker: repeat visits show the search indexes at once (from this browser's cache) while a fresh copy is
// fetched; the page is told when one changed. The page, scripts and data/index.json come from the network first
// (the cache only when offline), so code and data stay in step. VERSION is set by the build (site.py) from the
// page's files: a new version drops every cached file.
const VERSION = "__VERSION__";
const CACHE = `suumo-${VERSION}`;
const SWR = /\/data\/(p\/[^/]+|rail)\.json$/;  // stale-while-revalidate: the big data files

self.addEventListener("install", () => self.skipWaiting());
self.addEventListener("activate", (e) => e.waitUntil((async () => {
  for (const key of await caches.keys()) if (key !== CACHE) await caches.delete(key);
  await self.clients.claim();
})()));

self.addEventListener("fetch", (e) => {
  const url = new URL(e.request.url);
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
    await cache.put(e.request, response.clone());
    const before = cached?.headers.get("etag") || cached?.headers.get("last-modified");
    const after = response.headers.get("etag") || response.headers.get("last-modified");
    if (cached && before !== after) {  // the page has an older copy: tell it
      for (const client of await self.clients.matchAll()) client.postMessage({ type: "updated", url: e.request.url });
    }
    return response;
  });
  if (!cached) return fresh;
  e.waitUntil(fresh.catch(() => {}));
  return cached;
}
