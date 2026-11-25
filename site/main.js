// The search page: a map and, beside it (a bottom sheet on phones and tablets), the list of the results in its view;
// listings open as tabs in that panel. Conditions live in the URL (shareable). The search rules are in filter.js,
// the strings in i18n.js; both are classic scripts (globals Filter, I18N), loaded before these modules.
//
// This module starts the page (loads data/index.json and the prefectures the URL asks for, then prefs.route) and
// wires the document-wide events: clicks (hearts, the gallery, listing links, then util.ACTIONS, which each module
// registers its own buttons in), history (back closes the top overlay, else the URL is routed), keys, the wheel.
// Nothing imports it: it runs last, once every module is evaluated.
//
// The modules (flat in site/, native ES modules; shared state in state.js `st`):
//   util       DOM and storage helpers, media queries, the click-action registry, toast, swipes
//   format     settings and strings (t), numbers, prices, dates, a listing's parts as text
//   state      the shared state and collections
//   url        URL <-> conditions
//   results    the results and the list (cards, chips, saved searches, quick chips)
//   searchbox  the search box's suggestions
//   filters-panel  the filters (rail ≥1280 px, sheet elsewhere)
//   prefs      the prefecture picker and loading the prefectures' indexes
//   map        the map, its markers and clusters
//   rail       railway lines and stations on the map
//   records    listing records from their buckets
//   listing    a listing's page, 比較, the photo gallery and viewer
//   panel      the panel's tabs, the bottom sheet, the divider
//   favorites  the shared saved list, pinned photos
//   overlays   the overlay stack (history entries, Escape, focus, inert)
//   settings   language, theme and the settings overlay

import { $, SPLIT, WIDE, esc, keep, ls, runAction, stored } from "./util.js";
import { t } from "./format.js";
import { listY, st } from "./state.js";
import { hashFor, prefParam, rawQuery, saveLast } from "./url.js";
import {
  drawn, keepSearches, listResults, more, render, renderUnplaced, restoreList, searchKey, searches, toggleFavorites,
  unkeepSearch, update,
} from "./results.js";
import { applyText, wireSearch } from "./searchbox.js";
import { FILTERS, openFilters, sheetSemantics, wireFilters } from "./filters-panel.js";
import {
  choosePrefs, loadPrefs, prefsFromHash, route, savedAway, showLoading, watchUpdates, wirePrefs,
} from "./prefs.js";
import { MAP_WAIT, earlyMap, map, mapReady, placeMap } from "./map.js";
import { record } from "./records.js";
import { galleryClick, wireGallery } from "./listing.js";
import { compareFavs, openTab, setSheet, toList, wireSheet, wireSplit } from "./panel.js";
import { reconcilePins, refreshFavs, toggleFav } from "./favorites.js";
import { closeOverlay, dropOverlay, isOpen, topOverlay, trapFocus } from "./overlays.js";
import { applyLanguage, applyTheme, wireSettings } from "./settings.js";

function wire() {
  wireSearch();
  wireSplit();
  wireSheet();
  wireFilters();
  wirePrefs();
  wireSettings();
  wireGallery();
  $("unplaced").addEventListener("toggle", renderUnplaced);
  $("sort").addEventListener("change", (e) => { st.q.sort = e.target.value; update(); });
  $("keep").addEventListener("click", () => {
    const key = searchKey(st.q), i = searches.findIndex((s) => s.c === key && !savedAway(s));
    if (i >= 0) unkeepSearch(i); else keepSearches(() => searches.push({ c: key, p: prefParam(st.chosen), seen: st.dataHour }), "searchKept");
  });
  $("compare").addEventListener("click", () => compareFavs());
  $("favs").addEventListener("click", toggleFavorites);
  $("fit").addEventListener("click", () => { st.fitNext = "results"; placeMap(); });
  document.querySelector(".brand").addEventListener("click", () => { st.fitNext = "results"; });  // a new search: all of it
  document.addEventListener("click", (e) => {
    if (e.target === document.body && document.body.classList.contains("show-filters")) return openFilters(false);  // backdrop
    const fav = e.target.closest("[data-fav]");
    if (fav) { e.preventDefault(); toggleFav(fav.dataset.fav); return; }
    if (galleryClick(e)) return;
    const link = e.target.closest("a[data-key]");
    if (link && !e.metaKey && !e.ctrlKey && !e.shiftKey) { e.preventDefault(); openTab(link.dataset.key); return; }
    const b = e.target.closest("button");
    if (b) runAction(b);
  });
  document.querySelectorAll(".skip").forEach((a) => a.addEventListener("click", (e) => {
    e.preventDefault();  // the hash holds the search
    const id = a.getAttribute("href").slice(1);
    if (id === "filters") openFilters(true);
    else {
      if (!SPLIT.matches && $("panel").dataset.sheet !== "half") setSheet("half");  // the map and the list both in sight
      $(id)?.focus();
    }
  }));
  window.addEventListener("popstate", () => {
    const o = topOverlay();  // back from an overlay: it closes
    if (o) { if (o.back) o.back(); else closeOverlay(o, true); return; }
    const before = st.mode;
    if (st.tab === "list") listY[st.mode] = $("results").scrollTop;
    route();
    if (st.mode !== before && st.tab === "list") restoreList();
  });
  document.addEventListener("keydown", (e) => {
    const dialog = topOverlay();
    if (dialog?.keys?.(e)) return;  // (the photo viewer's own)
    if (e.key === "Tab" && dialog) trapFocus(e, dialog.el());
    if (e.key === "/" && !dialog && !/^(INPUT|SELECT|TEXTAREA)$/.test(document.activeElement.tagName)) { e.preventDefault(); $("q").focus(); }
    if (e.key !== "Escape" || e.target.matches("#q, select, textarea")) return;  // (the search box has its own)
    const railPanel = $("map").querySelector(".rail-panel:not([hidden])");
    if ($("map").contains(e.target) && (railPanel || $("map").querySelector(".leaflet-popup"))) {  // a popup, the lines panel
      if (railPanel) { railPanel.hidden = true; $("map").querySelector(".rail-btn").focus(); }
      else { map.closePopup(); $("map").focus({ preventScroll: true }); }
    }
    else if (dialog) closeOverlay(dialog);
    else if (st.tab !== "list" && (e.target === document.body || $("panel").contains(e.target))) toList();
  });
  // a mouse wheel over a strip that scrolls sideways (tabs, chips, thumbnails: no scrollbar shown) scrolls it, while it
  // can go further that way (then the list scrolls as usual)
  document.addEventListener("wheel", (e) => {
    if (e.ctrlKey || Math.abs(e.deltaX) >= Math.abs(e.deltaY)) return;
    let strip = e.target.closest?.(".tabs, .g-chips, .g-strip, .searches, .chip-row");
    while (strip && strip.scrollWidth <= strip.clientWidth) strip = strip.parentElement.closest(".tabs, .g-chips, .g-strip, .searches, .chip-row");
    if (!strip) return;
    const d = e.deltaMode === 1 ? e.deltaY * 16 : e.deltaY, end = strip.scrollWidth - strip.clientWidth;
    if ((d < 0 && strip.scrollLeft <= 0) || (d > 0 && strip.scrollLeft >= end - 1)) return;
    e.preventDefault();
    strip.scrollLeft += d;
  }, { passive: false });
  // a card pressed: its record asked for already (the bucket's ~200 ms round trip before the click lands); not on
  // touch screens, where a press may start a scroll
  document.addEventListener("pointerdown", (e) => {
    if (e.pointerType === "touch") return;
    const key = e.target.closest?.("a[data-key]")?.dataset.key;
    if (key && st.manifest) record(key).catch(() => {});
  }, { passive: true });
  WIDE.addEventListener("change", () => {
    // a tablet turned past 1280 with the sheet open: the rail takes over (the sheet's history entry goes)
    if (WIDE.matches && isOpen(FILTERS)) {
      dropOverlay(FILTERS);
      if (history.state?.sheet) history.back();
    }
    sheetSemantics();
    render();
  });
  new IntersectionObserver((es) => { if (es[0].isIntersecting) more(); }, { root: $("results"), rootMargin: "800px" }).observe($("sentinel"));
}

async function start() {
  history.scrollRestoration = "manual";  // the list keeps its place itself (per tab and view)
  watchUpdates();
  mapReady().then(earlyMap);  // the map loads while the listings do
  setTimeout(() => { if (st.waiting) { st.waiting = false; if (drawn.search) listResults(); } }, MAP_WAIT);  // or list them all
  applyTheme();
  applyLanguage();
  $("list").innerHTML = '<div class="skeleton"></div>'.repeat(6);
  $("count").textContent = t("loading");
  try {
    st.manifest = await (await fetch("data/index.json")).json();
    const last = ls.get("suumo.last");
    if (!location.hash && last) history.replaceState(null, "", last);
    // a link to a listing (shared, from outside): the list under it, so the back button returns to the list
    const linked = new URLSearchParams(location.hash.slice(1));
    if (!history.state && linked.has("id")) {
      const url = location.href;
      linked.delete("id");
      history.replaceState(null, "", "#" + linked.toString());
      history.pushState({ tab: 1 }, "", url);
    }
    showLoading(prefsFromHash());
    await loadPrefs(prefsFromHash());
    choosePrefs(prefsFromHash());
    // where the map opens: the link's place (old links: the area they showed), its listing, else the results
    const p = new URLSearchParams(location.hash.slice(1)), m = p.get("m"), b = (p.get("b") || "").split(",").map(Number);
    if (/^-?[\d.]+,-?[\d.]+,\d+$/.test(m || "")) st.fitNext = { m: (st.mapAt = m) };
    else if (p.get("view") === "area" && b.length === 4 && b.every(Number.isFinite)) st.fitNext = { b };
    else if (p.get("id")) st.fitNext = { id: p.get("id") };
    refreshFavs();
    for (const s of searches) s.c = searchKey(rawQuery(new URLSearchParams(s.c)));  // in today's terms
    // a visit = a new data version: the previous one is what "since your last visit" compares with
    const visit = stored("suumo.visit", {}), now = +String(st.manifest.updated).replace(/\D/g, "").slice(0, 10) || null;
    if (now && visit.cur !== now) { visit.prev = visit.cur ?? null; visit.cur = now; keep("suumo.visit", visit); }
    st.lastVisit = visit.prev ?? null;
    st.dataHour = visit.cur ?? null;
  } catch {
    const [title, hint] = t("loadFailed");
    $("list").innerHTML = `<div class="empty"><h3>${esc(title)}</h3><p>${esc(hint)}</p></div>`;
    return;
  }
  const early = $("q").value.trim();  // typed while the listings were loading
  if (history.state?.viewer) history.replaceState({ ...history.state, viewer: undefined }, "", location.href);  // (a reload)
  wire();
  route();
  history.replaceState(history.state, "", hashFor());  // (old links: in today's terms)
  saveLast();
  if (early && !st.q.text) applyText(early);
  (window.requestIdleCallback || setTimeout)(() => reconcilePins().catch(() => {}));
}

start();
