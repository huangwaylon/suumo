// A listing's page (its tab): galleryHtml/showPhoto (chips by caption, 間取り図 first; lazy 120×90 strip),
// openViewer (full screen, an overlay: arrows/swipe/Esc), textsHtml (folded), envHtml (周辺環境 by kind, closest
// first), officeHtml, 「掲載情報 … 時点」 (page_date); and 比較 (compareHtml: the open tabs side by side).

import { $, esc, icon, onSwipe, prose } from "./util.js";
import {
  S, TSUBO, age, areaName, day, feature, get, isCondo, listingLink, m2, money, monthlyFees, nowrapWalk, num, photoTag,
  place, price, safeUrl, station, stationName, t, tsubo, typeName, usd, value,
} from "./format.js";
import { listing, st } from "./state.js";
import { tags } from "./results.js";
import { map } from "./map.js";
import { lineLabel, loadRail, railLoaded } from "./rail.js";
import { record } from "./records.js";
import { closeOverlay, openOverlay, trapFocus } from "./overlays.js";

// Listings side by side (the 比較 tab: the open listings): a row per fact, a column per listing (× closes its tab).
// A numeric row marks its best value (lowest price, largest area...).
export async function compareHtml(list) {
  const recs = await Promise.all(list.map((it) => record(it.key).catch(() => null)));
  const cols = list.map((it, i) => [it, recs[i] || {}]);
  const yen = (v) => (v ? t("yen", num(v)) : "");
  const monthly = ([it, r]) => (isCondo(it.type) ? monthlyFees(it, r) : null);
  const walk = ([it]) => it.stations.reduce((m, [, w]) => (w != null && (m == null || w < m) ? w : m), null);
  const newBuild = (it) => it.type === "new_condo" || it.type === "new_house";
  // [label, cell html, best: [value of a column, "min" | "max"]]
  const ROWS = [
    ["", ([it]) => `<div class="cmp-top">${listingLink(it, `<div class="ph">${it.image != null ? photoTag(it, 240, 180) : ""}</div>`)}${
      `<button class="cmp-x" data-tab-close="${esc(it.key)}" aria-label="${esc(t("cmpHide", money(it.price)))}">${icon("x")}</button>`}</div>`],
    ["name", ([it]) => listingLink(it, esc(it.name || typeName(it.type)), "cmp-name")],
    ["price", ([it]) => `<span class="num">${price(it, true)}</span>${usd(it)}`, [([it]) => it.price, "min"]],
    ["unit", ([it]) => esc(it.unit ? t("perM2", it.unit) : ""), [([it]) => it.unit, "min"]],
    ["feesMonthly", (c) => esc(monthly(c) ? t("perMonth", yen(monthly(c))) : ""), [monthly, "min"]],
    ["layout", ([it]) => esc(value(it.layout) || typeName(it.type))],
    [cols.every(([it]) => isCondo(it.type)) ? "floor" : cols.some(([it]) => isCondo(it.type)) ? "floorBoth" : "building",
      ([it]) => esc(it.size ? m2(it.size) : ""), [([it]) => it.size, "max"]],
    ["land", ([it]) => esc(it.land ? m2(it.land) : ""), [([it]) => it.land, "max"]],
    ["age", ([it]) => esc(age(it)), [([it]) => (newBuild(it) ? 0 : it.ageMonths), "min"]],
    ["station", ([it]) => nowrapWalk(station(it)), [walk, "min"]],
    ["floors", ([, r]) => esc([r.floor != null && t("floorN", r.floor), r.floors_above && t("storeys", r.floors_above)].filter(Boolean).join(" / "))],
    ["structure", ([, r]) => esc(value(r.structure) || "")],
    ["units", ([, r]) => esc(r.total_units ? t("unitsN", num(r.total_units)) : "")], ["rights", ([, r]) => esc(value(r.land_rights) || "")],
    ["place", ([it]) => esc(it.town || areaName(it.area))],
  ];
  const bestOf = (best) => {
    if (!best) return null;
    const vs = cols.map(best[0]).filter((v) => v != null);
    if (vs.length < 2 || vs.every((v) => v === vs[0])) return null;
    return best[1] === "min" ? Math.min(...vs) : Math.max(...vs);
  };
  return `<div class="compare"><table><tbody>${ROWS.filter(([, f]) => cols.some((c) => f(c))).map(([k, f, best]) => {
    const top = bestOf(best);
    return `<tr><th scope="row">${k ? esc(t(`d.${k}`)) : ""}</th>${cols.map((c) => (top != null && best[0](c) === top
      ? `<td class="best"><b>${f(c)}</b></td>` : `<td>${f(c)}</td>`)).join("")}</tr>`;
  }).join("")}</tbody></table></div>`;
}

// A listing's page into box (its tab).
// Resolves to the record, or null when it couldn't be loaded (ended, offline) or box moved on to another.
export async function renderListing(box, key) {
  const it = listing(key);
  box.dataset.key = key;
  // this render's token: a later render of the box (the same listing again included: a tab left and reopened while
  // its record loads) supersedes it, so what this one draws after waiting is dropped
  const token = (box.renderToken = (box.renderToken || 0) + 1), current = () => box.renderToken === token;
  box.innerHTML = '<div class="d-head"><div class="skeleton"></div></div>';
  let r;
  try {
    r = await record(key);
  } catch (e) {  // not in its bucket: the listing ended; anything else: the network (try again)
    if (current()) box.innerHTML = e.message === "gone" ? `<div class="empty"><h3>${esc(t("gone"))}</h3></div>`
      : e.message === "unsupported" ? `<div class="empty"><h3>${esc(t("unsupported"))}</h3></div>`  // (no DecompressionStream)
      : `<div class="empty"><h3>${esc(t("offline"))}</h3><button class="btn" data-retry="${esc(key)}">${esc(t("retry"))}</button></div>`;
    return current() ? null : undefined;
  }
  if (!current()) return undefined;  // superseded (null: it couldn't be loaded)
  box.innerHTML = detailHtml(r, it);
  box.photos = photosOf(r);
  // it opens on the photo its card shows (the page's first is often the agent's map)
  const cover = r.image != null && r.image !== "" ? Filter.photo(r.image, r.id) : null;
  showPhoto(box, Math.max(0, box.photos.findIndex((p) => p.path === cover)));
  if (S.lang === "en" && r.stations?.length && !railLoaded) {  // line names in English once the rail data is in
    loadRail().then(() => { const dd = box.querySelector(".d-access"); if (dd && current()) dd.innerHTML = accessHtml(r); });
  }
  return r;
}

// (exported for tests/test_site.py, which renders a listing in Node)
export function detailHtml(r, it) {
  const type = r.type, L_ = (k) => t(`d.${k}`);
  const rows = (pairs) => pairs.filter(([, v]) => v != null && v !== "").map(([k, v]) => `<dt>${esc(L_(k))}</dt><dd>${v}</dd>`).join("");
  const sec = (k, pairs) => { const html = rows(pairs); return html ? `<section class="d-sec"><h3>${esc(L_(k))}</h3><dl>${html}</dl></section>` : ""; };
  const yen = (v) => (v ? esc(t("yen", num(v))) : null);
  const txt = (s) => (s == null || s === "" ? null : esc(value(s)));
  const second = (main, sub) => esc(main) + (sub ? `<small>${esc(sub)}</small>` : "");  // 坪 on a line of its own
  const unit = it?.unit ? second(t("perM2", it.unit), !isCondo(type) && t("perTsubo", Math.round(it.unit * TSUBO))) : "";
  const monthly = monthlyFees(it, r);
  // what matters most first: price per ㎡, monthly fees, the walk; then the home itself
  const facts = [["unit", unit], ["feesMonthly", isCondo(type) && monthly ? esc(t("yen", num(monthly))) : ""],
    [it && !it.stations.length && it.stop ? "stop" : "station", it ? nowrapWalk(station(it)) : ""],
    ["layout", esc(value(r.layout) || "")], [isCondo(type) ? "floor" : "building", esc(m2(r.floor_m2 || r.building_m2))],
    [isCondo(type) ? "balcony" : "land", isCondo(type) ? esc(m2(r.balcony_m2)) : m2(r.land_m2) && second(m2(r.land_m2), tsubo(r.land_m2))],
    ["age", it ? esc(age(it)) : ""]].filter(([, v]) => v);
  const changes = r.history?.length && r.history.map(([d, o, n]) => esc(`${day(d)} ${money(o)} → ${money(n)}`)).join("<br>");
  const parking = r.parking && [value(r.parking.status), r.parking.fee_min && t("perMonth", t("yen", num(r.parking.fee_min)))].filter(Boolean).join(" ");
  const road = r.road && ([value(r.road.dir), r.road.width_m && t("width", r.road.width_m)].filter(Boolean).join(" ") || r.road.text);
  const access = accessHtml(r);
  return `
    ${galleryHtml(photosOf(r))}
    <div class="d-head">
      <h2 tabindex="-1">${esc(r.name || [r.layout, typeName(type)].filter(Boolean).join(" "))}</h2>
      <div class="meta">${esc([r.address, r.name && typeName(type)].filter(Boolean).join(" · "))}</div>
      ${it?.lat != null && map ? `<button class="btn ghost on-map" data-on-map="${esc(it.key)}">${icon("pin")}${esc(t("showOnMap"))}</button>` : ""}
      <div class="d-price num">${it ? price(it, true) + usd(it) : money(r.price, true) + usd({ price: r.price })}${r.price_excludes_building ? `<small class="note">${esc(t("excludesBuilding"))}</small>` : ""}</div>
      <div class="tags">${it ? tags(it) : ""}</div>
      ${r.texts?.catch ? `<p class="catch">${esc(r.texts.catch)}</p>` : ""}
    </div>
    <div class="facts">${facts.map(([k, v]) => `<div class="fact"><span>${esc(L_(k))}</span><b>${v}</b></div>`).join("")}</div>
    ${changes ? `<section class="d-sec"><h3>${esc(L_("history"))}</h3>${historyChart(r, it)}<dl>${rows([["price", changes]])}</dl></section>` : ""}
    ${it ? market(it) : ""}
    ${access ? `<section class="d-sec"><h3>${esc(L_("access"))}</h3><dl><dt>${esc(L_("nearest"))}</dt><dd class="d-access">${access}</dd></dl></section>` : ""}
    ${textsHtml(r, ["pr", "event", "present"])}
    ${envHtml(r.env)}
    ${sec("costs", [["mgmt", yen(r.mgmt_fee) && t("perMonth", yen(r.mgmt_fee)) + (r.mgmt_form ? esc(t("paren", value(r.mgmt_form))) : "")],
      ["repair", yen(r.repair_fee) && t("perMonth", yen(r.repair_fee))],
      ["otherMonthly", yen(r.other_monthly) && t("perMonth", yen(r.other_monthly)) + (r.other_fees ? `<small>${esc(r.other_fees)}</small>` : "")],
      ["feesTotal", isCondo(type) && monthly ? `<b>${t("perMonth", yen(monthly))}</b>` : null], ["repairOnce", yen(r.repair_fund_once)],
      ["otherFees", r.other_monthly ? null : txt(r.other_fees)], ["parking", txt(parking)]])}
    ${sec("bldg", [["built", r.built && t("month", +r.built.slice(0, 4), +r.built.slice(5, 7)) + (r.built_planned ? t("planned") : "")],
      ["structure", txt(r.structure)], ["floors", txt([r.floor != null && t("floorN", r.floor), r.floors_above && t("storeys", r.floors_above)].filter(Boolean).join(" / "))],
      ["units", isCondo(type) && r.total_units ? t("unitsN", num(r.total_units)) : null], ["direction", txt(r.direction)],
      ["reform", txt(r.reform?.text || r.reform?.date)], ["builder", txt(r.builder)], ["handover", txt(r.handover)]])}
    ${sec("legal", [["rights", r.land_rights && esc(value(r.land_rights) + (r.land_rights_note ? t("paren", r.land_rights_note) : ""))],
      ["zoning", txt(r.zoning)], ["ratios", r.coverage_pct || r.far_pct ? esc(`${r.coverage_pct ?? "-"}% / ${r.far_pct ?? "-"}%`) : null],
      ["road", txt(road)], ["category", txt(r.land_category)], ["landStatus", txt(r.land_status)], ["condition", r.build_condition ? t("yes") : null],
      ["utilities", txt(r.utilities)], ["restrictions", txt(r.restrictions)]])}
    ${r.features?.length ? `<section class="d-sec"><h3>${esc(L_("features"))}</h3><div class="opts">${r.features.map((f) => `<span class="tag">${esc(feature(f))}</span>`).join("")}</div></section>` : ""}
    ${sec("listing", [["agent", txt(r.agent)], ["deal", txt(r.deal_type)], ["otherListings", (r.others || []).map((o) =>
      `<a href="${esc(safeUrl(o.url))}" target="_blank" rel="noopener">${esc(o.agent || "SUUMO")}</a>`).join("<br>") || null],
      ["seen", day(r.new_date || r.first_seen)]])}
    ${textsHtml(r, ["staff", "seller", "notes"])}
    ${officeHtml(r.office)}
    ${r.page_date ? `<p class="meta as-of">${esc(t("pageAsOf", day(r.page_date)))}</p>` : ""}`;
}

// A listing's photos: its page's gallery, else the search result's one photo. [{path, caption, comment}]
export function photosOf(r) {
  if (r.img?.l?.length) return Filter.gallery(r.id, r.img);
  const p = r.image != null && r.image !== "" && Filter.photo(r.image, r.id);
  return p ? [{ path: p, caption: "", comment: "" }] : [];
}
const photoCat = (c) => S.photoCats?.[c] ?? c;
const FLOOR_PLAN = /間取|区画図/;
// The photo in view (640×480), its caption and comment, chips per kind (間取り図 first), and a strip of thumbnails.
function galleryHtml(photos) {
  if (!photos.length) return "";
  const kinds = new Map();
  photos.forEach((p, i) => { if (p.caption && !kinds.has(p.caption)) kinds.set(p.caption, [i, 0]); if (p.caption) kinds.get(p.caption)[1]++; });
  const chips = [...kinds].sort(([a], [b]) => FLOOR_PLAN.test(b) - FLOOR_PLAN.test(a)).slice(0, 10);
  return `<div class="gallery" data-gallery>
    <button class="g-main" data-view aria-label="${esc(t("photoOpen"))}"><img alt="" decoding="async" onerror="this.style.visibility='hidden'">
      ${photos.length > 1 ? `<span class="g-n num"></span>` : ""}</button>
    <div class="g-cap"></div>
    ${chips.length > 1 ? `<div class="g-chips">${chips.map(([c, [i, n]]) => `<button class="chip" data-photo="${i}">${esc(photoCat(c))}${n > 1 ? ` <small>${n}</small>` : ""}</button>`).join("")}</div>` : ""}
    ${photos.length > 1 ? `<div class="g-strip">${photos.map((p, i) => `<button data-photo="${i}" aria-label="${esc(t("photoN", i + 1, photos.length))}"><img src="${esc(Filter.photoUrl(p.path, 120, 90))}" alt="" loading="lazy" decoding="async" width="120" height="90" onerror="this.style.visibility='hidden'"></button>`).join("")}</div>` : ""}
  </div>`;
}
// Shows photo i of a listing's box in its gallery.
function showPhoto(box, i) {
  const photos = box.photos, g = box.querySelector("[data-gallery]");
  if (!photos?.length || !g) return;
  i = (i + photos.length) % photos.length;
  box.photoAt = i;
  const p = photos[i], img = g.querySelector(".g-main img");
  img.style.visibility = "";
  img.src = Filter.photoUrl(p.path, 640, 480);
  img.alt = p.caption || "";
  const n = g.querySelector(".g-n");
  if (n) n.textContent = `${i + 1} / ${photos.length}`;
  g.querySelector(".g-cap").innerHTML = p.caption || p.comment ? `${p.caption ? `<b>${esc(photoCat(p.caption))}</b>` : ""}${p.comment ? ` ${prose(p.comment)}` : ""}` : "";
  for (const b of g.querySelectorAll(".g-strip [data-photo]")) b.classList.toggle("on", +b.dataset.photo === i);
  const on = g.querySelector(".g-strip .on");
  if (on) {
    // Centre it, measured against the strip itself (offsetLeft counts from a positioned ancestor, not the strip:
    // in the panel that added the strip's own 960 px and threw the strip far off)
    const s = on.parentElement, rect = on.getBoundingClientRect(), sr = s.getBoundingClientRect();
    s.scrollLeft += rect.left - sr.left - (s.clientWidth - rect.width) / 2;
  }
}

// The full-screen viewer: a history entry (back closes it); arrows, swipe, Escape.
let viewer = null;  // the open one: {box: the listing's, at: the photo shown, el, swiped}
const VIEWER = {
  entry: "viewer", el: () => viewer.el, state: (now) => ({ ...now, viewer: 1 }), modal: () => true,
  show(open) { if (open) document.body.append(viewer.el); else { viewer.el.remove(); viewer = null; } },
  closed: (focus) => focus?.focus({ preventScroll: true }),
  keys(e) {  // arrows step, Escape closes, Tab stays inside; no other key reaches the page
    if (e.key === "ArrowLeft" || e.key === "ArrowRight") { e.preventDefault(); stepViewer(e.key === "ArrowLeft" ? -1 : 1); }
    else if (e.key === "Escape") { e.preventDefault(); closeViewer(); }
    else if (e.key === "Tab") trapFocus(e, viewer.el);
    return true;
  },
};
let gallerySwiped = false;  // a swipe on the listing's photo just stepped it (its click is ignored)
// A click in the listing's gallery (the document's click handler asks first): a thumbnail or chip shows its photo,
// the photo opens the viewer. True when it was the gallery's.
export function galleryClick(e) {
  const photoBox = e.target.closest("[data-gallery]") && $("panel-body");
  if (!photoBox) return false;
  const ph = e.target.closest("[data-photo]");
  if (ph) showPhoto(photoBox, +ph.dataset.photo);
  else if (gallerySwiped) gallerySwiped = false;  // the click that ends a swipe doesn't open the viewer
  else if (e.target.closest("[data-view]")) openViewer(photoBox);
  return true;
}
// the listing's photo swiped sideways: the next or previous one (as in the viewer)
export function wireGallery() {
  onSwipe(document, (d) => {
    const box = $("panel-body");
    if (box.photos?.length > 1) { gallerySwiped = true; showPhoto(box, (box.photoAt || 0) + d); }
  }, (e) => e.target.closest?.("#panel-body .g-main"));
}

function openViewer(box) {
  if (viewer || !box.photos?.length) return;
  viewer = { box, at: box.photoAt || 0, el: document.createElement("div"), swiped: false };
  const el = viewer.el;
  el.className = "viewer";
  el.setAttribute("role", "dialog");
  el.setAttribute("aria-modal", "true");
  el.setAttribute("aria-label", t("photos"));
  el.innerHTML = `<button class="v-x" data-viewer-close aria-label="${esc(t("close"))}">${icon("x")}</button>
    <button class="v-prev" data-viewer-step="-1" aria-label="${esc(t("photoPrev"))}">${icon("back")}</button>
    <figure><img alt="" decoding="async"><figcaption></figcaption></figure>
    <button class="v-next" data-viewer-step="1" aria-label="${esc(t("photoNext"))}">${icon("back")}</button>`;
  onSwipe(el, (d) => { viewer.swiped = true; stepViewer(d); });
  el.addEventListener("click", (e) => {
    if (viewer.swiped) { viewer.swiped = false; return; }  // the click that ends a swipe neither steps nor closes
    const b = e.target.closest("button");
    if (b?.dataset.viewerStep) stepViewer(+b.dataset.viewerStep);
    else if (b?.dataset.viewerClose != null || e.target === el) closeViewer();
  });
  openOverlay(VIEWER);
  stepViewer(0);
  el.querySelector(".v-x").focus();
}
function stepViewer(d) {
  const { box, el } = viewer, photos = box.photos, n = photos.length;
  viewer.at = (viewer.at + d + n) % n;
  const p = photos[viewer.at];
  el.querySelector("img").src = Filter.photoUrl(p.path, 640, 480);
  el.querySelector("img").alt = p.caption || "";
  el.querySelector("figcaption").innerHTML = `<span class="num">${viewer.at + 1} / ${n}</span> ${p.caption ? `<b>${esc(photoCat(p.caption))}</b>` : ""} ${prose(p.comment)}`;
  for (const k of [1, -1]) new Image().src = Filter.photoUrl(photos[(viewer.at + k + n) % n].path, 640, 480);  // the next ones, ahead
  showPhoto(box, viewer.at);
}
const closeViewer = () => closeOverlay(VIEWER);

// The page's free texts (agents' copy, as written): each folded under its heading; PR open when it's short.
function textsHtml(r, keys) {
  return keys.filter((k) => r.texts?.[k]).map((k) => {
    const text = r.texts[k], long = text.length > 280;
    return `<details class="d-sec d-text"${!long && k === "pr" ? " open" : ""}><summary><h3>${esc(t(`texts.${k}`))}</h3></summary><p>${prose(text)}</p></details>`;
  }).join("");
}
// 周辺環境: the nearby facilities, by kind, with the walk.
function envHtml(env) {
  if (!env?.length) return "";
  const kinds = new Map();
  for (const [kind, text] of env) { if (!kinds.has(kind)) kinds.set(kind, []); kinds.get(kind).push(text); }
  const line = (raw) => {
    const s = raw.normalize("NFKC"), m = /^(.*?)[：:]\s*(.*)$/.exec(s);
    if (!m) return esc(s);
    const walk = /徒歩\s*約?\s*(\d+)\s*分/.exec(m[2]), dist = /[（(]\s*([\d,.]+)\s*[mｍ]\s*[)）]/.exec(m[2]);
    return `${esc(m[1])} <span class="nw meta">${esc(walk ? t("walkMin", +walk[1]) : m[2])}${dist ? esc(t("paren", `${dist[1]}m`)) : ""}</span>`;
  };
  const mins = (x) => +(/徒歩\s*約?\s*(\d+)\s*分/.exec(x.normalize("NFKC"))?.[1] ?? 999);
  for (const list of kinds.values()) list.sort((a, b) => mins(a) - mins(b));  // the closest first
  return `<details class="d-sec d-env" open><summary><h3>${esc(t("envTitle"))}</h3></summary><dl>${[...kinds].map(([k, list]) =>
    `<dt>${esc(photoCat(k))}</dt><dd>${list.map(line).join("<br>")}</dd>`).join("")}</dl></details>`;
}
// The agent's business details (company, licence, office phone, address, hours).
function officeHtml(o) {
  if (!o || !(o.name || o.tel || o.address)) return "";
  const rows = [["name", esc(o.name || "")], ["tel", o.tel ? `<a href="tel:${esc(o.tel.replace(/[^\d+]/g, ""))}">${esc(o.tel)}</a>` : ""],
    ["license", esc(o.license || "")], ["address", esc(o.address || "")], ["hours", esc(o.hours || "")]].filter(([, v]) => v);
  return `<details class="d-sec d-office"><summary><h3>${esc(t("office.title"))}</h3></summary><dl>${rows.map(([k, v]) =>
    `<dt>${esc(t(`office.${k}`))}</dt><dd>${v}</dd>`).join("")}</dl>${o.about && o.about !== o.address ? `<p class="meta pre">${esc(o.about)}</p>` : ""}</details>`;
}

// The stations of a listing: line, station, how far.
function accessHtml(r) {
  return (r.stations || []).map((s) => nowrapWalk([lineLabel(s.line), s.bus_stop ? t("busStop", s.name) : t("station", stationName(s.name)), s.bus ? [t("busMin", s.bus), s.walk != null && t("stopWalk", s.walk)].filter(Boolean).join(" ")
    : s.walk != null ? t("walkMin", s.walk) : s.car_km ? t("carKm", s.car_km_max ? `${s.car_km}–${s.car_km_max}` : s.car_km) : ""].filter(Boolean).join(" "))).join("<br>");
}

// The price over time: a step line with a dot per change (the text rows say the same).
function historyChart(r, it) {
  const h = r.history || [];
  if (!h.length) return "";
  const pts = [[r.new_date || r.first_seen || h[0][0], h[0][1]], ...h.map(([d, , p]) => [d, p])];
  const time = (d) => Date.parse(d), t0 = time(pts[0][0]);
  const end = [(st.manifest.updated || "").slice(0, 10), pts.at(-1)[0]].sort().at(-1);  // the line runs to today
  const t1 = Math.max(time(end) || 0, t0 + 864e5);
  const prices = pts.map(([, p]) => p), lo = Math.min(...prices), hi = Math.max(...prices);
  const X = (d) => (8 + ((time(d) - t0) / (t1 - t0)) * 264).toFixed(1);
  const Y = (p) => (hi === lo ? 32 : 46 - ((p - lo) / (hi - lo)) * 26).toFixed(1);
  let path = `M${X(pts[0][0])} ${Y(pts[0][1])}`;
  for (const [d, p] of pts.slice(1)) path += `H${X(d)}V${Y(p)}`;
  path += `H272`;
  const first = money(pts[0][1]), last = money(it?.price ?? pts.at(-1)[1]);
  return `<svg class="spark" viewBox="0 0 280 64" role="img" aria-label="${esc(t("historyChart", first, last))}">
    <path d="${path}"/>${pts.map(([d, p]) => `<circle cx="${X(d)}" cy="${Y(p)}" r="3"/>`).join("")}
    <text x="8" y="11">${esc(first)}</text><text x="272" y="11" text-anchor="end">${esc(last)}</text>
    <text class="d" x="8" y="62">${esc(day(pts[0][0]))}</text><text class="d" x="272" y="62" text-anchor="end">${esc(day(end))}</text></svg>`;
}

// Where this listing's price per ㎡ sits among similar ones in the loaded data: same area and type (resale: built
// within 10 years of it). The bar runs from their 5th to 95th percentile; ticks at the quartiles and (taller) the
// median, this listing a dot; a legend says so. Condos and land only (a house's price per ㎡ of building is mostly its land).
function market(it) {
  if (!it.unit || !(isCondo(it.type) || it.type === "land")) return "";
  const band = it.type.startsWith("used") && it.ageMonths != null, units = [];
  for (const h of Filter.partOf(it.h)?.items || []) {
    const unit = get.unit(h), months = band && get.ageMonths(h);
    if (unit && get.area(h) === it.area && get.type(h) === it.type && (!band || (months != null && Math.abs(months - it.ageMonths) <= 120))) units.push(unit);
  }
  if (units.length < 10) return "";
  units.sort((a, b) => a - b);
  const at = (f) => units[Math.round(f * (units.length - 1))], lo = at(0.05), hi = at(0.95), mid = at(0.5);
  const pos = (v) => Math.max(0, Math.min(100, hi > lo ? ((v - lo) / (hi - lo)) * 100 : 50)).toFixed(1);
  const diff = Math.round((it.unit / mid - 1) * 100), where = place(it.areaName), kind = typeName(it.type);
  return `<section class="d-sec market"><h3>${esc(t("market"))}</h3>
    <div class="mbar" aria-hidden="true"><i style="left:${pos(at(0.25))}%"></i><i class="mid" style="left:${pos(mid)}%"></i><i style="left:${pos(at(0.75))}%"></i><b style="left:${pos(it.unit)}%"></b></div>
    <div class="mscale" aria-hidden="true"><span>${esc(t("perM2", lo))}</span><span>${esc(t("perM2", hi))}</span></div>
    <div class="mlegend" aria-hidden="true"><span><b></b>${esc(t("marketSelf"))}</span><span><i class="mid"></i>${esc(t("marketMid"))}</span><span><i></i>${esc(t("marketQuart"))}</span></div>
    <p>${esc(Math.abs(diff) < 3 ? t("marketSame", where, kind) : t("marketDiff", where, kind, Math.abs(diff), diff < 0))}</p>
    <p class="meta">${esc(t("marketN", num(units.length), band))}</p></section>`;
}
