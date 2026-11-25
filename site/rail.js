// Railway lines and stations on the map (data/rail.json: MLIT 鉄道データ; colours, English names from ODPT): lines on
// halos, faint zoomed out; stations from STATION_ZOOM, their names from 14; a panel to switch operators off.

import { $, esc } from "./util.js";
import { S, num, saveSettings, settings, stationName, t } from "./format.js";
import { st } from "./state.js";
import { credits, map } from "./map.js";

let railData = null;              // loading or loaded with the first map: {operators, lines, stations}
export let railLoaded = null;            // ... and once loaded
export const loadRail = () => (railData ||= fetch("data/rail.json").then((r) => r.json()).then((d) => {
  railLoaded = d;
  for (const line of d.lines) {    // each line's box, to find what's in view
    let s = Infinity, w = Infinity, n = -Infinity, e = -Infinity;
    for (const part of line[4]) {
      for (const [lng, lat] of part) {
        if (lat < s) s = lat;
        if (lat > n) n = lat;
        if (lng < w) w = lng;
        if (lng > e) e = lng;
      }
    }
    line.box = [s, w, n, e];
  }
  return d;
}).catch(() => { railData = null; return null; }));
const COARSE_BELOW = 11;  // zoom levels below this draw the lines' coarse copy
const STATION_ZOOM = 13;  // stations from this zoom (zoomed out, their dots crowd out the homes' markers)
const stationRadius = (z, lines) => (z < 15 ? 4.5 : 6) + (lines > 1 ? 1.5 : 0);
const dark = () => document.documentElement.dataset.theme === "dark";
const halo = () => (dark() ? "#0d0f13" : "#ffffff");  // under each line
// a station's ring and fill: dark on light in the light theme, the other way round in the dark one (as
// style.css .station-label; the canvas can't read CSS variables)
const dotColours = () => (dark() ? { color: "#f2f4f7", fillColor: "#171a21" } : { color: "#1d2026", fillColor: "#ffffff" });
const latlngs = (parts) => parts.map((p) => p.map(([lng, lat]) => [lat, lng]));
const lineName = (line) => (S.lang === "en" && line[2]) || line[1];
const operatorName = (op) => (S.lang === "en" && op[1]) || op[3] || op[0];  // JR東日本 rather than 東日本旅客鉄道
export let rail = null, railLines = [], railHalos = [], railStations = null, railLabels = null, railAsked = false;
let railStyled = null, radiusBand = null;  // how the lines and the stations are styled now (restyled on a change)
let lineStyle = null;                      // ... as {z, band, base, focus, colour}, for the lines added later
let railShapes = [];                       // per line [its paths, its coarse paths (zoomed out)] as Leaflet takes them
let railDots = [];                         // each station's marker (null until near the view); those near it are in railStations
let railCanvas = null;                     // the lines' and stations' canvas
const hiddenOps = new Set([].concat(settings.railHidden || []));  // operators switched off (remembered)

async function drawRail() {
  rail = await loadRail();
  if (!rail) return;  // the map works without it
  const canvas = (railCanvas = L.canvas({ padding: 0.3 }));
  // (an older rail.json, from the service worker's cache, has no coarse copy: the paths serve for both)
  railShapes = rail.lines.map((line) => { const fine = latlngs(line[4]); return [fine, line[5] ? latlngs(line[5]) : fine]; });
  const coarse = map.getZoom() < COARSE_BELOW ? 1 : 0;
  railHalos = railShapes.map((shapes) => Object.assign(L.polyline(shapes[coarse], { renderer: canvas, interactive: false }), { shape: coarse }));
  railLines = rail.lines.map((line, i) => Object.assign(L.polyline(railShapes[i][coarse], { renderer: canvas, color: line[3], interactive: false }), { shape: coarse }));
  // stations as on a transit map: a dot in a contrasting ring (dotColours), bigger where lines meet and as you zoom in (each made
  // the first time it's near the view: cullRail)
  railDots = new Array(rail.stations.length).fill(null);
  railStations = L.layerGroup();
  railLabels = L.layerGroup();
  credits();
  const open = document.createElement("button"), panel = document.createElement("div");
  open.className = "rail-btn";
  panel.className = "rail-panel";
  panel.hidden = true;
  open.addEventListener("click", () => { panel.hidden = !panel.hidden; if (!panel.hidden) railPanel(); });
  const applyRail = () => { settings.railHidden = [...hiddenOps]; saveSettings(); showRail(); };
  panel.addEventListener("change", (e) => {  // a switch: all lines, or one operator
    const op = e.target.dataset.op;
    if (op == null) settings.rail = e.target.checked;
    else if (e.target.checked) hiddenOps.delete(op); else hiddenOps.add(op);
    applyRail();
  });
  panel.addEventListener("click", (e) => {
    const b = e.target.closest("button[data-ops]");
    if (!b) return;
    for (const op of railOperatorsInView()) if (b.dataset.ops === "none") hiddenOps.add(op[0]); else hiddenOps.delete(op[0]);
    applyRail();
    railPanel();
  });
  for (const el of [open, panel]) { L.DomEvent.disableClickPropagation(el); L.DomEvent.disableScrollPropagation(el); $("map").append(el); }
  map.on("zoomend", showRail);
  showRail();
}

// The lines and stations, drawn while idle the first time the map has a view (askRail)
export function askRail() {
  if (railAsked) return;
  railAsked = true;
  (window.requestIdleCallback || setTimeout)(drawRail);
}

// The map moved: the lines panel lists the operators in view, the lines and stations in view are drawn.
export function railMoved() {
  if (!railStations) return;  // (not drawn yet)
  if (!$("map").querySelector(".rail-panel").hidden) railPanel();
  cullRail();
  stationLabels();
}

// Station names on the map once zoomed in (14+): only those in view, so a few dozen labels at most. Those still in
// view stay; the others go.
const labels = new Map();  // station (rail.stations entry) -> its label
let labelsLang = null;
function stationLabels() {
  if (labelsLang !== S.lang) { railLabels.clearLayers(); labels.clear(); labelsLang = S.lang; }
  const b = map.hasLayer(railStations) && map.getZoom() >= 14 && map.getBounds().pad(0.1);
  const want = new Set(b ? rail.stations.filter(([, lat, lng]) => b.contains([lat, lng])).slice(0, 200) : []);
  for (const [s, m] of labels) if (!want.has(s)) { railLabels.removeLayer(m); labels.delete(s); }
  for (const s of want) {
    if (labels.has(s)) continue;
    const [name, lat, lng] = s;
    labels.set(s, L.marker([lat, lng], { interactive: false, keyboard: false,
      icon: L.divIcon({ className: "station-label", html: esc(stationName(name)), iconSize: null, iconAnchor: [-9, 8] }) }).addTo(railLabels));
  }
}

// The operators with lines in view, most lines first.
function railOperatorsInView() {
  const b = map.getBounds(), seen = new Set();
  for (const line of rail.lines) {
    const [s, w, n, e] = line.box;
    if (n >= b.getSouth() && s <= b.getNorth() && e >= b.getWest() && w <= b.getEast()) seen.add(line[0]);
  }
  return [...seen].sort((a, c) => a - c).map((i) => rail.operators[i]);
}

function railPanel() {
  const panel = $("map").querySelector(".rail-panel"), on = settings.rail !== false;
  panel.innerHTML = `<label class="rail-all"><input type="checkbox"${on ? " checked" : ""}> ${esc(t("railShow"))}</label>
    <div class="rail-ops"${on ? "" : " hidden"}><div class="rail-bulk"><button data-ops="all">${esc(t("railAll"))}</button><button data-ops="none">${esc(t("railNone"))}</button></div>${
    railOperatorsInView().map((op) => `<label><input type="checkbox" data-op="${esc(op[0])}"${hiddenOps.has(op[0]) ? "" : " checked"}><i style="background:${esc(op[2])}"></i>${esc(operatorName(op))}</label>`).join("")}</div>`;
}

// The lines of the chosen stations (they're highlighted; the others fade).
let chosenFor = null, chosenSet = null;
function chosenLines() {
  if (chosenFor === st.q.stations.join("\n")) return chosenSet;
  chosenFor = st.q.stations.join("\n");
  const names = new Set(st.q.stations.map((s) => s.replace(/（.*）$/, "")));
  return (chosenSet = new Set(names.size ? rail.stations.filter(([n]) => names.has(n)).flatMap(([, , , lines]) => lines) : []));
}

// Lines unless switched off (all, or by operator), each on a halo so it reads on any part of the map: thin and
// faint zoomed out (the homes' markers come first), full strength zoomed in; stations once there's room for them
// (STATION_ZOOM, 13+), their names from 14.
export function showRail() {
  if (!rail || !map) return;
  const z = map.getZoom(), on = settings.rail !== false, focus = chosenLines(), colour = halo();
  const band = z < 11 ? 0 : z < 13 ? 1 : z < 15 ? 2 : 3, base = [1.5, 2, 4, 6][band];
  const style = [band, on, [...hiddenOps].sort(), [...focus], colour].join("|");
  lineStyle = { z, band, base, focus, colour };
  if (style !== railStyled) railLines.forEach((l, i) => { if (map.hasLayer(l)) styleLine(i); });
  railStyled = style;
  const stations = on && z >= STATION_ZOOM;
  if (stations !== map.hasLayer(railStations)) {
    if (stations) { railStations.addTo(map); railLabels.addTo(map); } else { railStations.remove(); railLabels.remove(); }
  }
  const dots = `${z < 15 ? 1 : 2}|${colour}`;  // (their size band and the theme)
  if (stations && radiusBand !== dots) {
    radiusBand = dots;
    railStations.eachLayer(styleDot);
  }
  cullRail();
  stationLabels();
  const b = $("map").querySelector(".rail-btn");
  b.textContent = t("railLayer");
  b.setAttribute("aria-pressed", on);
}

function styleLine(i) {
  const { z, band, base, focus, colour } = lineStyle, l = railLines[i], h = railHalos[i], shape = z < COARSE_BELOW ? 1 : 0;
  if (l.shape !== shape) { l.shape = h.shape = shape; l.setLatLngs(railShapes[i][shape]); h.setLatLngs(railShapes[i][shape]); }
  const lit = focus.has(i), faded = focus.size && !lit, weight = base + (lit ? 3 : 0);
  l.setStyle({ weight, opacity: faded ? 0.2 : lit ? 0.95 : [0.45, 0.55, 0.85, 0.95][band] });
  h.setStyle({ weight: weight + (band < 2 ? 2 : 3), color: colour, opacity: faded ? 0 : [0.3, 0.4, 0.8, 0.85][band] });
  if (lit) { h.bringToFront(); l.bringToFront(); }
}
function styleDot(m) {
  m.band = radiusBand;
  m.setStyle({ radius: stationRadius(map.getZoom(), m.options.lines), ...dotColours() });
}
// Only the lines and stations in and around the view are on the map (Leaflet reprojects every path on the map at
// each zoom); the others are added as the map moves to them, half a screen ahead.
function cullRail() {
  if (!rail || !map || !lineStyle) return;
  const b = map.getBounds().pad(0.5), s = b.getSouth(), n = b.getNorth(), w = b.getWest(), e = b.getEast();
  const on = settings.rail !== false;
  let added = false;
  for (let i = 0; i < railLines.length; i++) {
    const line = rail.lines[i], [bs, bw, bn, be] = line.box, l = railLines[i];
    const want = on && !hiddenOps.has(rail.operators[line[0]][0]) && bn >= s && bs <= n && be >= w && bw <= e;
    if (want === map.hasLayer(l)) continue;
    if (want) { railHalos[i].addTo(map); l.addTo(map); styleLine(i); added = true; } else { railHalos[i].remove(); l.remove(); }
  }
  if (added) for (const i of lineStyle.focus) if (map.hasLayer(railLines[i])) { railHalos[i].bringToFront(); railLines[i].bringToFront(); }
  if (!map.hasLayer(railStations)) return;
  for (let k = 0; k < railDots.length; k++) {
    const [, lat, lng] = rail.stations[k], m = railDots[k], want = lat >= s && lat <= n && lng >= w && lng <= e;
    if (want === (!!m && railStations.hasLayer(m))) continue;
    if (want) { const dot = m || (railDots[k] = stationDot(rail.stations[k])); if (dot.band !== radiusBand) styleDot(dot); railStations.addLayer(dot); }
    else railStations.removeLayer(m);
  }
}
function stationDot([name, lat, lng, lines]) {
  return L.circleMarker([lat, lng], { renderer: railCanvas, radius: 4, ...dotColours(), weight: 2, fillOpacity: 1,
    lines: lines.length }).bindTooltip(name, { direction: "top", offset: [0, -5] }).bindPopup(() => stationPopup(name, lines));
}

// A station on the map: its lines, and its homes under the current conditions in one tap.
function stationPopup(name, lines) {
  const s = st.db.index.stations.find((n) => n === name || n.startsWith(name + "（"));
  const n = s ? Filter.count(st.db, { ...st.q, stations: [s] }) : 0;  // under the current conditions
  return `<strong>${esc(t("station", stationName(s || name)))}</strong><div class="meta wrap">${esc(lines.map((i) => lineName(rail.lines[i])).join(t("sep")))}</div>${
    n ? `<button class="btn primary station-pick" data-station-pick="${esc(s)}">${esc(t("stationHomes", num(n)))}</button>` : ""}`;
}

// A listing's line name in English when the rail data knows it ("ＪＲ東海道本線" -> "JR Tokaido Line"), else as
// written with full-width letters made plain ("JR東海道本線").
const lineNames = new Map();
export function lineLabel(name) {
  const plain = (name || "").normalize("NFKC");
  if (S.lang !== "en" || !railLoaded || !plain) return S.lang === "en" ? plain : name;
  if (!lineNames.has(plain)) {
    const key = (n) => n.normalize("NFKC").replace(/本線$/, "線").replace(/[・\s]/g, "");
    const short = (op) => (op[3] || op[0]).replace(/(電気鉄道|電鉄|鉄道|株式会社)$/, "");
    let found = null;
    for (const line of railLoaded.lines) {
      const op = railLoaded.operators[line[0]], jr = /^JR/.test(op[3] || ""), prefix = jr ? "JR" : short(op);
      const rest = plain.startsWith(prefix) ? plain.slice(prefix.length) : null;
      if (line[2] && (key(line[1]) === key(plain) || (rest != null && (key(line[1]) === key(rest) || key(prefix + line[1]) === key(plain))))) {
        found = `${jr && !/^JR/.test(line[2]) ? "JR " : ""}${line[2]}`;
        break;
      }
    }
    lineNames.set(plain, found || plain);
  }
  return lineNames.get(plain);
}
