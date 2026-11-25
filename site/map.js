// The map (Leaflet, loaded from unpkg): always there; the list is what's in its view. Town clusters to zoom 15
// (CLUSTER_MAX, supercluster once per result set); from OWN_ZOOM (16) homes with an own point get their own pin
// (.pin.own); from PRICE_ZOOM a town's marker is a price tag. The open listing has a ring.

import { $, ACTIONS, COARSE, SPLIT, WIDE, esc, frame, ls, recent } from "./util.js";
import { S, age, get, item, listingLink, money, num, photoTag, station, t, typeName, usd, value } from "./format.js";
import { isListing, listing, st } from "./state.js";
import { hashFor, saveLast } from "./url.js";
import { dataVersion, drawn, kept, listResults, update } from "./results.js";
import { askRail, rail, railMoved, showRail } from "./rail.js";
import { setSheet } from "./panel.js";

const TILES = "https://cyberjapandata.gsi.go.jp/xyz/pale/{z}/{x}/{y}.png";
const MAP_FILES = [  // (supercluster, the markers' clustering, is ours: no integrity needed)
  ["css", "https://unpkg.com/leaflet@1.9.4/dist/leaflet.css", "sha384-sHL9NAb7lN7rfvG5lfHpm643Xkcjzp4jFvuavGOndn6pjVqS6ny56CAt3nsEVT4H"],
  ["js", "https://unpkg.com/leaflet@1.9.4/dist/leaflet.js", "sha384-cxOPjt7s7Iz04uaHJceBmS+qpjv2JkIHNVcuOrM+YHwZOmJGBXI00mdUXEq65HTH"],
  ["js", "supercluster.min.js"],
];

export let leaflet = null, map = null;
let autopan = false;          // a popup moved the map: the list stays as it is
export const MAP_WAIT = 2500;
const CLUSTER_MAX = 15;       // zoomed in further, every town has its marker
const OWN_ZOOM = 16;          // from this zoom, a home with its own point (the listing page's map) is drawn there
const PRICE_ZOOM = 15;        // from this zoom, a town's marker is a price tag
// The listings of a list in a box: looked up on the list's grid (Filter.spatial), made the first time it's asked.
const grids = new WeakMap();
export const inArea = (list, b) => (grids.get(list) || grids.set(list, Filter.spatial(list)).get(list)).within(b);
let viewItems = null, viewItemsFor = null;  // the chosen prefectures' listings in view (the quick chips count them)
export const inView = () => (viewItemsFor === `${dataVersion}|${st.view}` ? viewItems : ((viewItemsFor = `${dataVersion}|${st.view}`), (viewItems = inArea(st.db.items, st.view))));

// After a pan or zoom (the map's one moveend handler): the markers at once, the lines and stations in view, then
// shortly (onMapMove) the list, its counts and the URL (replaced, not a history entry).
function onMoveEnd() {
  drawMarkers();
  railMoved();
  onMapMove();
}
let moveTimer = null;
export function onMapMove() {
  clearTimeout(moveTimer);
  moveTimer = setTimeout(updateView, 150);
}
function updateView() {
  if (!st.db) return;  // (the map came before the listings: placed again once they're in)
  const size = map.getSize(), h = size.y - hidden();
  if (h < 40) return;  // the sheet covers the map: the list stays as it was
  const nw = map.containerPointToLatLng([0, 0]), se = map.containerPointToLatLng([size.x, h]), c = map.containerPointToLatLng([size.x / 2, h / 2]);
  st.view = [se.lat, nw.lng, nw.lat, se.lng];
  st.mapAt = `${c.lat.toFixed(5)},${c.lng.toFixed(5)},${map.getZoom()}`;
  st.waiting = false;
  askRail();  // the lines: once listed
  history.replaceState(history.state, "", hashFor());
  if (history.state?.prefs) st.prefsUrl = location.href;  // (the picker's choice moved the map: back keeps where it is)
  saveLast();
  if (autopan) { autopan = false; return; }
  if (document.body.classList.contains("resizing")) return;  // (the divider: once it's let go)
  if (document.body.classList.contains("show-filters") && !WIDE.matches) st.listStale = true;
  else listResults(true);
}

// The height of the map under the bottom sheet (phones; a full sheet counts as half: the map shows when it's back).
function hidden() {
  if (SPLIT.matches) return 0;
  const box = map.getContainer().getBoundingClientRect(), panel = $("panel");
  return Math.max(0, box.bottom - (panel.dataset.sheet === "full" && !panel.classList.contains("dragging") ? innerHeight / 2 : panel.getBoundingClientRect().top));
}
// The map's centre that puts latlng in the middle of the visible part.
const centreFor = (latlng, z) => map.unproject(map.project(latlng, z).add([0, hidden() / 2]), z);

// The open listing on the map: a ring at its town, outside the clusters (also when the conditions leave it out).
let openMarker = null;
export function markOpen() {
  const it = isListing() && listing(st.tab);
  openMarker?.remove();
  // (its own point when it has one: where its pin is from OWN_ZOOM; zoomed out the two are a few pixels apart)
  openMarker = it?.lat != null && map ? L.marker([it.ownLat ?? it.lat, it.ownLng ?? it.lng], { icon: L.divIcon({ className: "", html: '<div class="open-pin"></div>', iconSize: [30, 30] }),
    interactive: false, keyboard: false, zIndexOffset: 1000 }).addTo(map) : null;
}

// Moves the map as fitNext says, once it's there.
export function placeMap() {
  const how = st.fitNext, it = how?.id && listing(how.id), [lat, lng, z] = how?.m?.split(",").map(Number) ?? [];
  if (!map || !how) return;
  st.fitNext = null;
  if (how.m) map.setView(centreFor([lat, lng], z), z);
  else if (how.b) map.fitBounds([[how.b[0], how.b[1]], [how.b[2], how.b[3]]], { paddingBottomRight: [0, hidden()] });
  else if (it?.ownLat != null) map.setView(centreFor([it.ownLat, it.ownLng], OWN_ZOOM), OWN_ZOOM);  // its own point
  else if (it?.lat != null) map.setView(centreFor([it.lat, it.lng], 15), 15);
  else {  // the results (none: the prefectures)
    const placed = st.mapHits.filter((h) => get.lat(h) != null);
    const bounds = placed.length ? coreBounds(placed, (h) => [get.lat(h), get.lng(h)])
      : coreBounds(Filter.places(st.db), (p) => p);
    if (bounds) map.fitBounds(bounds, { paddingTopLeft: [30, 30], paddingBottomRight: [30, 30 + hidden()], maxZoom: 15 });
  }
  onMapMove();  // (also when it was there already)
}

// Leaflet loaded (true) or not available (false). A failure is retried at a render a minute later at the
// soonest (not at every render), its elements removed first.
let mapFailedAt = -Infinity;
export function mapReady() {
  if (!leaflet && performance.now() - mapFailedAt < 60000) return Promise.resolve(false);
  const els = [];
  leaflet ||= Promise.all(MAP_FILES.map(([kind, url, integrity]) => new Promise((ok, fail) => {
    const el = document.createElement(kind === "css" ? "link" : "script");
    Object.assign(el, kind === "css" ? { rel: "stylesheet", href: url } : { src: url, async: false });
    Object.assign(el, { ...(integrity && { integrity, crossOrigin: "" }), onload: ok, onerror: fail });
    els.push(el);
    document.head.append(el);
  }))).then(() => true, () => {
    for (const el of els) el.remove();
    leaflet = null;
    mapFailedAt = performance.now();
    return false;
  });
  return leaflet;
}

// Markers: a town (丁目) is a pill with its count, or from PRICE_ZOOM a price tag (its lowest price, and how many);
// a cluster of towns is a filled circle sized by its count (a tap zooms in). Icons are reused across redraws.
const icons = new Map(), countLabels = new Map(), MAX_ICONS = 4000;
const cachedIcon = (key, make) => recent(icons, key, MAX_ICONS, make);
// a marker's count as written (formatting tens of thousands of them adds up)
const countLabel = (n) => recent(countLabels, S.lang + n, MAX_ICONS, () => num(n));
// (own: the homes' own point, not their town's: a pin of its own look)
function townIcon(list, priced, own = false) {
  const cls = own ? "pin own" : "pin";
  if (!priced) {
    const label = countLabel(list.length);
    return cachedIcon(`t${label}${own}`, () => L.divIcon({ className: "", html: `<div class="${cls}">${label}</div>`, iconSize: [Math.max(28, 14 + label.length * 8), 24] }));
  }
  const lo = list.reduce((m, h) => { const p = get.price(h); return p != null && (m == null || p < m) ? p : m; }, null);
  const label = lo == null ? t("undecided") : t("pinPrice", lo), more = list.length > 1 ? list.length : 0;
  return cachedIcon(`p${label}|${more}${own}`, () => {
    const w = 18 + label.length * (S.lang === "en" ? 7 : 10) + (more ? 10 + String(more).length * 7 : 0);
    return L.divIcon({ className: "", html: `<div class="${cls} price">${esc(label)}${more ? `<i>${countLabel(more)}</i>` : ""}</div>`,
      iconSize: [w, 26], iconAnchor: [w / 2, 32] });
  });
}
// (its size on a log scale: 2 homes 24 px, 100 33 px, 1,000 39 px, 10,000 44 px)
function clusterIcon(n) {
  const size = Math.round(22 + 5.5 * Math.log10(Math.max(2, n)));
  return cachedIcon(`c${n}`, () => L.divIcon({ className: "", html: `<div class="cl${n >= 1000 ? " big" : ""}" style="width:${size}px;height:${size}px">${countLabel(n)}</div>`, iconSize: [size, size] }));
}
let priced = false;  // the town markers show prices (zoomed in)

function createMap() {
  const motion = !matchMedia("(prefers-reduced-motion: reduce)").matches;
  map = L.map("map", { zoomControl: false, zoomAnimation: motion, fadeAnimation: motion, markerZoomAnimation: motion,
    wheelPxPerZoomLevel: 120, wheelDebounceTime: 80 });  // a wheel notch: about one level
  map.setView([35.68, 139.76], 10);  // a view before the markers (they're those in view); placed later
  // Leaflet measures its box only on window resizes; the layout also resizes it (the filter rail, the panel), and a
  // stale size leaves part of the map without tiles
  new ResizeObserver(() => map.invalidateSize({ pan: false, debounceMoveend: true })).observe(map.getContainer());
  map.on("popupopen", (e) => {
    const el = e.popup.getElement(), x = el?.querySelector(".leaflet-popup-close-button");
    if (x) { x.setAttribute("aria-label", t("close")); x.title = t("close"); }  // (Leaflet's: in English)
    el?.querySelector(".popup-list a")?.focus({ preventScroll: true });
  });
  map.on("popupclose", () => setTimeout(drawMarkers));  // (its marker was kept while it was open)
  map.on("autopanstart", () => { autopan = true; });
  map.on("moveend", onMoveEnd);
  markerLayer = L.layerGroup().addTo(map);
  if (!COARSE.matches) L.control.zoom({ position: "topright", zoomInTitle: t("zoomIn"), zoomOutTitle: t("zoomOut") }).addTo(map);
  map.attributionControl.setPrefix(false);  // no "Leaflet" and flag: only the data credits, kept short
  // (phones: bottom left, just above the sheet: style.css moves the corner with it)
  map.attributionControl.setPosition(SPLIT.matches ? "bottomright" : "bottomleft");
  L.tileLayer(TILES, { maxZoom: 18 }).addTo(map);
  credits();
}
// The map's data credits in the page's language (the tiles; the lines once rail.json is loaded)
let creditsShown = [];
export function credits() {
  if (!map) return;
  for (const c of creditsShown) map.attributionControl.removeAttribution(c);
  creditsShown = [`<a href="https://maps.gsi.go.jp/development/ichiran.html" target="_blank" rel="noopener">${esc(t("tileCredit"))}</a>`];
  if (rail) creditsShown.push(`<span title="${esc(rail.attribution)}">${esc(t("railCredit"))}</span>`);
  for (const c of creditsShown) map.attributionControl.addAttribution(c);
}

// The map and its tiles before the listings (a phone's first visit: ~2 s sooner) when it's known where it opens: the
// link's (or the last search's) m=, else Tokyo's default view for a link without p=
export function earlyMap(ok) {
  if (!ok || map) return;
  const p = new URLSearchParams((location.hash || ls.get("suumo.last") || "").replace(/^#/, ""));
  const m = /^(-?[\d.]+),(-?[\d.]+),(\d+)$/.exec(p.get("m") || "");
  if (!m && p.get("p")) return;  // (another prefecture: where it is comes with the index)
  createMap();
  if (m) map.setView([+m[1], +m[2]], +m[3]);
}

export async function drawMap() {
  if (!(await mapReady())) {  // (unpkg unreachable) everything is listed
    $("map").innerHTML = `<p class="map-failed">${esc(t("mapFailed"))}</p>`;
    return listResults((st.waiting = false));
  }
  if (!map) { $("map").querySelector(".map-failed")?.remove(); createMap(); }
  drawMarkers();
  showRail();  // the chosen stations' lines stand out
  markOpen();
  placeMap();
}

// The markers: the results' towns clustered once per set of results (Supercluster; in any order), and on the map
// only the clusters and towns in and around the view, made as it moves. The current set, the one before it and the
// unfiltered one are kept, so going back to one (clearing the conditions) is quick. Towns at one point are one
// marker. A cluster counts the homes in it.
export const clusterSets = new Map();  // drawn.set -> {index, lat, lng, start, homes, order}: see clusterSet
const onMap = new Map();      // the markers on the map: "c" + cluster id or "p" + point -> marker
let markerLayer = null, shownSet = null, shownLang = null;
let layerOrder = null;        // the results (drawn.search) whose order the shown set's towns list their homes in
// Point i is at lat[i], lng[i]; its homes are homes[start[i]..start[i + 1]] (handles, in the results' order).
function clusterSet() {
  const n = st.mapHits.length, of = new Int32Array(n).fill(-1), at = new Map(), byTown = new Map(), lat = [], lng = [];
  for (let i = 0; i < n; i++) {  // each home's point (town -> its point; towns at one place are one point)
    const h = st.mapHits[i], y = get.lat(h);
    if (y == null) continue;
    const town = get.townKey(h);
    let k = byTown.get(town);
    if (k === undefined) {
      const x = get.lng(h), place = `${y},${x}`;
      if ((k = at.get(place)) === undefined) { at.set(place, (k = lat.length)); lat.push(y); lng.push(x); }
      byTown.set(town, k);
    }
    of[i] = k;
  }
  const m = lat.length, start = new Int32Array(m + 1);
  for (let i = 0; i < n; i++) if (of[i] >= 0) start[of[i] + 1]++;
  for (let k = 0; k < m; k++) start[k + 1] += start[k];
  const homes = new Int32Array(start[m]), next = start.slice(0, m);
  for (let i = 0; i < n; i++) if (of[i] >= 0) homes[next[of[i]]++] = st.mapHits[i];
  const index = new Supercluster({ radius: 60, extent: 256, maxZoom: CLUSTER_MAX,  // (60: fewer, calmer clusters)
    map: (props) => ({ n: props.n }), reduce: (sum, props) => { sum.n += props.n; } });
  index.load(Array.from({ length: m }, (_, i) => ({ type: "Feature", properties: { i, n: start[i + 1] - start[i] }, geometry: { type: "Point", coordinates: [lng[i], lat[i]] } })));
  compact(index, m);
  // homes with their own point, one marker per point (a building's homes share it): own[k] = [lat, lng, handles]
  const ownAt = new Map(), own = [];
  for (let i = 0; i < n; i++) {
    const h = st.mapHits[i], y = get.ownLat(h);
    if (y == null || of[i] < 0) continue;
    const x = get.ownLng(h), place = `${y},${x}`;
    let k = ownAt.get(place);
    if (k === undefined) { ownAt.set(place, (k = own.length)); own.push([y, x, []]); }
    own[k][2].push(h);
  }
  return { index, lat: Float64Array.from(lat), lng: Float64Array.from(lng), start, homes, own, order: drawn.search };
}
// A loaded Supercluster, smaller: its points' features aren't kept (a point is read, by its number, only when it's
// in view), and each zoom's numbers are a Float64Array rather than a growable array (the same values).
// This reaches into Supercluster's internals (8.0.1, vendored: site/supercluster.min.js): `points` (getClusters
// returns points[i] for a lone point; only its `properties` survive here, so getTile, which reads `geometry`, would
// break) and `trees[z].data` (each zoom's flat number array). Recheck both on any upgrade: a renamed field fails
// silently (wrong markers or none), not with an error.
function compact(index, m) {
  index.points = new Proxy([], { get: (_, k) => (k === "length" ? m : typeof k === "string" ? { properties: { i: +k } } : undefined) });
  for (const tree of index.trees) if (tree) tree.data = Float64Array.from(tree.data);
}
// A town's marker (close: zoomed in to OWN_ZOOM, without the homes drawn at their own point; null when none is left).
function pointMarker(set, i, close) {
  let list = set.homes.subarray(set.start[i], set.start[i + 1]);
  if (close) list = list.filter((h) => get.ownLat(h) == null);
  if (!list.length) return null;
  return homesMarker([set.lat[i], set.lng[i]], list, false);
}
function homesMarker(at, list, own = true) {
  const towns = new Map();
  for (const h of list) if (!towns.has(get.townKey(h))) towns.set(get.townKey(h), get.town(h));
  const names = [...towns.values()].join(t("sep"));
  return L.marker(at, { icon: townIcon(list, priced, own), pricedAs: priced, list, own, title: `${names} ${t("count", countLabel(list.length))}` })
    .bindPopup(() => popup(names, inOrder(list)), { autoPanPadding: [56, 56] });
}
function clusterMarker(f, index) {
  const [lng, lat] = f.geometry.coordinates;
  return L.marker([lat, lng], { icon: clusterIcon(f.properties.n), title: t("clusterTitle", countLabel(f.properties.n)) }).on("click", () => {  // zoomed in until it splits
    const z = Math.min(index.getClusterExpansionZoom(f.properties.cluster_id), map.getMaxZoom());
    map.setView(centreFor([lat, lng], z), z);
  });
}
let clustering = null;        // the set being clustered (in a task of its own, after the list is drawn)
function drawMarkers() {
  if (!map || !drawn.set) return;
  if (!clusterSets.has(drawn.set)) {  // (the markers already there stay until the new ones are ready)
    if (clustering !== drawn.set) {
      const key = (clustering = drawn.set);
      frame().then(() => {
        if (clustering !== key) return;  // (another set was asked for meanwhile)
        clustering = null;
        if (drawn.set === key) { kept(clusterSets, key, clusterSet, drawn.base); drawMarkers(); }
      });
    }
    return;
  }
  const set = kept(clusterSets, drawn.set, clusterSet, drawn.base);
  if (set !== shownSet || shownLang !== S.lang) {  // other results (or labels): all new markers
    markerLayer.clearLayers();
    onMap.clear();
    shownSet = set;
    shownLang = S.lang;
    layerOrder = set.order;
  }
  const z = map.getZoom(), b = map.getBounds().pad(0.25), want = new Map();
  priced = z >= PRICE_ZOOM;
  const close = z >= OWN_ZOOM;  // (town markers keyed apart: they hold fewer homes then)
  for (const f of set.index.getClusters([b.getWest(), b.getSouth(), b.getEast(), b.getNorth()], Math.floor(z))) {
    want.set(f.properties.cluster ? `c${f.properties.cluster_id}` : `${close ? "q" : "p"}${f.properties.i}`, f);
  }
  if (close) {
    const s = b.getSouth(), n = b.getNorth(), w = b.getWest(), e = b.getEast();
    set.own.forEach(([y, x], k) => { if (y >= s && y <= n && x >= w && x <= e) want.set(`o${k}`, k); });
  }
  for (const [k, m] of onMap) {
    if (want.has(k) || m.isPopupOpen()) continue;  // (an open popup stays, with its marker, until it's closed)
    markerLayer.removeLayer(m);
    onMap.delete(k);
  }
  for (const [k, f] of want) {
    const m = onMap.get(k);
    if (!m) {
      const made = k[0] === "o" ? homesMarker(set.own[f].slice(0, 2), set.own[f][2])
        : f.properties.cluster ? clusterMarker(f, set.index) : pointMarker(set, f.properties.i, close);
      if (!made) continue;  // (a town whose homes are all at their own points)
      onMap.set(k, made);
      markerLayer.addLayer(made);
    } else if (m.options.list && m.options.pricedAs !== priced) {  // zoomed past PRICE_ZOOM
      m.options.pricedAs = priced;
      m.setIcon(townIcon(m.options.list, priced, m.options.own));
    }
  }
}
// the map: show all the results, a listing on it, a station's homes
Object.assign(ACTIONS, {
  fit() { st.fitNext = "results"; placeMap(); },
  onMap(key) {  // a listing's 地図で見る: the map goes there (phones: the sheet makes room)
    st.fitNext = { id: key };
    map.closePopup();
    placeMap();
    if (!SPLIT.matches) setSheet("half", false);
  },
  stationPick(name) {  // from a station on the map
    st.q.stations = [name];
    st.lastPlaces.push(name);  // it's in sight already: the map stays
    map?.closePopup();
    update(false);
  },
});

// A town's homes in the results' order (the layer may have been drawn for another sort).
function inOrder(list) {
  if (layerOrder === drawn.search || list.length < 2) return list;
  st.rank ??= Filter.positions(st.mapHits);
  const at = (h) => { const i = st.rank(h); return i < 0 ? Infinity : i; };  // (markers of the previous results, while the new ones are made)
  return [...list].sort((a, b) => at(a) - at(b) || 0);
}

// Where the results are, without far outliers (islands, a stray geocode): the 2nd-98th percentile box; all of
// them when there are few; null for none. (Of an even sample of at most CORE_SAMPLE of them: the box hardly
// changes.) point: an element of the list -> [lat, lng].
const CORE_SAMPLE = 5000;
function coreBounds(all, point) {
  if (!all.length) return null;
  const step = Math.max(1, all.length / CORE_SAMPLE), points = Array.from({ length: Math.floor(all.length / step) }, (_, k) => point(all[Math.floor(k * step)]));
  const pick = (i) => Float64Array.from(points, (p) => p[i]).sort(), cut = points.length < 20 ? 0 : 0.02;  // (numeric)
  const lat = pick(0), lng = pick(1), at = (a, f) => a[Math.min(a.length - 1, Math.floor((a.length - 1) * f + 0.5))];
  return [[at(lat, cut), at(lng, cut)], [at(lat, 1 - cut), at(lng, 1 - cut)]];
}

function popup(town, list) {
  return `<strong>${esc(town)}</strong> ${t("count", num(list.length))}<ul class="popup-list">${Array.from(list.slice(0, 40), item).filter(Boolean).map((it) =>
    `<li>${listingLink(it, `${it.image != null ? photoTag(it, 120, 90, { keep: true }) : "<span></span>"}
      <span><b class="num">${money(it.price)}</b> ${usd(it)}<br>${esc([value(it.layout) || typeName(it.type), age(it)].filter(Boolean).join(" · "))}<br>${esc(station(it))}</span>`)}</li>`).join("")}</ul>`;
}
