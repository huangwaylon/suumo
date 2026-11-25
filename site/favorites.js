// Favorites: the shared saved list (the index's `saved` flag) plus this browser's pending requests (a heart opens a
// GitHub issue; suumo/saved.py applies it); their photos pinned by the service worker.

import { $, keep, stored, toast } from "./util.js";
import { cardPhoto, t } from "./format.js";
import { byKey, favs, listing, loaded, st } from "./state.js";
import { hashFor } from "./url.js";
import { render } from "./results.js";
import { record } from "./records.js";
import { photosOf } from "./listing.js";

// The saved list is shared and kept on GitHub (saved.json, see suumo/saved.py): the heart opens a pre-filled
// issue, a workflow applies it and rebuilds the site. Until then the request shows here as pending.
const REPO = "https://github.com/huangwaylon/suumo";
const PENDING_HOURS = 24;   // a request that never arrived (issue not sent) is forgotten after this

// favs from the site's saved list and this browser's pending requests (dropped once the list shows them).
export function refreshFavs() {
  const now = Date.now();
  favs.clear();
  for (const key of Object.keys(st.manifest.saved)) favs.add(key);
  for (const [key, [save, at]] of Object.entries(st.pending)) {
    if (favs.has(key) === save || now - at > PENDING_HOURS * 3600e3) delete st.pending[key];
    else if (save) favs.add(key); else favs.delete(key);
  }
  keep("suumo.pending", st.pending);
  $("favs-n").hidden = !favs.size;
  $("favs-n").textContent = favs.size;
}

export function toggleFav(key) {
  const save = !favs.has(key);
  const title = `${save ? "save" : "unsave"} ${key}`;  // read by suumo/saved.py
  window.open(`${REPO}/issues/new?title=${encodeURIComponent(title)}&body=${encodeURIComponent(t("issueBody", location.origin + location.pathname + hashFor({ id: key })))}`,
    "_blank", "noopener");
  st.pending[key] = [save, Date.now(), [...loaded].find(([, d]) => Filter.find(d, key) >= 0)?.[0]];  // (its prefecture: loaded to list it)
  refreshFavs();
  setFavButtons(key);
  toast(t(save ? "favAdded" : "favRemoved"));
  if (st.mode === "favorites") render();
  pinPhotos(key, save).catch(() => {});
}
// A favorite's photos kept by the service worker for good: its card's and its gallery's first two (the worker
// caps pins: each photo is an opaque response, counted as megabytes). What was pinned for each listing is
// remembered here (suumo.pins), so removing it lets go of exactly those; reconcilePins keeps them in step with
// the favorites (the shared list included) once per visit.
const PIN_PHOTOS = 2, PIN_LISTINGS = 10;
const pinnedPhotos = stored("suumo.pins", {});
const savePins = () => keep("suumo.pins", pinnedPhotos);
// pinnedPhotos[key] = {u: the URLs the worker confirmed it pinned, ok: all of them (or none were wanted, or its
// pins are full)}; a favorite that isn't ok is asked again at the next reconcile.
const pinsOf = (key) => { const v = pinnedPhotos[key]; return Array.isArray(v) ? { u: v, ok: true } : v || null; };
// The worker's answer to a pin request: {pinned: [urls], full}; null when it doesn't answer.
function askWorker(sw, msg) {
  return new Promise((ok) => {
    const ch = new MessageChannel(), timer = setTimeout(() => ok(null), 15000);
    ch.port1.onmessage = (e) => { clearTimeout(timer); ok(e.data || null); };
    sw.postMessage(msg, [ch.port2]);
  });
}
async function pinPhotos(key, pin) {
  const sw = navigator.serviceWorker?.controller;
  if (!sw) return;
  const had = pinsOf(key);
  if (!pin) {
    if (had?.u.length) sw.postMessage({ type: "unpin", urls: had.u });
    delete pinnedPhotos[key];
    savePins();
    return;
  }
  if (had?.ok || (!had && Object.keys(pinnedPhotos).length >= PIN_LISTINGS)) return;
  const it = listing(key);
  let r;
  try { r = await record(key); } catch (e) { if (e.message !== "gone") return; r = null; }  // (offline: next time)
  const urls = it?.image != null ? [cardPhoto(it)] : [];
  if (r) for (const p of photosOf(r).slice(0, PIN_PHOTOS)) urls.push(Filter.photoUrl(p.path, 640, 480));
  if (!urls.length) { pinnedPhotos[key] = { u: [], ok: true }; savePins(); return; }  // (no photos: nothing to ask)
  const answer = await askWorker(sw, { type: "pin", urls });
  if (!answer) return;  // (no answer: asked again next time)
  const pinned = (answer.pinned || []).filter((u) => urls.includes(u));
  pinnedPhotos[key] = { u: pinned, ok: pinned.length === urls.length || !!answer.full };
  savePins();
}
export async function reconcilePins() {
  if (!navigator.serviceWorker?.controller) return;
  for (const key of Object.keys(pinnedPhotos)) if (!favs.has(key)) await pinPhotos(key, false);
  for (const key of favs) { if (pinsOf(key)?.ok) continue; if (byKey(key) >= 0) await pinPhotos(key, true); }
}

// Every heart for the listing (its card's, its tab's) says whether it's saved.
function setFavButtons(key) {
  for (const b of document.querySelectorAll(`[data-fav="${CSS.escape(key)}"]`)) {
    b.setAttribute("aria-pressed", favs.has(key));
    b.querySelector("span")?.replaceChildren(t(favs.has(key) ? "saved" : "save"));
  }
}
