// The filters: one panel, a rail ≥1280 px (can be hidden, remembered), a modal sheet elsewhere (an overlay). Choice
// lists, counts per choice (Filter.facets), sections drawn on demand (エリア, こだわり, 周辺環境).

import { $, WIDE, esc, icon } from "./util.js";
import {
  PLANS, TSUBO, ageLabel, areaName, feature, isCondo, money, num, place, planLabel, saveSettings, settings,
  stationName, t, typeName,
} from "./format.js";
import { st } from "./state.js";
import { hashFor, nearKinds } from "./url.js";
import { activeConditions, dataVersion, facetCache, kept, render, toSearch, update } from "./results.js";
import { prefsLabel } from "./prefs.js";
import { inView } from "./map.js";
import { panelDefault, setPanel } from "./panel.js";
import { closeOverlay, isOpen, openOverlay, pageInert } from "./overlays.js";

const PRICES = [1000, 2000, 3000, 4000, 5000, 6000, 7000, 8000, 10000, 12000, 15000, 20000, 30000].map((m) => m * 1e4);
const SIZES = [40, 50, 60, 70, 80, 100, 120];
const LANDS = [50, 80, 100, 120, 150, 200];
const AGES = [0, 5, 10, 20, 30];
const WALKS = [5, 7, 10, 15, 20];
const NEAR_WALKS = [5, 10, 15];  // 周辺環境: minutes on foot
const FEES = [15000, 20000, 25000, 30000, 35000, 40000, 50000];  // condos' monthly fees, yen
const PICKS = ["freehold", "south", "upper", "top", "nofee", "ready", "noCondition", "tower", "parking"];  // こだわり
export const QUICK = ["newOnly", "dropsOnly"];  // also one tap away above the results

export const railOn = () => WIDE.matches && !settings.railOff;  // desktop: the filter rail, unless hidden (remembered)
export const filtersVisible = () => railOn() || document.body.classList.contains("show-filters");
const CHOICES = { priceMin: PRICES, priceMax: PRICES, plan: PLANS.map(([v]) => v), sizeMin: SIZES, landMin: LANDS,
  ageMax: AGES, walk: WALKS, feesMax: FEES };
const openFolds = new Set();  // the collapsed sections (エリア, こだわり) the user opened

function chipBtn(attrs, label, n, on) {
  return `<button class="chip" ${attrs} aria-pressed="${on}"${!n && !on ? " disabled" : ""}>${esc(label)}${n != null ? `<span class="n">${num(n)}</span>` : ""}</button>`;
}
const many = (field, v, label, n) => chipBtn(`data-many="${field}" data-value="${esc(v)}"`, label, n, st.q[field].includes(v));
const one = (field, v, label, n) => chipBtn(`data-one="${field}" data-value="${v}"`, label, n, st.q[field] === v);
const flag = (field, n) => chipBtn(`data-flag="${field}"`, t("flags")[field], n, st.q[field]);
// A value set by a link or a typed search that isn't one of the choices: shown as an extra choice (no count).
const withExtra = (field, values) => (st.q[field] == null || values.includes(st.q[field]) ? values : [...values, st.q[field]].sort((a, b) => a - b));
const selectEl = (field, values, fmt, none, label) => `<select class="select" data-num="${field}" aria-label="${esc(label)}"><option value="">${esc(none)}</option>${
  withExtra(field, values).map((v) => `<option value="${v}"${st.q[field] === v ? " selected" : ""}>${esc(fmt(v))}${values.includes(v) ? esc(t("paren", num(st.facets[field][v] || 0))) : ""}</option>`).join("")}</select>`;
const select = (field, label, values, fmt) => `<label class="field"><span>${esc(label)}</span>${selectEl(field, values, fmt, t("any"), label)}</label>`;
// The share of the chosen prefectures' listings whose page was fetched (conditions from it only cover those).
function detailShare() {
  let n = 0, sum = 0;
  for (const [slug, , c, share] of st.manifest.prefs) if (st.chosen.includes(slug) && typeof share === "number") { n += c; sum += share * c; }
  return n ? sum / n : null;
}
function nearHtml() {
  const kinds = nearKinds(), walk = st.q.nearWalk ?? Filter.NEAR_WALK;
  const toggle = (k) => (st.q.near.includes(k) ? st.q.near.filter((x) => x !== k) : [...st.q.near, k]);
  const patches = Object.fromEntries([...kinds.map((k) => [`k${k}`, { near: toggle(k) }]),
    ...(st.q.near.length ? NEAR_WALKS.map((w) => [`w${w}`, { nearWalk: w }]) : [])]);
  const n = Filter.counts(st.db, st.q, patches);
  return `<p class="hint">${esc(t("nearHint"))}</p>
    <div class="opts">${kinds.map((k) => many("near", k, t("nearKinds")[k] || k, st.q.near.includes(k) ? null : n[`k${k}`])).join("")}</div>
    ${st.q.near.length ? `<div class="opts">${NEAR_WALKS.map((w) => chipBtn(`data-one="nearWalk" data-value="${w}"`, t("walkWithin", w), walk === w ? null : n[`w${w}`], walk === w)).join("")}</div>` : ""}`;
}
// A flag of the index: an old cached index may not have the newer ones.
const flagKnown = (f) => f === "freehold" || f === "noCondition" || st.db.flags[f] != null;
const count = (field, v) => (CHOICES[field].includes(v) ? st.facets[field][v] || 0 : null);  // none for an extra value

export function renderFilters() {
  const key = `${dataVersion}|${JSON.stringify({ ...st.q, sort: "" })}`;  // the sort doesn't change any count
  if (key !== st.facetsFor) { st.facets = kept(facetCache, key, () => Filter.facets(st.db, st.q, CHOICES)); st.facetsFor = key; }
  const body = $("filters-body");
  if (!body.firstChild) {  // sections are built once; their contents are redrawn (the station search keeps focus)
    body.innerHTML = ["text", "types", "price", "plan", "areas", "stations", "near", "size", "age", "extra"].map((s) =>
      `<section class="sec" data-sec="${s}"></section>`).join("");
    body.querySelector('[data-sec="stations"]').innerHTML = `<h3>${esc(t("sec.stations"))}</h3>
      <p class="hint" id="walk-from"></p><div class="opts" id="walk-list"></div>
      <input class="text-input" id="station-q" type="search" placeholder="${esc(t("stationSearch"))}" aria-label="${esc(t("stationSearch"))}" autocomplete="off">
      <div class="opts" id="station-list"></div>`;
  }
  const narrows = (n) => n < st.facets.total;  // a choice every result has changes nothing: not shown
  const sec = (s, html) => { body.querySelector(`[data-sec="${s}"]`).innerHTML = html; };
  const h3 = (k, hint) => `<h3>${esc(t(`sec.${k}`))}${hint ? `<span class="hint">${esc(hint)}</span>` : ""}</h3>`;
  const isLand = st.q.types.length && st.q.types.every((type) => type === "land");
  const any = (fn) => !st.q.types.length || st.q.types.some(fn), condos = any(isCondo), others = any((type) => !isCondo(type));
  // a section that opens on demand, saying what's chosen in it (its contents are drawn once it's open)
  const fold = (k, summary, html) => `<details class="fold" data-fold="${k}"${openFolds.has(k) ? " open" : ""}><summary><h3>${esc(t(`sec.${k}`))}</h3>${
    `<span class="fold-sum${summary ? " on" : ""}">${esc(summary || t("any"))}</span>`}</summary>${openFolds.has(k) ? `<div class="fold-body" data-drawn="1">${html()}` : '<div class="fold-body">'}</div></details>`;
  const list = (labels) => labels.slice(0, 3).join(", ") + (labels.length > 3 ? t("andMore", labels.length - 3) : "");
  sec("text", st.q.text ? `<div class="opts"><button class="chip" aria-pressed="true" data-cleartext="1">${esc(t("textFilter", st.q.text))}${icon("x")}</button></div>` : "");
  sec("types", `${h3("types")}<div class="opts">${Object.keys(t("types")).filter((type) => st.db.index.types.includes(type)).map((type) => many("types", type, typeName(type), st.facets.types[type] || 0)).join("")}</div>`);
  sec("areas", fold("areas", list(st.q.areas.map(areaName)), areaGroups));
  sec("price", `${h3("price")}<div class="field-pair">${selectEl("priceMin", PRICES, (v) => money(v), t("minNone"), t("priceFrom"))}<span aria-hidden="true">${
    esc(t("to"))}</span>${selectEl("priceMax", PRICES, (v) => money(v), t("maxNone"), t("priceTo"))}</div>`);
  sec("plan", isLand ? "" : `${h3("plan", t("sec.planHint"))}<div class="opts">${withExtra("plan", CHOICES.plan).map((v) =>
    one("plan", v, t("planFrom", planLabel(v)), count("plan", v))).join("")}</div>`);
  sec("size", `${h3("size")}${isLand ? "" : select("sizeMin", t("floorArea"), SIZES, (v) => t("sizeMin", v))}${
    st.q.types.length && st.q.types.every(isCondo) ? "" : select("landMin", t("landArea"), LANDS, (v) => t("landMin", v, Math.round(v / TSUBO)))}`);
  sec("age", isLand ? "" : `${h3("age")}<div class="opts">${withExtra("ageMax", AGES).map((v) => one("ageMax", v, ageLabel(v), count("ageMax", v))).join("")}${
    flag("post1981", st.facets.post1981)}</div>`);
  stationList();
  $("walk-from").textContent = t(st.q.stations.length ? "walkFromChosen" : "walkFromNearest");
  $("walk-list").innerHTML = withExtra("walk", WALKS).filter((w) => st.q.walk === w || narrows(st.facets.walk[w] || 0)).map((w) => one("walk", w, t("walkWithin", w), count("walk", w))).join("");
  const tagNames = Object.keys(st.facets.features).sort((a, b) => st.facets.features[b] - st.facets.features[a]);
  const shownTags = [...st.q.features, ...tagNames.filter((f) => !st.q.features.includes(f) && narrows(st.facets.features[f])).slice(0, 30)];
  const tagChip = (f) => many("features", f, feature(f), st.facets.features[f] || 0);
  // こだわり: the curated flags that fit the chosen types, condos' fees, then SUUMO's tags
  const fits = { top: condos, upper: condos, tower: condos, parking: others, noCondition: others };
  const share = detailShare();
  const extras = activeConditions().filter(([, , s]) => s === "extra").map(([label]) => label);
  sec("extra", fold("extra", list(extras), () => `${share != null && share < 0.9 ? `<p class="hint share">${esc(t("detailShare", Math.round(share * 100)))}</p>` : ""}
    <div class="opts">${PICKS.filter((f) => st.q[f] || (flagKnown(f) && fits[f] !== false)).map((f) => flag(f, st.facets[f])).join("")}</div>
    ${condos || st.q.feesMax != null ? select("feesMax", t("feesLabel"), FEES, (v) => t("feesMax", v)) : ""}
    ${shownTags.length ? `<details class="group"${st.q.features.length ? " open" : ""}><summary>${esc(t("moreTags"))}</summary><div class="opts">${shownTags.map(tagChip).join("")}</div></details>` : ""}`));
  sec("near", nearKinds().length ? fold("near", st.q.near.length ? t("chipNear", st.q.near.map((k) => t("nearKinds")[k] || k).join(t("sep")), st.q.nearWalk ?? Filter.NEAR_WALK) : "", nearHtml) : "");
  $("apply").textContent = !WIDE.matches && st.view && st.mode === "search" ? t("showInView", num(Filter.count({ ...st.db, items: inView() }, st.q)))
    : t("show", num(st.facets.total));
  $("clear").disabled = !activeConditions().length;
  $("filters-scope").textContent = t("countsScope", ...prefsLabel(st.chosen));  // (the list counts the map's view)
}

function stationList() {
  const typed = $("station-q").value.trim(), counts = st.facets.stations, rank = new Map();
  if (typed) for (const s of st.db.index.stations) rank.set(s, Filter.stationMatch(st.db, s, typed));
  const found = typed ? st.db.index.stations.filter((s) => rank.get(s) < 3) : Object.keys(counts);
  const names = found.filter((s) => !st.q.stations.includes(s) && counts[s])  // stations with no homes left aren't offered
    .sort((a, b) => (rank.get(a) ?? 0) - (rank.get(b) ?? 0) || (counts[b] || 0) - (counts[a] || 0)).slice(0, 24);
  $("station-list").innerHTML = [...st.q.stations, ...names].map((s) => many("stations", s, t("station", stationName(s)), counts[s] || 0)).join("")
    || `<span class="meta">${esc(t(found.length ? "noStationHomes" : "noStation"))}</span>`;
}

// Areas grouped as the index says (23区, a designated city's wards, cities, towns), prefecture by prefecture. A
// group's chips are drawn when it's open (all of Japan has some 1,900 areas).
let areaGroupList = [];  // per group: its areas as [code, label]
function areaGroups() {
  const prefs = new Set(st.db.index.areas.map((a) => a[2])), kinds = t("kinds");
  const groups = new Map();
  const rank = (pref) => st.manifest.prefs.findIndex(([, name]) => name === pref);
  const order = (a) => [rank(a[2]), a[3] === "ku" ? 0 : a[3] === "shi" ? 2 : a[3] === "gun" ? 3 : 1, a[0]];
  const sorted = [...st.db.index.areas].sort((a, b) => { const x = order(a), y = order(b); return x[0] - y[0] || x[1] - y[1] || x[2].localeCompare(y[2]); });
  for (const [code, name, pref, group] of sorted) {
    const city = !kinds[group];  // a designated city: its wards without the city's name
    const g = `${prefs.size > 1 ? place(pref) + " " : ""}${city ? place(group) : kinds[group]}`;
    if (!groups.has(g)) groups.set(g, []);
    groups.get(g).push([code, city ? place(name).replace(group, "") || place(name) : place(name)]);
  }
  areaGroupList = [...groups.values()];
  return [...groups].map(([g, areas], i) => {
    const open = i === 0 || openGroups.has(g) || areas.some(([code]) => st.q.areas.includes(code));
    return `<details class="group" data-group="${i}" data-name="${esc(g)}"${open ? " open" : ""}><summary>${esc(g)}</summary><div class="opts">${open ? areaChips(i) : ""}</div></details>`;
  }).join("");
}
const areaChips = (i) => areaGroupList[i].map(([code, label]) => many("areas", code, label, st.facets.areas[code] || 0)).join("");
const openGroups = new Set();  // the area groups the user opened (they stay open as the filters are redrawn)

export function wireFilters() {
  $("filters-open").addEventListener("click", () => openFilters(!railOn()));
  $("filters-close").addEventListener("click", () => openFilters(false));
  $("apply").addEventListener("click", () => openFilters(false));
  $("clear").addEventListener("click", clearAll);
  $("filters-body").addEventListener("click", onFilterClick);
  $("filters-body").addEventListener("change", (e) => {
    const f = e.target.dataset.num;
    if (f) {
      st.q[f] = e.target.value === "" ? null : +e.target.value;
      update(false);
      $("filters-body").querySelector(`select[data-num="${f}"]`)?.focus();  // redrawn: keep the keyboard there
    }
  });
  $("filters-body").addEventListener("input", (e) => { if (e.target.id === "station-q") stationList(); });
  $("filters-body").addEventListener("toggle", (e) => {  // (toggle doesn't bubble: caught on the way down)
    const el = e.target, k = el.dataset?.fold, g = el.dataset?.group;
    if (k) {
      if (el.open) openFolds.add(k); else openFolds.delete(k);
      if (el.open && !el.querySelector(".fold-body").dataset.drawn) {  // drawn now (the keyboard stays on it)
        const focused = el.contains(document.activeElement);
        renderFilters();
        if (focused) $("filters-body").querySelector(`[data-fold="${k}"] > summary`)?.focus();
      }
    } else if (g != null) {
      if (el.open) openGroups.add(el.dataset.name); else openGroups.delete(el.dataset.name);
      const box = el.querySelector(".opts");
      if (el.open && !box.firstChild) box.innerHTML = areaChips(+g);
    }
  }, true);
  $("filters-body").addEventListener("keydown", (e) => {  // Enter in the station search takes the first match
    if (e.target.id !== "station-q" || e.key !== "Enter") return;
    e.preventDefault();  // (else Enter also activates the chip that gets the focus, undoing it)
    $("station-list").querySelector('button[aria-pressed="false"]')?.click();
  });
}

function onFilterClick(e) {
  const b = e.target.closest("button[data-many], button[data-one], button[data-flag], button[data-cleartext]");
  if (!b) return;
  toSearch();
  const v = b.dataset.value;
  if (b.dataset.cleartext) st.q.text = "";
  else if (b.dataset.many) {
    const f = b.dataset.many;
    st.q[f] = st.q[f].includes(v) ? st.q[f].filter((x) => x !== v) : [...st.q[f], v];
  } else if (b.dataset.one) {
    const f = b.dataset.one;
    st.q[f] = st.q[f] === +v ? null : +v;
    if (f === "ageMax") st.q.post1981 = false;
  } else {
    st.q[b.dataset.flag] = !st.q[b.dataset.flag];
  }
  const focus = [...b.attributes].filter((a) => a.name.startsWith("data-")).map((a) => `[${a.name}="${CSS.escape(a.value)}"]`).join("");
  update(false);
  $("filters-body").querySelector(`button${focus}`)?.focus();
}

export function clearAll() {
  st.q = { ...Filter.emptyQuery(), sort: st.q.sort };
  toSearch();
  if ($("station-q")) $("station-q").value = "";
  update(false);
}

// On phones and tablets the filter panel is a modal sheet (an overlay): dialog semantics, the page behind inert.
export const FILTERS = {
  entry: "sheet", el: () => $("filters"), state: () => ({ sheet: true }), modal: () => true,
  show(open) { document.body.classList.toggle("show-filters", open); sheetSemantics(); },
  back() {  // keeping its changes (the entry gone back to keeps its own state)
    history.replaceState(history.state, "", hashFor());
    closeOverlay(FILTERS, true);
  },
  closed() { if (st.listStale) render(); $("filters-open").focus(); },
};
export function sheetSemantics() {
  const open = document.body.classList.contains("show-filters"), sheet = $("filters");
  if (WIDE.matches) { sheet.removeAttribute("role"); sheet.removeAttribute("aria-modal"); } else {
    sheet.setAttribute("role", "dialog");
    sheet.setAttribute("aria-modal", "true");
  }
  $("filters-open").setAttribute("aria-expanded", WIDE.matches ? railOn() : open);
  pageInert();
  const full = $("panel").dataset.sheet === "full";
  // peek: its head (on the layout: the map's credits sit above it too)
  $("panel").parentElement.style.setProperty("--peek", `${$("panel").querySelector(".panel-head").offsetHeight}px`);
  $("sheet-handle").setAttribute("aria-expanded", full);
  $("sheet-handle").setAttribute("aria-label", t(full ? "sheetCollapse" : "sheetExpand"));
  // desktop: the rail can be hidden, giving its width to the results
  document.body.classList.toggle("rail-off", WIDE.matches && !railOn());
  setPanel(settings.panel ?? panelDefault());
  $("filters-close").setAttribute("aria-label", t(WIDE.matches ? "filtersHide" : "close"));
}

// section: scroll the filters to it (a chip's label edits its condition)
export function openFilters(open, section) {
  if (WIDE.matches && railOn() !== open) {
    settings.railOff = !open;
    saveSettings();
    sheetSemantics();
    if (!open) { $("filters-open").focus(); return; }
  }
  if (!open) {  // the sheet: closed by going back (FILTERS.back)
    if (isOpen(FILTERS)) closeOverlay(FILTERS); else { sheetSemantics(); FILTERS.closed(); }
    return;
  }
  if (WIDE.matches) sheetSemantics(); else openOverlay(FILTERS);
  if (section) openFolds.add(section);
  renderFilters();
  const sec = section && $("filters-body").querySelector(`[data-sec="${section}"]`);
  if (sec) sec.scrollIntoView({ block: "start" }); else $("filters-body").scrollTop = 0;
  (WIDE.matches ? (sec || $("filters-body")).querySelector("button:not(:disabled), input, select") : $("filters-close"))?.focus();
}
