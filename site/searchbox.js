// The search box: a combobox of suggestions (Filter.suggest) and of the conditions read from the text
// (Filter.parse); nothing is searched until one is chosen (Enter, a tap, leaving the box).

import { $, ACTIONS, COARSE, esc, icon } from "./util.js";
import { areaName, feature, num, stationName, t } from "./format.js";
import { st } from "./state.js";
import { activeConditions, render, toSearch, update } from "./results.js";

export const NL = { features: I18N.en.features, places: I18N.en.places, year: new Date().getFullYear() };  // (filter.js caches by it)
const KIND_ICON = { area: "map", station: "train", town: "pin", name: "building", word: "sliders" };
let options = [], activeOption = -1, sgTimer = null;
export let altSwaps = [];     // [{field, value, label, alt}]: readings of the last search that can be switched (目黒区 ⇄ 目黒駅)
export let ignoredNote = [];  // phrases the last search understood but couldn't use (築30年以上)
const withoutLast = (s) => s.replace(/\S+\s*$/, "").trim();
const joined = (...parts) => parts.filter(Boolean).join(" ");
export const altLabel = (a) => (a.field === "stations" ? t("altStation", stationName(a.value)) : t("altArea", areaName(a.value), st.areaNames.get(a.value) || ""));
function suggestLabel(c) {
  if (c.kind === "area") return areaName(c.value);
  if (c.kind === "station") return t("station", stationName(c.value));
  return c.kind === "word" ? feature(c.label) : c.label;
}

function applyQuery(next, read = null) {
  st.q = next;
  toSearch();
  altSwaps = read ? read.tokens.filter((tk) => tk.alt).map(({ field, value, label, alt }) => ({ field, value, label, alt })) : [];
  ignoredNote = read ? read.ignored : [];
  closeSuggest();
  $("q").value = st.q.text;
  update();
}
// The box's text: its conditions applied, the rest searched as text.
export function applyText(text) {
  const read = Filter.parse(text, st.db, NL);
  applyQuery(read.tokens.length ? Filter.applyParse(st.q, read) : { ...st.q, text: text.trim() }, read);
}
// A suggestion for the word being typed: the words before it stay; a town or building name is added as text.
function pick(c, text) {
  const base = { ...st.q, text: withoutLast(text) };
  if (c.kind === "area" || c.kind === "station") {
    const f = c.kind === "area" ? "areas" : "stations";
    return applyQuery({ ...base, [f]: [...new Set([...st.q[f], c.value])] });
  }
  if (c.kind === "word") {  // a condition word or a tag: as the parser reads it
    const read = Filter.parse(c.value, st.db, NL), next = Filter.applyParse(base, read);
    return applyQuery({ ...next, text: joined(base.text, read.rest) }, read);
  }
  applyQuery({ ...base, text: joined(base.text, c.value) });
}

function updateSuggest() {
  const text = $("q").value;
  if (!text.trim() || !st.db) { closeSuggest(); return; }
  const read = Filter.parse(text, st.db, NL), applied = read.tokens.length ? Filter.applyParse(st.q, read) : null;
  options = [];
  // with conditions already in force: added to them, or (a new search) in their place
  const kept = activeConditions().some(([, , sec]) => sec !== "quick");
  const only = applied && kept ? Filter.applyParse({ ...Filter.emptyQuery(), sort: st.q.sort }, read) : null;
  const go = (label, query, run) => options.push({ cls: "go", run,
    html: `${icon("search")}<span class="sg-label">${esc(label)}</span><span class="n">${esc(t("count", num(Filter.count(st.db, query))))}</span>` });
  if (applied) go(t(kept ? "applyAdd" : "applyParsed"), applied, () => applyText(text));
  if (only) go(t("applyOnly"), only, () => applyQuery(only, read));
  for (const c of Filter.suggest(st.db, text, NL)) {
    options.push({ run: () => pick(c, text), html: `${icon(KIND_ICON[c.kind] || "search")}<span class="sg-label">${esc(suggestLabel(c))}</span>${
      c.count != null ? `<span class="n">${num(c.count)}</span>` : ""}` });
  }
  options.push({ cls: "free", run: () => applyQuery({ ...st.q, text: text.trim() }), html: `${icon("search")}<span class="sg-label">${esc(t("freeword", text.trim()))}</span>` });
  activeOption = -1;
  // the conditions the text would add, as they'll show once applied (dashed: not yet)
  const now = new Set(activeConditions().map(([l]) => l));
  $("pending").innerHTML = (applied ? activeConditions(applied).filter(([l, , s]) => s !== "quick" && !now.has(l)).map(([l]) =>
    `<span class="chip pend">${esc(l)}</span>`).join("") : "") + (read.ignored.length ? `<span class="chip muted">${esc(t("ignored", read.ignored.join(t("sep"))))}</span>` : "");
  $("pending").hidden = !$("pending").innerHTML;
  $("suggest").innerHTML = options.map((o, i) => `<li role="option" id="sg-${i}" class="sg ${o.cls || ""}" aria-selected="false" data-sg="${i}">${o.html}</li>`).join("");
  openSuggest(true);
  $("suggest-live").textContent = t("suggestCount", options.length);
}
function openSuggest(open) {
  $("suggest-box").hidden = !open;
  $("q").setAttribute("aria-expanded", open);
  if (!open) $("q").removeAttribute("aria-activedescendant");
}
function closeSuggest() { clearTimeout(sgTimer); options = []; activeOption = -1; openSuggest(false); }
// the readings of the last search
Object.assign(ACTIONS, {
  alt(i) {  // 目黒区 ⇄ 目黒駅
    const a = altSwaps[+i];
    st.q[a.field] = st.q[a.field].filter((v) => v !== a.value);
    st.q[a.alt.field] = [...new Set([...st.q[a.alt.field], a.alt.value])];
    altSwaps[+i] = { ...a.alt, alt: { field: a.field, value: a.value, label: a.label } };
    update(false);
  },
  ignoredX() { ignoredNote = []; render(); },
});

function moveOption(d) {
  if (!options.length) return;
  activeOption = Math.max(-1, Math.min(options.length - 1, activeOption + d));
  $("suggest").querySelectorAll(".sg").forEach((el, i) => el.setAttribute("aria-selected", i === activeOption));
  const el = $(`sg-${activeOption}`);
  if (el) { $("q").setAttribute("aria-activedescendant", el.id); el.scrollIntoView({ block: "nearest" }); } else $("q").removeAttribute("aria-activedescendant");
}

export function wireSearch() {
  const box = $("q");
  box.addEventListener("input", () => { clearTimeout(sgTimer); sgTimer = setTimeout(updateSuggest, 200); });
  box.addEventListener("focus", () => { if (box.value.trim()) updateSuggest(); });
  box.addEventListener("keydown", (e) => {
    if (e.key === "ArrowDown" || e.key === "ArrowUp") {
      e.preventDefault();
      if ($("suggest-box").hidden) updateSuggest();
      moveOption(e.key === "ArrowDown" ? 1 : -1);
    } else if (e.key === "Enter") {
      e.preventDefault();
      clearTimeout(sgTimer);
      if (activeOption >= 0) options[activeOption].run(); else applyText(box.value);
      if (COARSE.matches) box.blur();
    } else if (e.key === "Escape" && !$("suggest-box").hidden) { e.stopPropagation(); closeSuggest(); }
    else if (e.key === "Tab") closeSuggest();
  });
  box.addEventListener("blur", () => setTimeout(() => {  // leaving the box applies what it says
    if (document.activeElement === box || !st.db) return;
    if (box.value.trim() !== st.q.text) applyText(box.value); else closeSuggest();
  }, 0));
  $("suggest-box").addEventListener("mousedown", (e) => e.preventDefault());  // the box keeps focus
  $("suggest-box").addEventListener("click", (e) => {
    const li = e.target.closest("[data-sg]");
    if (li) options[+li.dataset.sg]?.run();
    if (li && COARSE.matches) $("q").blur();  // (phones: the keyboard goes, the results show)
  });
}
