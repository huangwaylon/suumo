// The results and the list: what's drawn depends, by level, on the results (search: their set and order), the
// map's markers (the set) and the list (the cards in the map's view). render() redoes each only when what it
// depends on changed. Also the condition chips, the quick chips (新着 / 値下げ / since the last visit), saved
// searches (localStorage suumo.searches, with their prefectures `p`), last search (suumo.last).

import { $, ACTIONS, WIDE, esc, icon, keep, recent, stored, toast, touch } from "./util.js";
import {
  FLAGS, PAGE, S, age, ageLabel, ago, areaName, feature, fees, get, item, listingLink, money, nowrapWalk, num,
  photoTag, planLabel, price, settings, showUsd, sizes, station, stationName, t, typeName, usd, value, weekday,
} from "./format.js";
import { byKey, favs, listY, loaded, st, tabs } from "./state.js";
import { hashFor, paramsFor, queryFrom, rawQuery, saveLast } from "./url.js";
import { altLabel, altSwaps, ignoredNote } from "./searchbox.js";
import { QUICK, clearAll, filtersVisible, openFilters, renderFilters } from "./filters-panel.js";
import { loadPrefs, prefName, prefsFromParam, renderPrefBar, route, savedAway } from "./prefs.js";
import { clusterSets, drawMap, inArea, inView } from "./map.js";
import { panelDefault, renderTabs, setPanel, showTab } from "./panel.js";

// Saved searches (this browser): [{c: conditions as URL parameters, p: the prefectures (p=; none in older ones:
// wherever they're opened), seen: dataHour when last opened}]
export const searches = [].concat(stored("suumo.searches", [])).filter((s) => typeof s?.c === "string");
export const searchKey = (query) => paramsFor({ ...query, sort: "new", since: null }).toString();
const saveSearches = () => keep("suumo.searches", searches);
// a change to the saved searches (the star, a chip's ×): kept, said, redrawn
export function keepSearches(change, said) {
  change();
  saveSearches();
  toast(t(said));
  renderSearches();
}
export const unkeepSearch = (i) => keepSearches(() => searches.splice(i, 1), "searchRemoved");

// What's drawn depends on, by level: the results (search: their set and order), the map's markers (the set, in any
// order), the list (the cards in view). render() redoes each only when what it depends on changed.
export let dataVersion = 0;          // the loaded listings: bumped when they change (what was drawn from them is stale)
export const drawn = { search: null, set: null, base: false };
const EMPTY = JSON.stringify({ ...Filter.emptyQuery(), sort: "" });
const unfiltered = () => st.mode === "search" && JSON.stringify({ ...st.q, sort: "" }) === EMPTY;
const setKey = () => JSON.stringify([dataVersion, st.mode, st.mode === "favorites" ? [...favs] : { ...st.q, sort: "" }]);
export const resultsKey = () => `${setKey()}|${st.q.sort}`;
// A few states are kept, so going back to one is instant: the latest two and the unfiltered one (clearing the
// conditions). cache: key -> {value, base}, least recently used first.
export function kept(cache, key, make, base = unfiltered()) {
  const e = touch(cache, key, () => ({ value: make(), base }));
  const keys = [...cache.keys()], lastBase = keys.findLast((k) => cache.get(k).base);
  for (const k of keys.slice(0, -2)) if (k !== lastBase) cache.delete(k);
  return e.value;
}
export const results = new Map(), facetCache = new Map();
let unplaced = null;          // the results without coordinates
export function dataChanged() {
  dataVersion++;
  results.clear();
  facetCache.clear();
  clusterSets.clear();
}

let awayFailed = false;  // the favorites' other prefectures couldn't be loaded (this page: no retry loop)
function compute() {
  if (st.mode === "search") return Filter.search(st.db, st.q);
  const away = [...new Set([...favs].map((k) => st.manifest.saved[k] ?? st.pending[k]?.[2]).filter((p) => p && !loaded.has(p)))];
  if (away.length && !awayFailed) {
    loadPrefs(away).then(() => { st.fitNext = "results"; render(); },
      () => { awayFailed = true; toast(t("offline")); });  // (once: not again at every render)
  }  // saved listings in prefectures not loaded yet
  return Filter.search(st.db, { ...Filter.emptyQuery(), sort: st.q.sort }, [...favs].map(byKey).filter((h) => h >= 0));
}

export function update(top = true) {
  if ([...st.q.areas, ...st.q.stations].some((x) => !st.lastPlaces.includes(x))) st.fitNext = "results";  // a place named: show it
  // the phone sheet's entry stays one (closing it goes back to the page, keeping the changes); a view's or a
  // listing's doesn't (going back would undo the changes)
  history.replaceState(history.state?.sheet ? history.state : null, "", hashFor());
  saveLast();
  if (top) listY[st.mode] = 0;
  render();
  if (top) $("results").scrollTop = 0;
}

export function render() {
  st.lastPlaces = [...st.q.areas, ...st.q.stations];
  if (document.body.classList.contains("show-filters") && !WIDE.matches) {
    st.listStale = true;  // only the sheet is visible: its counts are enough until it closes
    renderFilters();
    return;
  }
  st.listStale = false;
  const key = resultsKey();
  if (key !== drawn.search) {
    Object.assign(drawn, { search: key, set: setKey(), base: unfiltered() });
    st.mapHits = kept(results, key, compute, drawn.base);
    st.rank = unplaced = null;
  }
  if ($("q").value.trim() !== st.q.text && document.activeElement !== $("q")) $("q").value = st.q.text;  // (not while typing)
  $("sort").value = st.q.sort;
  st.byDay = st.mode === "search" && st.q.sort === "new" && !!(st.q.newOnly || st.q.since);  // what's new: a heading per day
  const active = activeConditions(), set = st.mode === "search" ? active.filter(([, , s]) => s !== "text").length : 0;
  $("filters-n").hidden = !set;  // the text has its own box
  $("filters-n").textContent = set;
  // its name is what it shows (条件 2); whether the filters are open is aria-expanded
  $("filters-open").setAttribute("aria-label", set ? `${t("filters")} ${set}` : t("filters"));
  $("favs").setAttribute("aria-pressed", st.mode === "favorites");
  document.body.classList.toggle("listmode", st.mode !== "search");
  setPanel(settings.panel ?? panelDefault());  // (the rail comes and goes with the favorites)
  renderPrefBar();
  renderSearches();
  // a ward read from the search text that is also a station (or the other way round) can be switched
  const swap = (ref) => { const j = ref ? altSwaps.findIndex((a) => a.field === ref[0] && a.value === ref[1]) : -1; return j < 0 ? "" :
    `<button class="chip-alt" data-alt="${j}" aria-label="${esc(t("swapTo", altLabel(altSwaps[j].alt)))}">⇄ ${esc(altLabel(altSwaps[j].alt))}</button>`; };
  $("chips").innerHTML = st.mode !== "search" ? "" : active.map(([label, , s, ref], i) => s === "quick" ? "" :
    `<span class="chip on"><button class="chip-label" data-edit="${s}">${esc(label)}</button>${swap(ref)}<button class="chip-x" data-remove="${i}" aria-label="${esc(t("remove", label))}">${icon("x")}</button></span>`).join("") +
    (ignoredNote.length ? `<span class="chip muted">${esc(t("ignored", ignoredNote.join(t("sep"))))}<button class="chip-x" data-ignored-x="1" aria-label="${esc(t("close"))}">${icon("x")}</button></span>` : "") +
    (active.length > 1 && st.mapHits.length ? `<button class="chip ghost" data-clear="1">${esc(t("clearAll"))}</button>` : "");
  drawMap();
  listResults();
  if (filtersVisible()) renderFilters();
}

// The list: the results in the map's view (favorites: all of them), with the count and the quick chips.
// moved: the map moved (a new place: the list starts from the top).
export function listResults(moved = false) {
  const here = st.mode === "search" && !!st.view;
  st.hits = here ? inArea(st.mapHits, st.view) : st.mapHits;
  const width = $("panel").clientWidth, short = width < 480;  // a narrow list: "1,234件 / 全5,678件" (style.css: the head in one row)
  // the cards' photo: 120 px wide in a list under 600 px (style.css), 240 px asked for unless the screen is dense
  st.cardW = width < 600 && devicePixelRatio < 1.5 ? 240 : 360;
  $("count").textContent = st.mode === "favorites" ? t("favBanner", num(st.hits.length)) : t(here && !short ? "inView" : "count", num(st.hits.length));
  // said aloud once the conditions changed (and the map followed them), not on every pan
  if (!moved && !st.sayTimer) st.sayTimer = setTimeout(() => { $("count-live").textContent = $("count").textContent; st.sayTimer = null; }, 700);
  $("fit").hidden = !here || !st.mapHits.length;
  $("fit").textContent = t("fitAll", num(st.mapHits.length));
  $("fit").setAttribute("aria-label", t("fitAllShow", num(st.mapHits.length)));
  $("sort").hidden = st.hits.length < 2;
  $("compare").hidden = st.mode !== "favorites" || st.hits.length < 2;
  renderQuick();
  renderTabs();
  if (st.waiting) return;  // the skeleton stays until the map has its view
  if (st.tab !== "list") { st.listStale = true; return; }  // (drawn when it's shown)
  // the same cards as already drawn (a sheet closed, a view switched back): kept as they are, with their place
  // (the ones further down are drawn from these hits as the list scrolls)
  const head = [S.lang, st.mode, st.byDay, st.q.stations, showUsd(), [...favs], Object.keys(st.pending), tabs, st.hits.length].join("|");
  let same = st.listSig?.head === head && st.hits.length > 0;
  for (let i = 0, n = st.byDay ? st.hits.length : Math.min(st.shown, st.hits.length); same && i < n; i++) same = st.hits[i] === st.listSig.hits[i];
  if (same) { st.listSig.hits = st.hits; return; }
  st.listSig = { head, hits: st.hits };
  const focused = $("list").contains(document.activeElement) && document.activeElement.closest("[data-key], [data-fav]");
  $("list").innerHTML = st.hits.length ? "" : st.mapHits.length ? `<div class="empty"><h3>${esc(t("viewEmpty"))}</h3>
    <button class="btn primary" data-fit="1">${esc(t("fitAllShow", num(st.mapHits.length)))}</button></div>` : emptyState(activeConditions());
  st.shown = 0;
  more();
  renderUnplaced();
  if (moved) $("results").scrollTop = 0;
  if (focused) {  // the keyboard stays on the same card, if it's still listed
    const key = focused.dataset.key || focused.dataset.fav;
    ($("list").querySelector(`[data-key="${CSS.escape(key)}"]`) || $("count")).focus({ preventScroll: true });
  }
}

// The results the map can't show (no coordinates): in every view, folded at the end of the list.
export function renderUnplaced() {
  const box = $("unplaced"), list = st.mode === "search" && st.view ? (unplaced ??= st.mapHits.filter((h) => get.lat(h) == null)) : [];
  box.hidden = !list.length;
  box.querySelector("summary").textContent = t("unplaced", num(list.length));
  box.querySelector(".grid").innerHTML = box.open ? Array.from(list, item).filter(Boolean).map((it) => card(it)).join("") : "";
}

// The favorites are a view of their own: a history entry (back returns to the search, its place and its map).
export function toggleFavorites() {
  if (history.state?.view) { history.back(); return; }  // undoing the switch: back to where it was
  if (st.tab === "list") listY[st.mode] = $("results").scrollTop; else showTab("list");
  const before = st.mode;
  st.mode = st.mode === "favorites" ? "search" : "favorites";
  modeChanged(before);
  history.pushState({ view: 1 }, "", hashFor());
  render();
  restoreList();
}
// Entering the favorites fits the map to them; leaving puts the search's map back.
let searchAt = null;
// a condition changed in the favorites view: back to the search (and its map)
export function toSearch() { const before = st.mode; st.mode = "search"; modeChanged(before); }
export function modeChanged(before) {
  if (before === st.mode) return;
  if (st.mode === "favorites") { searchAt = st.mapAt; st.fitNext = "results"; } else if (searchAt) st.fitNext = { m: searchAt };
}
export function restoreList() {
  const y = listY[st.mode] || 0;
  while (st.shown < st.hits.length && $("list").offsetHeight < y + $("results").clientHeight) more();
  $("results").scrollTop = y;
}

export function more() {
  if (st.waiting || st.shown >= st.hits.length) return;  // (the map's view decides what's listed: not yet)
  let html = "";
  for (let i = st.shown; i < Math.min(st.shown + PAGE, st.hits.length); i++) {
    const d = get.newDate(st.hits[i]);
    if (st.byDay && (i === 0 || get.newDate(st.hits[i - 1]) !== d)) {  // a heading for each day (then the price cuts)
      let n = 0;
      for (let j = i; j < st.hits.length && get.newDate(st.hits[j]) === d; j++) n++;
      html += `<h3 class="day">${esc(d ? weekday(d) : t("tag.dropped"))}<span>${esc(t("count", num(n)))}</span></h3>`;
    }
    const it = item(st.hits[i]);  // (null: a released index's, until the redraw)
    if (it) html += card(it, i < 4);  // the first photos are in sight: not lazy
  }
  $("list").insertAdjacentHTML("beforeend", html);
  st.shown += PAGE;
}

export function tags(it) {
  const F = st.db.flags, out = [];
  if (it.flags & F.gone) out.push(`<span class="tag gone">${t("tag.gone")}</span>`);
  if (st.pending[it.key]) out.push(`<span class="tag warn">${t(st.pending[it.key][0] ? "tag.pending" : "tag.pendingRemove")}</span>`);
  if (it.flags & F.new) {  // under a day heading the date is said already: only the hours, today
    const when = st.byDay && it.newDate && !/[時h]/.test(ago(it.newAt)) ? "" : ago(it.newAt);
    out.push(`<span class="tag new">${t(it.flags & F.relisted ? "tag.relisted" : "tag.new")} ${esc(when)}</span>`);
  }
  if (it.flags & F.dropped) {
    const pct = it.prevPrice && it.price ? Math.round((1 - it.price / it.prevPrice) * 100) : 0;
    out.push(`<span class="tag drop">${t("tag.dropped")}${pct ? ` −${pct}%` : ""}</span>`);
  }
  if (it.flags & F.leasehold) out.push(`<span class="tag warn">${t("tag.leasehold")}</span>`);
  if (it.flags & F.conditional) out.push(`<span class="tag warn">${t("tag.conditional")}</span>`);
  if (it.others) out.push(`<span class="tag">${t("others", it.others)}</span>`);
  return out.join("");
}

function card(it, eager = false) {
  const spec = [value(it.layout), ...sizes(it), age(it)].filter(Boolean).map((s) => `<span>${esc(s)}</span>`).join("");
  return `<article class="card${tabs.includes(it.key) ? " open" : ""}">${listingLink(it, `
    <div class="ph">${it.image != null ? photoTag(it, st.cardW, st.cardW * 3 / 4, { eager }) : ""}${
      it.photos > 1 ? `<span class="ph-n" aria-label="${esc(t("photosN", it.photos))}">${icon("camera")}${it.photos}</span>` : ""}</div>
    <div class="body">
      <div class="price num">${price(it, true)}${usd(it)}</div>
      <div class="spec">${spec}</div>
      <div class="meta wrap"><span class="kind">${esc(typeName(it.type))}</span> · ${nowrapWalk(station(it))}</div>
      <div class="meta">${esc([S.lang === "en" && it.town ? `${areaName(it.area)} · ${it.town.slice(it.areaName.length)}` : it.town, it.name].filter(Boolean).join(" · "))}</div>
      ${fees(it) ? `<div class="meta fees">${esc(fees(it))}</div>` : ""}
      <div class="tags">${tags(it)}</div>
    </div>`)}
    <button class="fav" data-fav="${esc(it.key)}" aria-pressed="${favs.has(it.key)}" aria-label="${esc(t("favFor", money(it.price), it.layout, it.town || it.areaName))}">${icon("heart")}</button></article>`;
}

function emptyState(active) {
  if (st.mode !== "search" || (st.byDay && active.every(([, , s]) => s === "quick"))) {
    const [title, hint] = t(st.mode !== "search" ? "favEmpty" : "dailyEmpty");
    return `<div class="empty"><h3>${esc(title)}</h3><p>${esc(hint)}</p></div>`;
  }
  // what each condition's removal would show, in one pass: each removal as the fields it changes
  const patches = {};
  active.forEach(([, remove], i) => {
    const without = remove(structuredClone(st.q));
    patches[i] = Object.fromEntries(Object.keys(st.q).filter((k) => JSON.stringify(st.q[k]) !== JSON.stringify(without[k])).map((k) => [k, without[k]]));
  });
  const counts = Filter.counts(st.db, st.q, patches);
  const loosen = active.map(([label], i) => (counts[i]
    ? `<button class="chip" data-remove="${i}">${esc(t("remove", label))}<span class="n">${t("count", num(counts[i]))}</span></button>` : "")).join("");
  return `<div class="empty"><h3>${esc(t("emptyTitle"))}</h3><p>${esc(t("emptyHint"))}</p>
    <div class="chips">${loosen}</div>${active.length ? `<button class="btn primary" data-clear="1">${esc(t("clearAll"))}</button>` : ""}</div>`;
}

const freshCounts = new Map(), MAX_COUNTS = 100;  // (the saved searches' counts: key -> count)
// The saved searches, each with what's new or cheaper since it was last opened; the star saves the current one.
function renderSearches() {
  const box = $("searches"), key = searchKey(st.q), here = st.mode === "search";
  const label = (query) => activeConditions(query).filter(([, , s]) => s !== "quick").map(([l]) => l).slice(0, 3).join(t("sep"));
  box.hidden = !here || !searches.length;
  if (!box.hidden) {
    box.setAttribute("aria-label", t("savedSearches"));
    box.innerHTML = searches.map((s, i) => {
      const away = savedAway(s), query = away ? rawQuery(new URLSearchParams(s.c)) : queryFrom(new URLSearchParams(s.c));
      // the counts change only with the data and when it was seen; another prefecture's aren't loaded: none
      const k = `${st.chosen}|${dataVersion}|${s.seen}|${s.c}`;
      const n = s.seen && !away ? recent(freshCounts, k, MAX_COUNTS, () => Filter.count(st.db, { ...query, since: s.seen })) : 0;
      const where = away ? `${prefsFromParam(s.p).map(prefName).join(t("sep"))}: ` : "";
      const name = where + (label(query) || t("allListings"));
      return `<span class="chip saved${s.c === key && !away ? " on" : ""}"><button class="chip-label" data-saved-search="${i}">${esc(name)}</button>${
        n ? `<span class="n fresh">${esc(t("freshN", num(n)))}</span>` : ""}<button class="chip-x" data-unsave-search="${i}" aria-label="${esc(t("unkeepSearch"))}: ${esc(name)}">${icon("x")}</button></span>`;
    }).join("");
  }
  const keep = $("keep"), saved = searches.some((s) => s.c === key && !savedAway(s));
  keep.hidden = !here || !activeConditions().some(([, , s]) => s !== "quick" && s !== "text");
  keep.setAttribute("aria-pressed", saved);
  keep.setAttribute("aria-label", t(saved ? "unkeepSearch" : "keepSearch"));
}

// the list's buttons: conditions, saved searches, quick chips
Object.assign(ACTIONS, {
  remove(i) {  // a condition's ×
    activeConditions()[+i][1](st.q);
    update();
    ($("chips").querySelector(".chip-x") || $("count")).focus();
  },
  clear: () => clearAll(),
  edit: (section) => openFilters(true, section),  // a condition's label
  savedSearch(i) {  // open a saved search: what's new in it, if anything; then seen
    const s = searches[+i];
    if (savedAway(s)) {  // another prefecture's: there (route loads it), newest first
      const p = new URLSearchParams(s.c);
      p.set("p", s.p);
      s.seen = st.dataHour;
      saveSearches();
      history.replaceState(history.state, "", "#" + p.toString());
      st.prefsUrl = location.href;
      st.fitNext = "results";
      route();
      return;
    }
    const query = queryFrom(new URLSearchParams(s.c));
    const fresh = s.seen && Filter.count(st.db, { ...query, since: s.seen });
    st.q = { ...query, sort: "new", since: fresh ? s.seen : null };
    s.seen = st.dataHour;
    saveSearches();
    st.fitNext = "results";
    update();
  },
  unsaveSearch: (i) => unkeepSearch(+i),
  quick(f) {  // 新着 / 値下げ / since the last visit
    st.q[f] = st.q[f] ? Filter.emptyQuery()[f] : f === "since" ? st.lastVisit : true;
    if (f === "dropsOnly") st.q.sort = st.q[f] ? "drop_desc" : "new";  // price cuts: biggest first
    else if (st.q[f]) st.q.sort = "new";                               // what's new: by day, newest first
    update();
  },
});

// 新着 / 値下げ / since the last visit, one tap each, with what they'd show under the current conditions.
function renderQuick() {
  const box = $("quick");
  box.hidden = st.mode !== "search";
  if (box.hidden) return;
  const offers = { newOnly: true, dropsOnly: true, ...(st.lastVisit ? { since: st.lastVisit } : {}) }, patches = {};
  for (const [f, v] of Object.entries(offers)) if (st.q[f] !== v) patches[f] = { [f]: v };
  // In view, in one pass. Each offer only adds a condition to q (newOnly, dropsOnly; since unless another since is
  // in force, which it would replace), so what it would show is among the results in view (hits): counted there,
  // rather than over every listing in view (that was the work at every pan).
  const narrows = st.q.since == null || !patches.since;
  const counts = Filter.counts(narrows ? { ...st.db, items: st.hits } : st.view ? { ...st.db, items: inView() } : st.db, st.q, patches);
  const chip = (field, label, value) => {
    const on = st.q[field] === value, n = on ? st.hits.length : counts[field];
    return n || on ? `<button class="chip" data-quick="${field}" aria-pressed="${on}">${esc(label)}<span class="n">${num(n)}</span></button>` : "";
  };
  box.innerHTML = chip("newOnly", t("quickNew"), true) + chip("dropsOnly", t("quickDrop"), true) +
    (st.lastVisit ? chip("since", t("sinceVisit"), st.lastVisit) : "");
}

// The conditions in force, as [label, remove(query), filter section] for the chips and the empty state.
export function activeConditions(of = st.q) {
  // remove(query) takes the condition out of the given query (and returns it)
  const out = [], drop = (f, v) => (qq) => { qq[f] = qq[f].filter((x) => x !== v); return qq; };
  const reset = (...fs) => (qq) => { const e = Filter.emptyQuery(); for (const f of fs) qq[f] = e[f]; return qq; };
  if (of.text) out.push([t("chipText", of.text), reset("text"), "text"]);
  for (const type of of.types) out.push([typeName(type), drop("types", type), "types"]);
  for (const a of of.areas) out.push([areaName(a), drop("areas", a), "areas", ["areas", a]]);
  if (of.priceMin != null || of.priceMax != null) {
    out.push([t("chipPrice", of.priceMin != null ? money(of.priceMin) : "", of.priceMax != null ? money(of.priceMax) : ""),
      reset("priceMin", "priceMax"), "price"]);
  }
  if (of.plan != null) out.push([t("chipPlan", planLabel(of.plan)), reset("plan"), "plan"]);
  if (of.sizeMin != null) out.push([t("sizeMin", of.sizeMin), reset("sizeMin"), "size"]);
  if (of.landMin != null) out.push([t("chipLand", of.landMin), reset("landMin"), "size"]);
  if (of.ageMax != null) out.push([ageLabel(of.ageMax), reset("ageMax"), "age"]);
  for (const s of of.stations) out.push([t("station", stationName(s)), drop("stations", s), "stations", ["stations", s]]);
  if (of.walk != null) out.push([t("walkWithin", of.walk), reset("walk"), "stations"]);
  for (const f of FLAGS) {
    if (of[f]) out.push([t("flags")[f], reset(f), f === "post1981" ? "age" : QUICK.includes(f) ? "quick" : "extra"]);
  }
  if (of.feesMax != null) out.push([t("chipFees", of.feesMax), reset("feesMax"), "extra"]);
  if (of.near.length) out.push([t("chipNear", of.near.map((k) => t("nearKinds")[k] || k).join(t("sep")), of.nearWalk ?? Filter.NEAR_WALK), reset("near", "nearWalk"), "near"]);
  if (of.since) out.push([t("sinceVisit"), reset("since"), "quick"]);
  for (const f of of.features) out.push([feature(f), drop("features", f), "extra"]);
  return out;
}
