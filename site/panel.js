// The panel: its tabs (the list, the listings opened from it, 比較; opening one from the list is a history entry),
// the bottom sheet below 1024 px (peek/half/full, settings.sheet), the divider on wide screens (settings.panel).

import { $, ACTIONS, SPLIT, esc, icon } from "./util.js";
import { get, money, num, safeUrl, saveSettings, settings, t, typeName, value } from "./format.js";
import { MAX_TABS, favs, isListing, listY, listing, st, tabs } from "./state.js";
import { hashFor } from "./url.js";
import { render, restoreList } from "./results.js";
import { railOn, sheetSemantics } from "./filters-panel.js";
import { map, markOpen, onMapMove } from "./map.js";
import { compareHtml, renderListing } from "./listing.js";

// Opening a listing from the list is a history entry (back returns to the list); switching and closing tabs aren't.
export const addTabs = (keys) => { for (const k of keys) if (!tabs.includes(k)) tabs.push(k); tabs.splice(0, Math.max(0, tabs.length - MAX_TABS)); };

// A listing (from a card, the map, the strip) or 比較.
export function openTab(key) {
  if (key === "list") return toList();
  if (isListing(key)) addTabs([key]);
  map?.closePopup();  // (opened from a popup: the ring shows where it is)
  const from = st.tab;
  if (from === "list") st.openedFrom = key;
  showTab(key);
  if (from === "list") history.pushState({ tab: 1 }, "", hashFor()); else history.replaceState(history.state, "", hashFor());
}
// Favorites side by side: they open as tabs (the newest twelve), shown in 比較.
export function compareFavs(push = true) {
  addTabs(Array.from(st.hits.subarray(0, MAX_TABS), get.key));
  if (push) openTab("compare"); else showTab("compare");
}
// drawn again (another language, USD on or off)
export function rerenderTab() {
  $("panel-body").dataset.key = "";
  showTab(st.tab);
}

function closeTab(key) {
  if (key === "all") tabs.length = 0; else if (tabs.includes(key)) tabs.splice(tabs.indexOf(key), 1);
  if (st.tab !== "list" && (key === "all" || key === st.tab || (st.tab === "compare" && tabs.length < 2))) toList();
  else if (st.tab === "compare") rerenderTab(); else renderTabs();
  if (!tabs.length) $("count").focus({ preventScroll: true });  // (its button is gone)
}

// Back to the list: the listing's history entry goes (a shared link to a listing stays on the site).
export function toList() {
  if (st.tab === "list") return;
  if (history.state?.tab) { history.back(); return; }  // popstate shows the list
  showTab("list");
  history.replaceState(null, "", hashFor());
}

export function showTab(next) {
  if (next === "compare" && tabs.length < 2) next = "list";
  if (isListing(next) && !tabs.includes(next)) next = "list";
  const was = st.tab;
  if (was === "list" && next !== "list") listY[st.mode] = $("results").scrollTop;
  // phones: a listing (or 比較) takes the whole sheet; the list gets its height back
  if (was === "list" && next !== "list" && !SPLIT.matches) { st.sheetBefore = $("panel").dataset.sheet; setSheet("full", false); }
  if (next === "list" && st.sheetBefore) { setSheet(st.sheetBefore, false); st.sheetBefore = null; }
  st.tab = next;
  $("panel").dataset.tab = isListing() ? "listing" : st.tab;
  $("results").hidden = st.tab !== "list";
  $("panel-body").hidden = st.tab === "list";
  renderTabs();
  markOpen();
  const body = $("panel-body");
  if (st.tab === "list") {
    body.dataset.key = "";
    body.innerHTML = "";
    if (was === "list") return;
    if (st.listStale) render();  // the map moved meanwhile
    restoreList();
    const from = st.openedFrom && $("list").querySelector(`[data-key="${CSS.escape(st.openedFrom)}"]`);
    (from || $("count")).focus({ preventScroll: true });
    return;
  }
  if (was === "list") body.scrollTop = 0;
  if (st.tab === "compare") {
    body.dataset.key = "compare";
    body.innerHTML = '<div class="skeleton"></div>';
    compareHtml(tabs.map(listing).filter(Boolean)).then((html) => { if (body.dataset.key === "compare") body.innerHTML = html; });
  } else if (body.dataset.key !== st.tab) {
    const key = st.tab;
    renderListing(body, key).then((r) => {
      if (r === undefined || body.dataset.key !== key) return;  // a later render draws it
      const ended = !r || listing(key)?.flags & st.db.flags.gone;  // ended: SUUMO's page is gone too
      if (!ended || favs.has(key)) body.insertAdjacentHTML("beforeend", `<div class="actions">
        <button class="btn fav-btn" data-fav="${esc(key)}" aria-pressed="${favs.has(key)}">${icon("heart")}<span>${esc(t(favs.has(key) ? "saved" : "save"))}</span></button>
        ${ended ? "" : `<a class="btn primary" href="${esc(safeUrl(r.url))}" target="_blank" rel="noopener">${esc(t("openSuumo"))}${icon("external")}</a>`}</div>`);  // (ended: only unsaved)
      body.querySelector("h2")?.focus({ preventScroll: true });
    });
  }
}

// the panel's tabs
Object.assign(ACTIONS, {
  retry: () => rerenderTab(),  // a listing that couldn't be loaded: try again
  tabShow: (key) => openTab(key),
  tabClose: (key) => closeTab(key),
});

// The tab strip, shown once a listing is open: the list, each listing (×), 比較 with two or more, すべて閉じる.
// The cards are drawn marked (card()): marked again only when the tabs change, and the strip rewritten only when
// what it says changes (not at every pan).
let markedTabs = "", stripHtml = "";
export function renderTabs() {
  const strip = $("tabs"), open = tabs.join("\n");
  if (open !== markedTabs) {
    markedTabs = open;
    for (const a of document.querySelectorAll(".card > a[data-key]")) a.parentElement.classList.toggle("open", tabs.includes(a.dataset.key));
  }
  strip.hidden = !tabs.length;
  if (strip.hidden) return;
  const button = (id, label, x) => `<span class="tab${st.tab === id ? " on" : ""}" role="presentation">
    <button role="tab" aria-selected="${st.tab === id}" aria-controls="${id === "list" ? "results" : "panel-body"}" data-tab-show="${esc(id)}">${
    id === "list" ? icon("back") : ""}${esc(label)}</button>${
    x ? `<button class="tab-x" data-tab-close="${esc(id)}" aria-label="${esc(t("closeTab", label))}">${icon("x")}</button>` : ""}</span>`;
  const html = button("list", `${t(st.mode === "favorites" ? "favorites" : st.view ? "listTab" : "listAll")} ${num(st.hits.length)}`) +
    tabs.map((key) => {
      const it = listing(key);
      return button(key, it ? `${money(it.price)} ${value(it.layout) || typeName(it.type)}` : typeName(key.split(":")[0]) || key, true);
    }).join("") + (tabs.length > 1 ? button("compare", t("compareTab")) : "") +
    `<button class="tab-all" data-tab-close="all">${esc(t("closeAll"))}</button>`;
  if (html === stripHtml && strip.firstChild) return;
  strip.innerHTML = stripHtml = html;
  strip.querySelector(".on")?.scrollIntoView({ block: "nearest", inline: "nearest" });
}

const SHEETS = ["peek", "half", "full"];
let dragEnd = 0;
export function setSheet(state, save = true) {
  $("panel").dataset.sheet = state;
  sheetSemantics();
  if (save) { settings.sheet = state; saveSettings(); }
  if (map) setTimeout(onMapMove, 300);  // what's in view: what the sheet leaves visible (once it's there)
}
export function wireSheet() {
  const panel = $("panel"), head = panel.querySelector(".panel-head");
  // a tap on the handle: one step up, and from full back to half (not all the way down)
  $("sheet-handle").addEventListener("click", () => setSheet({ peek: "half", half: "full", full: "half" }[panel.dataset.sheet] || "half"));
  head.addEventListener("click", (e) => { if (Date.now() - dragEnd < 300) { e.stopPropagation(); e.preventDefault(); } }, true);
  // dragged by its head: it snaps to the nearest state, or a flick goes one state on in its direction
  head.addEventListener("pointerdown", (e) => {
    if (SPLIT.matches || e.button) return;
    const y0 = e.clientY, from = panel.getBoundingClientRect().top - panel.offsetTop;  // (offsetTop: a full sheet's top)
    let moved = false, last = [e.timeStamp, y0], speed = 0;  // px/ms, down positive (the last move's)
    const move = (ev) => {
      if (!moved && Math.abs(ev.clientY - y0) < 8) return;
      if (!moved) { moved = true; head.setPointerCapture(e.pointerId); panel.classList.add("dragging"); }
      if (ev.timeStamp > last[0]) speed = (ev.clientY - last[1]) / (ev.timeStamp - last[0]);
      last = [ev.timeStamp, ev.clientY];
      panel.style.transform = `translateY(${Math.max(0, from + ev.clientY - y0)}px)`;
    };
    const end = (ev) => {
      head.removeEventListener("pointermove", move);
      head.removeEventListener("pointerup", end);
      head.removeEventListener("pointercancel", end);
      if (!moved) return;
      const at = panel.getBoundingClientRect().top, was = panel.dataset.sheet;
      panel.style.transform = "";
      const tops = SHEETS.map((s) => { panel.dataset.sheet = s; return panel.getBoundingClientRect().top; });
      panel.classList.remove("dragging");
      dragEnd = Date.now();
      const nearest = tops.reduce((b, top, i) => (Math.abs(top - at) < Math.abs(tops[b] - at) ? i : b), 0);
      let to = nearest;  // SHEETS runs from the lowest (peek) to the highest (full)
      if (ev.type === "pointerup" && Math.abs(speed) > 0.5 && ev.timeStamp - last[0] < 100) {
        const i = SHEETS.indexOf(was), step = speed > 0 ? -1 : 1;  // down: towards peek
        to = Math.max(0, Math.min(SHEETS.length - 1, (speed > 0 ? Math.min(i, nearest) : Math.max(i, nearest)) + (nearest === i ? step : 0)));
      }
      setSheet(SHEETS[to]);
    };
    head.addEventListener("pointermove", move);
    head.addEventListener("pointerup", end);
    head.addEventListener("pointercancel", end);
  });
  panel.addEventListener("focusin", (e) => { if (panel.dataset.sheet === "peek" && !head.contains(e.target)) setSheet("half"); });
  SPLIT.addEventListener("change", () => {  // one layout to the other: the sheet back as it was, the map resized
    st.sheetBefore = null;
    setSheet(SPLIT.matches || st.tab === "list" ? settings.sheet || "half" : "full", false);
    map?.attributionControl.setPosition(SPLIT.matches ? "bottomright" : "topleft");
    if (document.activeElement?.closest("[inert]")) $("count").focus();
  });
  setSheet(settings.sheet || "half", false);
}

const PANEL = 440, PANEL_MIN = 340, MAP_MIN = 360;  // px
export const panelDefault = () => Math.max(PANEL, Math.round(innerWidth * 0.3));  // (1920 wide: 576, one more card column)
export function setPanel(px, save = false) {
  const layout = document.querySelector(".layout"), rail = railOn() && st.mode === "search" ? 280 : 0;
  const max = Math.max(PANEL_MIN, layout.clientWidth - rail - 8 - MAP_MIN), w = Math.round(Math.min(max, Math.max(PANEL_MIN, px)));
  layout.style.setProperty("--panel", `${w}px`);
  for (const [k, v] of Object.entries({ min: PANEL_MIN, max, now: w })) $("split").setAttribute(`aria-value${k}`, v);
  if (save) { settings.panel = w; saveSettings(); }
}
export function wireSplit() {
  const split = $("split");
  split.addEventListener("pointerdown", (e) => {
    const x0 = e.clientX, w0 = $("panel").offsetWidth;
    let frame = 0;
    const move = (ev) => { cancelAnimationFrame(frame); frame = requestAnimationFrame(() => setPanel(w0 + x0 - ev.clientX)); };
    split.setPointerCapture(e.pointerId);
    document.body.classList.add("resizing");
    split.addEventListener("pointermove", move);
    split.addEventListener("lostpointercapture", () => {
      split.removeEventListener("pointermove", move);
      document.body.classList.remove("resizing");
      setPanel($("panel").offsetWidth, true);
      if (map) onMapMove();  // the list follows once
    }, { once: true });
  });
  split.addEventListener("keydown", (e) => {
    const w = $("panel").offsetWidth, step = e.shiftKey ? 120 : 40;  // (the list is on the right: ← widens it)
    const to = { ArrowLeft: w + step, ArrowRight: w - step, Home: PANEL_MIN, End: Infinity, Enter: PANEL }[e.key];
    if (to != null) { e.preventDefault(); setPanel(to, true); }
  });
  split.addEventListener("dblclick", () => { delete settings.panel; saveSettings(); setPanel(panelDefault()); });  // (the default again)
  addEventListener("resize", () => setPanel(settings.panel ?? panelDefault()));
  setPanel(settings.panel ?? panelDefault());
}
