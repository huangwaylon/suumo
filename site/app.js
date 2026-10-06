// The search page. Conditions live in the URL (shareable); results show as a list, and as a map on wide screens
// or on request; a listing opens over the page. The search rules are in filter.js, the strings in i18n.js.
(() => {
  "use strict";
  const $ = (id) => document.getElementById(id);
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => `&#${c.charCodeAt(0)};`);
  const icon = (name) => `<svg class="i"><use href="#i-${name}"/></svg>`;

  // ---------- settings: language and theme ----------

  const stored = (key, fallback) => { try { return JSON.parse(localStorage.getItem(key)) ?? fallback; } catch { return fallback; } };
  const settings = { lang: "ja", theme: "auto", ...stored("suumo.settings", {}) };
  if (!I18N[settings.lang]) settings.lang = "ja";
  let S = I18N[settings.lang] || I18N.ja;
  const lookup = (strings, key) => key.split(".").reduce((o, k) => o?.[k], strings);
  const t = (key, ...args) => { const v = lookup(S, key) ?? lookup(I18N.ja, key); return typeof v === "function" ? v(...args) : v; };
  // fixed SUUMO values (land rights, zoning, utilities...) in English: whole, else part by part ("商業、１種住居")
  const value = (v) => {
    if (v == null || !S.yearMonth) return v;  // Japanese: as listed
    v = v.replace(/[（(]納戸[)）]/g, S.values["（納戸）"]);  // 6LDK+2S（納戸）
    const month = /^(\d{4})年(\d{1,2})月(上旬|中旬|下旬)?(予定)?$/.exec(v);
    if (month) return S.yearMonth(...month.slice(1));
    return S.values[v] ?? v.split(/([、／/])/).map((p) => S.values[p.trim()] ?? (p === "、" || p === "／" ? ", " : p)).join("");
  };
  const place = (name) => S.places?.[name] ?? name;
  const stationName = (s) => (S.lang === "en" && db?.stationNames.get(s)?.[1]) || s;  // English when known
  const feature = (f) => S.features?.[f] ?? f.normalize("NFKC");
  const DARK = matchMedia("(prefers-color-scheme: dark)");

  function applyTheme() {
    const dark = settings.theme === "dark" || (settings.theme === "auto" && DARK.matches);
    document.documentElement.dataset.theme = dark ? "dark" : "light";
    document.querySelector('meta[name="theme-color"]').content = getComputedStyle(document.documentElement).getPropertyValue("--surface");
    localStorage.setItem("suumo.settings", JSON.stringify(settings));
  }

  function applyLanguage() {
    S = I18N[settings.lang];
    document.documentElement.lang = S.lang;
    document.title = t("title");
    for (const el of document.querySelectorAll("[data-t]")) el.textContent = t(el.dataset.t);
    for (const el of document.querySelectorAll("[data-t-label]")) el.setAttribute("aria-label", t(el.dataset.tLabel));
    for (const el of document.querySelectorAll("[data-t-placeholder]")) el.placeholder = t(el.dataset.tPlaceholder);
    $("sort").innerHTML = Object.entries(t("sorts")).map(([k, v]) => `<option value="${k}">${esc(v)}</option>`).join("");
    $("filters-body").innerHTML = "";  // rebuilt in the new language
    $("sort").value = q.sort;
    localStorage.setItem("suumo.settings", JSON.stringify(settings));
  }

  function renderSettings() {
    const group = (name, options) => `<div class="seg" role="group" aria-labelledby="set-${name}">${Object.entries(options).map(([k, label]) =>
      `<button class="seg-btn" data-setting="${name}" data-value="${k}" aria-pressed="${settings[name] === k}">${esc(label)}</button>`).join("")}</div>`;
    $("settings-body").innerHTML = `<section class="sec"><h3 id="set-lang">${esc(t("language"))}</h3>${group("lang", { ja: "日本語", en: "English" })}</section>
      <section class="sec"><h3 id="set-theme">${esc(t("theme"))}</h3>${group("theme", t("themes"))}</section>
      ${db?.index.updated ? `<p class="meta">${esc(t("updated", day(db.index.updated.slice(0, 10))))}</p>` : ""}`;
  }

  // ---------- choices ----------

  const PRICE_CHIPS = [3000, 4000, 5000, 6000, 8000, 10000, 15000].map((m) => m * 1e4);  // 上限: one tap
  const PRICES = [1000, 2000, 3000, 4000, 5000, 6000, 7000, 8000, 10000, 12000, 15000, 20000, 30000].map((m) => m * 1e4);
  const PLANS = [[3, "1K"], [5, "1LDK"], [7, "2DK"], [8, "2LDK"], [10, "3DK"], [11, "3LDK"], [14, "4LDK"]];
  const SIZES = [40, 50, 60, 70, 80, 100, 120];
  const LANDS = [50, 80, 100, 120, 150, 200];
  const AGES = [0, 5, 10, 20, 30];
  const WALKS = [5, 7, 10, 15, 20];
  const FLAGS = ["freehold", "noCondition", "newOnly", "dropsOnly", "post1981"];
  const QUICK = ["newOnly", "dropsOnly"];  // also one tap away above the results
  const PAGE = 30, TSUBO = 3.30578;
  const IMG = "https://img01.suumo.com/jj/resizeImage?src=";
  const TILES = "https://cyberjapandata.gsi.go.jp/xyz/pale/{z}/{x}/{y}.png";
  const LEAFLET = [
    ["css", "https://unpkg.com/leaflet@1.9.4/dist/leaflet.css", "sha384-sHL9NAb7lN7rfvG5lfHpm643Xkcjzp4jFvuavGOndn6pjVqS6ny56CAt3nsEVT4H"],
    ["css", "https://unpkg.com/leaflet.markercluster@1.5.3/dist/MarkerCluster.css", "sha384-pmjIAcz2bAn0xukfxADbZIb3t8oRT9Sv0rvO+BR5Csr6Dhqq+nZs59P0pPKQJkEV"],
    ["js", "https://unpkg.com/leaflet@1.9.4/dist/leaflet.js", "sha384-cxOPjt7s7Iz04uaHJceBmS+qpjv2JkIHNVcuOrM+YHwZOmJGBXI00mdUXEq65HTH"],
    ["js", "https://unpkg.com/leaflet.markercluster@1.5.3/dist/leaflet.markercluster.js", "sha384-eXVCORTRlv4FUUgS/xmOyr66XBVraen8ATNLMESp92FKXLAMiKkerixTiBvXriZr"],
  ];
  const COARSE = matchMedia("(pointer: coarse)");
  const WIDE = matchMedia("(min-width: 1280px)");  // filters, results and map side by side (style.css too)

  // ---------- state ----------

  let db = null;
  let q = Filter.emptyQuery();
  let mode = "search";          // search | favorites (the shared saved list)
  let hits = [], shown = 0;
  let showMap = false;
  let compare = false;          // favorites as a side-by-side table
  let daily = false;            // search: only new listings, grouped by the day they appeared
  let area = null;              // [south, west, north, east]: the map view, listing what's in the visible area
  let mapHits = [];             // what the map shows: the results (in the map area view, before the area cut)
  let openKey = null, pushed = false, lastFocus = null;
  let renderedFor = null;       // the URL (without the open listing) the results were drawn for
  let facets = null, facetsFor = null;
  let listStale = false;        // the phone filter sheet covers the list: it's redrawn when the sheet closes
  let areaNames = new Map();
  const stationHomes = new Map();  // station -> homes listing it
  let lastVisit = null;            // YYYYMMDDHH of the data seen on the previous visit (for "since your last visit")
  let dataHour = null;             // YYYYMMDDHH of the data shown now
  // Saved searches (this browser): [{c: conditions as URL parameters, seen: dataHour when last opened}]
  const searches = [].concat(stored("suumo.searches", [])).filter((s) => typeof s?.c === "string");
  const searchKey = (query) => paramsFor({ ...query, sort: "new", since: null }).toString();
  const saveSearches = () => localStorage.setItem("suumo.searches", JSON.stringify(searches));
  // The saved list is shared and kept on GitHub (saved.json, see suumo/saved.py): the heart opens a pre-filled
  // issue, a workflow applies it and rebuilds the site. Until then the request shows here as pending.
  const REPO = "https://github.com/huangwaylon/suumo";
  const PENDING_HOURS = 24;   // a request that never arrived (issue not sent) is forgotten after this
  let pending = stored("suumo.pending", {});  // key -> [save: true | false, time requested]
  const favs = new Set();      // the saved list as it shows here: the site's list with the pending requests applied

  // ---------- formatting ----------

  const formats = {};
  const num = (n) => (formats[S.lang] ||= new Intl.NumberFormat(S.lang === "en" ? "en-US" : "ja-JP")).format(n);
  function money(yen, html = false) {
    if (yen == null) return t("undecided");
    if (S.lang === "en") return yen >= 1e9 ? `¥${+(yen / 1e9).toFixed(2)}B` : `¥${+(yen / 1e6).toFixed(1)}M`;
    const m = Math.round(yen / 1e4), oku = Math.floor(m / 1e4), rest = m % 1e4;
    const unit = (s) => (html ? `<small>${s}</small>` : s);
    if (oku) return `${oku}${unit("億")}${rest ? num(rest) + unit("万円") : unit("円")}`;
    return num(m) + unit("万円");
  }
  const price = (it, html) => money(it.price, html) +
    (it.priceHi && it.priceHi !== it.price ? `${S.lang === "en" ? "–" : "〜"}${money(it.priceHi, html)}` : "");
  const m2 = (v) => (v ? `${+v.toFixed(2)}㎡` : "");
  const tsubo = (v) => (v ? t("tsubo", (v / TSUBO).toFixed(1)) : "");
  const isCondo = (type) => type === "used_condo" || type === "new_condo";
  const day = (d) => { const s = String(d ?? "").replaceAll("-", ""); return s.length === 8 ? t("date", +s.slice(0, 4), +s.slice(4, 6), +s.slice(6)) : ""; };
  function age(it) {
    if (it.type === "new_condo" || it.type === "new_house") return t("newBuild");
    return it.age == null ? "" : it.age === 0 ? t("ageUnder1") : t("ageYears", it.age);
  }
  function sizes(it) {  // on cards: whole ㎡, land and building on one line
    const r = (v) => `${Math.round(v)}㎡`;
    if (isCondo(it.type)) return [it.size && m2(it.size)];
    return [[it.land && `${t("land")} ${r(it.land)}`, it.size && `${t("building")} ${r(it.size)}`].filter(Boolean).join(" · ")];
  }
  function station(it) {
    const closest = (chosen) => it.stations.reduce((best, [s, w]) =>
      (w != null && (!chosen || q.stations.includes(s)) && (!best || w < best[1]) ? [s, w] : best), null);
    const chosen = mode === "search" && q.stations.length;
    const best = closest(chosen);  // a chosen station when there are any: that's why it's listed
    if (best) return t("walk", stationName(best[0]), best[1]);
    const byBus = chosen && it.stations.find(([s]) => q.stations.includes(s));
    if (byBus) return t("bus", stationName(byBus[0]), byBus[2]);
    return it.stations.length ? t("bus", stationName(it.stations[0][0]), it.stations[0][2]) : "";
  }
  function image(it, w, h) {
    let p = it.image;
    if (!p) return "";
    if (p.startsWith("http")) return p.replace(/w=\d+&h=\d+/, `w=${w}&h=${h}`);
    if (!p.startsWith("gazo/")) {  // shortened by the builder: dir/sub/n/file
      const [a, b, c, file] = p.split("/");
      p = `gazo/bukken/${a}/${b}/img/${c}/${it.id}/${it.id}_${file}`;
    }
    return `${IMG}${encodeURIComponent(p)}&w=${w}&h=${h}`;
  }
  const typeName = (type) => t("types")[type];
  const safeUrl = (u) => (/^https:\/\//.test(u || "") ? u : "#");
  const areaName = (code) => place(areaNames.get(code) || code);
  const planLabel = (v) => PLANS.find(([p]) => p === v)?.[1] ?? "";

  // ---------- URL <-> state ----------

  const KEYS = { q: "text", t: "types", a: "areas", st: "stations", f: "features", w: "walk", pmin: "priceMin",
    pmax: "priceMax", plan: "plan", s: "sizeMin", lm: "landMin", g: "ageMax", quake: "post1981", fh: "freehold",
    nc: "noCondition", new: "newOnly", drop: "dropsOnly", since: "since", sort: "sort" };

  // URL parameters -> a query (unknown values dropped)
  function queryFrom(p) {
    const base = Filter.emptyQuery(), out = Filter.emptyQuery();
    for (const [k, f] of Object.entries(KEYS)) {
      if (!p.has(k)) continue;
      const v = p.get(k), d = base[f];
      if (Array.isArray(d)) out[f] = v ? v.split(",") : [];
      else if (typeof d === "boolean") out[f] = v === "1";
      else if (f === "since") out[f] = /^\d{10}$/.test(v) ? +v : null;
      else if (d === null) out[f] = CHOICES[f].includes(+v) ? +v : null;
      else out[f] = v;
    }
    if (!Object.hasOwn(t("sorts"), out.sort)) out.sort = "new";
    out.types = out.types.filter((type) => db.index.types.includes(type));
    out.areas = out.areas.filter((a) => areaNames.has(a));
    return out;
  }
  // a query -> its URL parameters (the conditions only)
  function paramsFor(query) {
    const p = new URLSearchParams(), base = Filter.emptyQuery();
    for (const [k, f] of Object.entries(KEYS)) {
      const v = query[f];
      if (JSON.stringify(v) === JSON.stringify(base[f])) continue;
      p.set(k, Array.isArray(v) ? v.join(",") : typeof v === "boolean" ? "1" : v);
    }
    return p;
  }

  function readHash() {
    const p = new URLSearchParams(location.hash.slice(1));
    q = queryFrom(p);
    mode = p.get("fav") === "1" ? "favorites" : "search";
    daily = mode === "search" && p.get("daily") === "1";
    const b = (p.get("b") || "").split(",").map(Number);
    area = p.get("view") === "area" && b.length === 4 && b.every(Number.isFinite) ? b : null;
    showMap = p.get("view") === "map";
    if (area) showMap = false;
    compare = mode !== "search" && p.get("cmp") === "1";
    return p.get("id");
  }

  function hashFor(extra = {}) {
    const p = paramsFor(q);
    if (mode === "favorites") p.set("fav", "1");
    if (showMap) p.set("view", "map");
    if (area) { p.set("view", "area"); p.set("b", area.map((v) => v.toFixed(4)).join(",")); }
    if (compare && mode !== "search") p.set("cmp", "1");
    if (daily && mode === "search") p.set("daily", "1");
    for (const [k, v] of Object.entries(extra)) p.set(k, v);
    return "#" + p.toString();
  }

  // ---------- results ----------

  function compute() {
    if (mode === "search" && daily) return Filter.search(db, { ...q, sort: "new" }).filter((it) => it.newDate);
    if (mode === "search") return Filter.search(db, q);
    return Filter.search(db, { ...Filter.emptyQuery(), sort: q.sort }, [...favs].map((k) => db.byKey.get(k)).filter(Boolean));
  }

  function update(top = true) {
    history.replaceState(null, "", hashFor());
    if (mode === "search") localStorage.setItem("suumo.last", location.hash);  // reopened later: the same search
    render();
    if (top && !WIDE.matches) window.scrollTo({ top: 0 });
    else if (top) document.querySelector(".results").scrollIntoView({ block: "start" });
  }

  function render() {
    if (mode === "search") compare = false;  // compare is for the saved list only
    renderedFor = hashFor();
    if (document.body.classList.contains("show-filters") && !WIDE.matches) {
      listStale = true;  // only the sheet is visible: its counts are enough until it closes
      renderFilters();
      return;
    }
    listStale = false;
    hits = mapHits = compute();
    if (area) hits = inArea(mapHits);
    document.body.classList.toggle("areamode", !!area);
    document.body.classList.toggle("show-map", showMap && !WIDE.matches);
    $("q").value = q.text;
    $("sort").value = q.sort;
    $("count").textContent = t(mode === "favorites" ? "favBanner" : area ? "areaCount" : daily ? "dailyCount" : "count", num(hits.length));
    $("tabs").hidden = mode !== "search" || !!area;
    for (const b of $("tabs").children) b.setAttribute("aria-selected", (b.dataset.tab === "daily") === daily);
    $("sort").hidden = hits.length < 2 || compare || daily;  // by day: newest first
    $("view").hidden = (!hits.length && !showMap) || compare || !!area;
    const active = activeConditions(), set = mode === "search" ? active.filter(([, , s]) => s !== "text").length : 0;
    $("filters-n").hidden = !set;  // the text has its own box
    $("filters-n").textContent = set;
    $("favs-n").hidden = !favs.size;
    $("favs-n").textContent = favs.size;
    $("favs").setAttribute("aria-pressed", mode === "favorites");
    document.body.classList.toggle("listmode", mode !== "search");
    const offers = mode === "search" ? suggestions() : [];
    renderQuick();
    renderSearches();
    $("chips").innerHTML = mode !== "search" ? "" : offers.map(([f, v, label]) =>
      `<button class="chip suggest" data-suggest="${f}" data-value="${esc(v)}">${esc(label)}</button>`).join("") + active.map(([label, , s], i) => s === "quick" ? "" :
      `<span class="chip on"><button class="chip-label" data-edit="${s}">${esc(label)}</button><button class="chip-x" data-remove="${i}" aria-label="${esc(t("remove", label))}">${icon("x")}</button></span>`).join("") +
      (active.length > 1 && hits.length ? `<button class="chip ghost" data-clear="1">${esc(t("clearAll"))}</button>` : "");
    renderBanner();
    $("view").innerHTML = showMap ? `${icon("list")}<span>${t("list")}</span>` : `${icon("map")}<span>${t("map")}</span>`;
    $("list").innerHTML = hits.length ? "" : emptyState(active);
    shown = 0;
    if (compare && hits.length) renderCompare(); else more();
    if (mapVisible()) drawMap();
    if (filtersVisible()) renderFilters();
  }

  const scrollFor = {};
  // Views (list/map, favorites, by day, the map area) are history entries (back returns), each keeping its place.
  function switchView(name, change) {
    if (history.state?.view === name) { history.back(); return; }  // undoing the last switch: back to where it was
    scrollFor[renderedFor] = window.scrollY;
    change();
    history.pushState({ view: name }, "", hashFor());
    render();
    restoreScroll();
  }
  function restoreScroll() {
    const y = scrollFor[renderedFor] || 0;
    while (document.documentElement.scrollHeight < y + innerHeight && shown < hits.length) more();
    window.scrollTo({ top: y });
  }

  function more() {
    if (shown >= hits.length || compare) return;
    let html = "";
    for (let i = shown; i < Math.min(shown + PAGE, hits.length); i++) {
      const d = hits[i].newDate;
      if (daily && (i === 0 || hits[i - 1].newDate !== d)) {  // a heading for each day
        let n = 0;
        for (let j = i; j < hits.length && hits[j].newDate === d; j++) n++;
        html += `<h3 class="day">${esc(weekday(d))}<span>${esc(t("count", num(n)))}</span></h3>`;
      }
      html += card(hits[i]);
    }
    $("list").insertAdjacentHTML("beforeend", html);
    shown += PAGE;
  }
  // 2026100619 (JST hour) -> "3時間前" within a day, else "10/6"
  const atHour = (h) => Date.UTC(Math.floor(h / 1e6), Math.floor(h / 1e4) % 100 - 1, Math.floor(h / 100) % 100, h % 100 - 9);
  function ago(h) {
    if (!h) return "";
    const hours = Math.round((Date.now() - atHour(h)) / 3600e3);
    return hours < 24 ? t("hoursAgo", Math.max(hours, 0)) : `${Math.floor(h / 1e4) % 100}/${Math.floor(h / 100) % 100}`;
  }

  // 20261006 -> "10月6日（火）" / "Tue, Oct 6"
  function weekday(d) {
    const date = new Date(Math.floor(d / 1e4), Math.floor(d / 100) % 100 - 1, d % 100);
    return date.toLocaleDateString(S.lang === "en" ? "en-US" : "ja-JP", { month: "short", day: "numeric", weekday: "short" });
  }

  function tags(it) {
    const F = db.flags, out = [];
    if (it.flags & F.gone) out.push(`<span class="tag drop">${t("tag.gone")}</span>`);
    if (it.flags & F.new) out.push(`<span class="tag new">${t(it.flags & F.relisted ? "tag.relisted" : "tag.new")} ${esc(ago(it.newAt))}</span>`);
    if (it.flags & F.dropped) {
      const pct = it.prevPrice && it.price ? Math.round((1 - it.price / it.prevPrice) * 100) : 0;
      out.push(`<span class="tag drop">${t("tag.dropped")}${pct ? ` −${pct}%` : ""}</span>`);
    }
    if (it.flags & F.leasehold) out.push(`<span class="tag warn">${t("tag.leasehold")}</span>`);
    if (it.flags & F.conditional) out.push(`<span class="tag warn">${t("tag.conditional")}</span>`);
    if (it.others) out.push(`<span class="tag">${t("others", it.others)}</span>`);
    return out.join("");
  }

  function card(it) {
    const spec = [value(it.layout), ...sizes(it), age(it)].filter(Boolean).map((s) => `<span>${esc(s)}</span>`).join("");
    return `<article class="card"><a href="${esc(hashFor({ id: it.key }))}" data-key="${esc(it.key)}">
      <div class="ph">${it.image ? `<img src="${esc(image(it, 360, 270))}" alt="" loading="lazy" decoding="async" onerror="this.remove()">` : ""}</div>
      <div class="body">
        <div class="price num">${price(it, true)}</div>
        <div class="spec">${spec}</div>
        <div class="meta wrap">${esc(station(it))}</div>
        <div class="meta">${esc([S.lang === "en" && it.town ? `${areaName(it.area)} · ${it.town.slice(it.areaName.length)}` : it.town, it.name].filter(Boolean).join(" · "))}</div>
        <div class="tags">${tags(it)}</div>
      </div></a>
      <button class="fav" data-fav="${esc(it.key)}" aria-pressed="${favs.has(it.key)}" aria-label="${esc(t("favFor", money(it.price), it.layout, it.town || it.areaName))}">${icon("heart")}</button></article>`;
  }

  function emptyState(active) {
    if (mode !== "search" || (daily && !active.length)) {
      const [title, hint] = t(mode !== "search" ? "favEmpty" : "dailyEmpty");
      return `<div class="empty"><h3>${esc(title)}</h3><p>${esc(hint)}</p></div>`;
    }
    const loosen = active.map(([label, remove], i) => {
      const n = Filter.count(db, remove(structuredClone(q)));
      return n ? `<button class="chip" data-remove="${i}">${esc(t("remove", label))}<span class="n">${t("count", num(n))}</span></button>` : "";
    }).join("");
    return `<div class="empty"><h3>${esc(t("emptyTitle"))}</h3><p>${esc(t("emptyHint"))}</p>
      <div class="chips">${loosen}</div>${active.length ? `<button class="btn primary" data-clear="1">${esc(t("clearAll"))}</button>` : ""}</div>`;
  }

  function renderBanner() {
    const b = $("banner");
    b.hidden = mode === "search";
    const cmp = hits.length > 1 ? `<button class="btn" data-compare="1" aria-pressed="${compare}">${esc(t(compare ? "showCards" : "compare"))}</button>` : "";
    if (mode === "favorites") {
      b.innerHTML = cmp;
      b.hidden = !cmp;
    }
  }

  function applySuggestion(field, v) {  // the typed name becomes a condition
    q[field] = [...q[field], v];
    q.text = "";
    mode = "search";
    update();
  }

  // The saved searches, each with what's new or cheaper since it was last opened; the star saves the current one.
  function renderSearches() {
    const box = $("searches"), key = searchKey(q), here = mode === "search" && !area;
    const label = (query) => activeConditions(query).filter(([, , s]) => s !== "quick").map(([l]) => l).slice(0, 3).join("・");
    box.hidden = !here || !searches.length;
    if (!box.hidden) {
      box.innerHTML = `<span class="searches-title">${esc(t("savedSearches"))}</span>` + searches.map((s, i) => {
        const query = queryFrom(new URLSearchParams(s.c));
        const n = s.seen ? Filter.count(db, { ...query, since: s.seen }) : 0;
        return `<button class="chip" data-saved-search="${i}" aria-pressed="${s.c === key}">${esc(label(query) || t("allListings"))}${
          n ? `<span class="n fresh">${esc(t("freshN", num(n)))}</span>` : ""}</button>`;
      }).join("");
    }
    const keep = $("keep"), saved = searches.some((s) => s.c === key);
    keep.hidden = !here || !activeConditions().some(([, , s]) => s !== "quick" && s !== "text");
    keep.setAttribute("aria-pressed", saved);
    keep.setAttribute("aria-label", t(saved ? "unkeepSearch" : "keepSearch"));
  }

  // 新着 / 値下げ / since the last visit, one tap each, with what they'd show under the current conditions.
  function renderQuick() {
    const box = $("quick");
    box.hidden = mode !== "search" || daily;
    if (box.hidden) return;
    const chip = (field, label, value) => {
      const on = q[field] === value, n = on ? hits.length : Filter.count(db, { ...q, [field]: value });
      return n || on ? `<button class="chip" data-quick="${field}" aria-pressed="${on}">${esc(label)}<span class="n">${num(n)}</span></button>` : "";
    };
    box.innerHTML = chip("newOnly", t("quickNew"), true) + chip("dropsOnly", t("quickDrop"), true) +
      (lastVisit ? chip("since", t("sinceVisit"), lastVisit) : "");
  }

  // Typed a station or area name in the search box: offer it as a condition (walk limits, exact area).
  // The typed text as conditions to offer, best first: [[field, value, label], ...]. A ward or city named the same
  // as a station comes first unless 駅 was typed (世田谷 -> 世田谷区); a station may be typed in part (ふたこ).
  function suggestions() {
    const station = /(駅|station|sta\.?)$/i.test(q.text), typed = q.text.replace(/\s*(駅|station|sta\.?)$/i, "");
    if (typed.length < 2) return [];
    const key = Filter.normalize(typed).replace(/ /g, "");
    const plain = (n) => Filter.normalize(n).replace(/ /g, "");
    const area = db.index.areas.find(([code, name]) => !q.areas.includes(code) && [name, name.replace(/[区市町村]$/, ""),
      I18N.en.places[name]].some((n) => n && plain(n) === key));
    let best = null;  // the exact station, else the one with most homes among those starting with the text
    for (const s of db.index.stations) {
      if (q.stations.includes(s)) continue;
      const m = Filter.stationMatch(db, s, typed);
      if (m === 0) { best = s; break; }
      if (m === 1 && (!best || (stationHomes.get(s) || 0) > (stationHomes.get(best) || 0))) best = s;
    }
    const out = [];
    if (area) out.push(["areas", area[0], t("useArea", place(area[1]))]);
    if (best) out[station ? "unshift" : "push"](["stations", best, t("useStation", stationName(best))]);
    return out;
  }

  // The conditions in force, as [label, remove(query), filter section] for the chips and the empty state.
  function activeConditions(of = q) {
    // remove(query) takes the condition out of the given query (and returns it)
    const out = [], drop = (f, v) => (qq) => { qq[f] = qq[f].filter((x) => x !== v); return qq; };
    const reset = (...fs) => (qq) => { const e = Filter.emptyQuery(); for (const f of fs) qq[f] = e[f]; return qq; };
    if (of.text) out.push([t("chipText", of.text), reset("text"), "text"]);
    for (const type of of.types) out.push([typeName(type), drop("types", type), "types"]);
    for (const a of of.areas) out.push([areaName(a), drop("areas", a), "areas"]);
    if (of.priceMin != null || of.priceMax != null) {
      out.push([t("chipPrice", of.priceMin != null ? money(of.priceMin) : "", of.priceMax != null ? money(of.priceMax) : ""),
        reset("priceMin", "priceMax"), "price"]);
    }
    if (of.plan != null) out.push([t("chipPlan", planLabel(of.plan)), reset("plan"), "plan"]);
    if (of.sizeMin != null) out.push([t("sizeMin", of.sizeMin), reset("sizeMin"), "size"]);
    if (of.landMin != null) out.push([t("chipLand", of.landMin), reset("landMin"), "size"]);
    if (of.ageMax != null) out.push([t("ages")[of.ageMax] ?? t("ageYears", of.ageMax), reset("ageMax"), "age"]);
    for (const s of of.stations) out.push([t("station", stationName(s)), drop("stations", s), "stations"]);
    if (of.walk != null) out.push([t("walkWithin", of.walk), reset("walk"), "stations"]);
    for (const f of FLAGS) {
      if (of[f]) out.push([t("flags")[f], reset(f), f === "post1981" ? "age" : QUICK.includes(f) ? "quick" : "extra"]);
    }
    if (of.since) out.push([t("sinceVisit"), reset("since"), "quick"]);
    for (const f of of.features) out.push([feature(f), drop("features", f), "extra"]);
    return out;
  }

  // ---------- filters ----------

  const filtersVisible = () => WIDE.matches || document.body.classList.contains("show-filters");
  const CHOICES = { priceMin: PRICES, priceMax: PRICES, plan: PLANS.map(([v]) => v), sizeMin: SIZES, landMin: LANDS,
    ageMax: AGES, walk: WALKS };

  function chipBtn(attrs, label, n, on) {
    return `<button class="chip" ${attrs} aria-pressed="${on}"${!n && !on ? " disabled" : ""}>${esc(label)}${n != null ? `<span class="n">${num(n)}</span>` : ""}</button>`;
  }
  const many = (field, v, label, n) => chipBtn(`data-many="${field}" data-value="${esc(v)}"`, label, n, q[field].includes(v));
  const one = (field, v, label, n) => chipBtn(`data-one="${field}" data-value="${v}"`, label, n, q[field] === v);
  const flag = (field, n) => chipBtn(`data-flag="${field}"`, t("flags")[field], n, q[field]);
  function select(field, label, values, fmt) {
    return `<label class="field"><span>${esc(label)}</span><select class="select" data-num="${field}"><option value="">${esc(t("any"))}</option>${values.map((v) =>
      `<option value="${v}"${q[field] === v ? " selected" : ""}>${esc(fmt(v))}${esc(t("paren", num(facets[field][v] || 0)))}</option>`).join("")}</select></label>`;
  }

  function renderFilters() {
    const key = JSON.stringify({ ...q, sort: "" });  // the sort doesn't change any count
    if (key !== facetsFor) { facets = Filter.facets(db, q, CHOICES); facetsFor = key; }
    const body = $("filters-body");
    if (!body.firstChild) {  // sections are built once; their contents are redrawn (the station search keeps focus)
      body.innerHTML = ["text", "types", "areas", "price", "plan", "size", "age", "stations", "extra"].map((s) =>
        `<section class="sec" data-sec="${s}"></section>`).join("");
      body.querySelector('[data-sec="stations"]').innerHTML = `<h3>${esc(t("sec.stations"))}</h3>
        <p class="hint" id="walk-from"></p><div class="opts" id="walk-list"></div>
        <input class="text-input" id="station-q" type="search" placeholder="${esc(t("stationSearch"))}" aria-label="${esc(t("stationSearch"))}" autocomplete="off">
        <div class="opts" id="station-list"></div>`;
    }
    const narrows = (n) => n < facets.total;  // a choice every result has changes nothing: not shown
    const sec = (s, html) => { body.querySelector(`[data-sec="${s}"]`).innerHTML = html; };
    const h3 = (k, hint) => `<h3>${esc(t(`sec.${k}`))}${hint ? `<span class="hint">${esc(hint)}</span>` : ""}</h3>`;
    const isLand = q.types.length && q.types.every((type) => type === "land");
    sec("text", q.text ? `<div class="opts"><button class="chip" aria-pressed="true" data-cleartext="1">${esc(t("textFilter", q.text))}${icon("x")}</button></div>` : "");
    sec("types", `${h3("types")}<div class="opts">${Object.keys(t("types")).filter((type) => db.index.types.includes(type)).map((type) => many("types", type, typeName(type), facets.types[type] || 0)).join("")}</div>`);
    sec("areas", `${h3("areas")}${areaGroups()}`);
    sec("price", `${h3("price")}${select("priceMin", t("min"), PRICES, (v) => t("priceMin", money(v)))}<p id="price-max">${esc(t("max"))}</p><div class="opts">${
      PRICE_CHIPS.map((v) => one("priceMax", v, t("priceMax", money(v)), facets.priceMax[v] || 0)).join("")}</div>`);
    sec("plan", isLand ? "" : `${h3("plan", t("sec.planHint"))}<div class="opts">${PLANS.map(([v, l]) =>
      one("plan", v, t("planFrom", l), facets.plan[v] || 0)).join("")}</div>`);
    sec("size", `${h3("size")}${isLand ? "" : select("sizeMin", t("floorArea"), SIZES, (v) => t("sizeMin", v))}${
      q.types.length && q.types.every(isCondo) ? "" : select("landMin", t("landArea"), LANDS, (v) => t("landMin", v, Math.round(v / TSUBO)))}`);
    sec("age", isLand ? "" : `${h3("age")}<div class="opts">${AGES.map((v) => one("ageMax", v, t("ages")[v], facets.ageMax[v] || 0)).join("")}${
      flag("post1981", facets.post1981)}</div>`);
    stationList();
    $("walk-from").textContent = t(q.stations.length ? "walkFromChosen" : "walkFromNearest");
    $("walk-list").innerHTML = WALKS.filter((w) => q.walk === w || narrows(facets.walk[w] || 0)).map((w) => one("walk", w, t("walkWithin", w), facets.walk[w] || 0)).join("");
    const tagNames = Object.keys(facets.features).sort((a, b) => facets.features[b] - facets.features[a]);
    const shownTags = [...q.features, ...tagNames.filter((f) => !q.features.includes(f) && narrows(facets.features[f])).slice(0, 30)];
    const tagChip = (f) => many("features", f, feature(f), facets.features[f] || 0);
    const flagsShown = FLAGS.filter((f) => f !== "post1981" && (q[f] || (facets[f] && narrows(facets[f]))));
    sec("extra", !flagsShown.length && !shownTags.length ? "" : `${h3("extra")}<div class="opts">${flagsShown.map((f) =>
      flag(f, facets[f])).join("")}</div><div class="opts" style="margin-top:10px">${shownTags.slice(0, 12).map(tagChip).join("")}</div>${
      shownTags.length > 12 ? `<details class="group"><summary>${esc(t("moreTags"))}</summary><div class="opts">${shownTags.slice(12).map(tagChip).join("")}</div></details>` : ""}`);
    $("apply").textContent = t("show", num(facets.total));
    $("clear").disabled = !activeConditions().length;
  }

  function stationList() {
    const typed = $("station-q").value.trim(), counts = facets.stations, rank = new Map();
    if (typed) for (const s of db.index.stations) rank.set(s, Filter.stationMatch(db, s, typed));
    const found = typed ? db.index.stations.filter((s) => rank.get(s) < 3) : Object.keys(counts);
    const names = found.filter((s) => !q.stations.includes(s) && counts[s])  // stations with no homes left aren't offered
      .sort((a, b) => (rank.get(a) ?? 0) - (rank.get(b) ?? 0) || (counts[b] || 0) - (counts[a] || 0)).slice(0, 24);
    $("station-list").innerHTML = [...q.stations, ...names].map((s) => many("stations", s, t("station", stationName(s)), counts[s] || 0)).join("")
      || `<span class="meta">${esc(t(found.length ? "noStationHomes" : "noStation"))}</span>`;
  }

  function areaGroups() {
    const prefs = new Set(db.index.areas.map((a) => a[2])), kinds = t("kinds");
    const groups = new Map();
    for (const [code, name, pref] of [...db.index.areas].sort((a, b) => a[0].localeCompare(b[0]))) {
      const g = `${prefs.size > 1 ? place(db.index.prefs[pref]) + " " : ""}${kinds[code[2]] || kinds[3]}`;
      if (!groups.has(g)) groups.set(g, []);
      groups.get(g).push([code, many("areas", code, place(name), facets.areas[code] || 0)]);
    }
    return [...groups].map(([g, chips], i) => {
      const open = i === 0 || chips.some(([code]) => q.areas.includes(code));
      return `<details class="group"${open ? " open" : ""}><summary>${esc(g)}</summary><div class="opts">${chips.map(([, c]) => c).join("")}</div></details>`;
    }).join("");
  }

  function onFilterClick(e) {
    const b = e.target.closest("button[data-many], button[data-one], button[data-flag], button[data-cleartext]");
    if (!b) return;
    mode = "search";
    const v = b.dataset.value;
    if (b.dataset.cleartext) q.text = "";
    else if (b.dataset.many) {
      const f = b.dataset.many;
      q[f] = q[f].includes(v) ? q[f].filter((x) => x !== v) : [...q[f], v];
    } else if (b.dataset.one) {
      const f = b.dataset.one;
      q[f] = q[f] === +v ? null : +v;
      if (f === "ageMax") q.post1981 = false;
    } else {
      q[b.dataset.flag] = !q[b.dataset.flag];
    }
    const focus = [...b.attributes].filter((a) => a.name.startsWith("data-")).map((a) => `[${a.name}="${CSS.escape(a.value)}"]`).join("");
    update(false);
    $("filters-body").querySelector(`button${focus}`)?.focus();
  }

  // ---------- map ----------

  let leaflet = null, map = null, cluster = null, fittedFor = null;
  const mapVisible = () => WIDE.matches || showMap || !!area;
  const inArea = (list) => list.filter((it) => it.lat != null && it.lat >= area[0] && it.lng >= area[1] && it.lat <= area[2] && it.lng <= area[3]);
  const boundsOf = (m) => { const b = m.getBounds(); return [b.getSouth(), b.getWest(), b.getNorth(), b.getEast()]; };

  // The map area view follows the map: after a pan or zoom, the list is what's visible (the pins stay as they are).
  let areaTimer = null;
  function onMapMove() {
    if (!area) return;
    clearTimeout(areaTimer);
    areaTimer = setTimeout(() => {
      area = boundsOf(map);
      history.replaceState(history.state, "", hashFor());
      renderedFor = hashFor();
      fittedFor = renderedFor;
      hits = inArea(mapHits);
      $("count").textContent = t("areaCount", num(hits.length));
      $("list").innerHTML = hits.length ? "" : `<div class="empty"><h3>${esc(t("areaEmpty"))}</h3></div>`;
      shown = 0;
      more();
    }, 250);
  }

  function loadLeaflet() {
    leaflet ||= Promise.all(LEAFLET.map(([kind, url, integrity]) => new Promise((ok, fail) => {
      const el = document.createElement(kind === "css" ? "link" : "script");
      Object.assign(el, kind === "css" ? { rel: "stylesheet", href: url } : { src: url, async: false });
      Object.assign(el, { integrity, crossOrigin: "", onload: ok, onerror: fail });
      document.head.append(el);
    })));
    return leaflet;
  }
  // Leaflet loaded (true) or not available (false; retried next time).
  const mapReady = () => loadLeaflet().then(() => true, () => { leaflet = null; return false; });

  const pins = new Map();  // one icon per count, reused across redraws
  function pin(n, cluster = false) {  // a cluster (filled) zooms in; a town (outlined) opens its list
    const key = `${n}${cluster ? "c" : ""}`;
    if (!pins.has(key)) {
      const label = num(n);
      pins.set(key, L.divIcon({ className: "", html: `<div class="pin${cluster ? " cluster" : ""}">${label}</div>`,
        iconSize: [Math.max(30, 16 + label.length * 8), 26] }));
    }
    return pins.get(key);
  }

  async function drawMap() {
    if (!(await mapReady()) || !mapVisible()) return;
    if (!map) {
      const motion = !matchMedia("(prefers-reduced-motion: reduce)").matches;
      map = L.map("map", { zoomControl: false, zoomAnimation: motion, fadeAnimation: motion, markerZoomAnimation: motion });
      map.on("popupopen", (e) => e.popup.getElement()?.querySelector(".popup-list a")?.focus({ preventScroll: true }));
      map.on("popupopen popupclose", (e) => $("map").classList.toggle("popup-open", e.type === "popupopen"));
      map.on("moveend", onMapMove);
      // the map area view: list what's in sight
      const go = document.createElement("button");
      go.className = "btn primary area-btn";
      go.addEventListener("click", () => switchView("area", () => { area = area ? null : boundsOf(map); showMap = false; }));
      L.DomEvent.disableClickPropagation(go);
      $("map").append(go);
      if (!COARSE.matches) L.control.zoom({ position: "topright" }).addTo(map);
      L.tileLayer(TILES, { maxZoom: 18, attribution: '<a href="https://maps.gsi.go.jp/development/ichiran.html" target="_blank">地理院タイル</a>' }).addTo(map);
      cluster = L.markerClusterGroup({ maxClusterRadius: 48, showCoverageOnHover: false, chunkedLoading: true,
        iconCreateFunction: (c) => pin(c.getAllChildMarkers().reduce((n, m) => n + m.options.count, 0), true) });
      map.addLayer(cluster);
    }
    map.invalidateSize();
    const towns = new Map();
    let unplaced = 0;
    $("map").querySelector(".area-btn").textContent = t(area ? "areaClose" : "areaOpen");
    for (const it of mapHits) {
      if (it.lat == null) { unplaced++; continue; }
      if (!towns.has(it.town)) towns.set(it.town, [it.lat, it.lng, []]);
      towns.get(it.town)[2].push(it);
    }
    cluster.clearLayers();
    cluster.addLayers([...towns].map(([town, [lat, lng, list]]) =>
      L.marker([lat, lng], { icon: pin(list.length), count: list.length, title: `${town} ${t("count", num(list.length))}` }).bindPopup(() => popup(town, list), { autoPanPadding: [56, 56] })));
    let note = $("map").querySelector(".map-note");
    if (!note) { note = document.createElement("div"); note.className = "map-note"; $("map").append(note); }
    note.textContent = area ? t("areaNote") : hits.length ? t("mapCount", num(hits.length), unplaced && num(unplaced)) : mode === "search" ? t("emptyTitle") : t("favEmpty")[0];
    if (area && fittedFor === null) {  // opened from a link: show that area
      fittedFor = renderedFor;
      map.fitBounds([[area[0], area[1]], [area[2], area[3]]]);
    } else if (area) {
      fittedFor = renderedFor;     // the map area view keeps the user's view
    } else if (fittedFor !== renderedFor) {  // follow the results when the conditions change
      fittedFor = renderedFor;
      const points = towns.size ? [...towns.values()] : db.index.towns.filter((tw) => tw[1] != null).map((tw) => tw.slice(1));
      if (points.length) map.fitBounds(coreBounds(points), { padding: [30, 30], maxZoom: 15 });
    }
  }

  // Where most results are: the 10th-90th percentile box (outskirts and islands don't shrink the view).
  function coreBounds(points) {
    const pick = (i) => points.map((p) => p[i]).sort((a, b) => a - b);
    const lat = pick(0), lng = pick(1), at = (a, f) => a[Math.min(a.length - 1, Math.floor(a.length * f))];
    return [[at(lat, 0.1), at(lng, 0.1)], [at(lat, 0.9), at(lng, 0.9)]];
  }

  function popup(town, list) {
    return `<strong>${esc(town)}</strong> ${t("count", num(list.length))}<ul class="popup-list">${list.slice(0, 40).map((it) =>
      `<li><a href="${esc(hashFor({ id: it.key }))}" data-key="${esc(it.key)}">${it.image ? `<img src="${esc(image(it, 120, 90))}" alt="" loading="lazy">` : "<span></span>"}
        <span><b class="num">${money(it.price)}</b><br>${esc([value(it.layout) || typeName(it.type), age(it)].filter(Boolean).join(" · "))}<br>${esc(station(it))}</span></a></li>`).join("")}</ul>`;
  }

  // ---------- listing ----------

  function openDetail(key) {
    if (openKey === key && !$("detail").hidden) return;
    lastFocus = document.activeElement;
    history.pushState(null, "", hashFor({ id: key }));
    pushed = true;
    showDetail(key);
  }

  // A listing's full record (data/l/<type>/<id>.json); rejects with the HTTP status (404: ended) or a network error.
  const records = new Map();
  function record(key) {
    if (!records.has(key)) {
      records.set(key, fetch(`data/l/${key.replace(":", "/")}.json`)
        .then((res) => (res.ok ? res.json() : Promise.reject(new Error(res.status))))
        .catch((e) => { records.delete(key); throw e; }));
    }
    return records.get(key);
  }

  // ---------- compare ----------

  async function renderCompare() {
    const list = hits.slice(0, 12), forKey = renderedFor;
    $("list").innerHTML = '<div class="skeleton"></div>';
    const recs = await Promise.all(list.map((it) => record(it.key).catch(() => null)));
    if (renderedFor !== forKey) return;
    const cols = list.map((it, i) => [it, recs[i] || {}]);
    const yen = (v) => (v ? t("yen", num(v)) : "");
    const ROWS = [
      ["", ([it]) => `<a href="${esc(hashFor({ id: it.key }))}" data-key="${esc(it.key)}"><div class="ph">${it.image ? `<img src="${esc(image(it, 240, 180))}" alt="" loading="lazy">` : ""}</div></a>`],
      ["name", ([it]) => `<a class="cmp-name" href="${esc(hashFor({ id: it.key }))}" data-key="${esc(it.key)}">${esc(it.name || typeName(it.type))}</a>`],
      ["price", ([it]) => `<b class="num">${price(it, true)}</b>`],
      ["layout", ([it]) => esc(value(it.layout) || typeName(it.type))],
      [cols.every(([it]) => isCondo(it.type)) ? "floor" : cols.some(([it]) => isCondo(it.type)) ? "floorBoth" : "building",
        ([it]) => esc(it.size ? m2(it.size) : "")], ["land", ([it]) => esc(it.land ? m2(it.land) : "")],
      ["age", ([it]) => esc(age(it))], ["station", ([it]) => esc(station(it))],
      ["monthly", ([, r]) => esc(r.mgmt_fee || r.repair_fee ? t("perMonth", yen((r.mgmt_fee || 0) + (r.repair_fee || 0))) : "")],
      ["floors", ([, r]) => esc([r.floor != null && t("floorN", r.floor), r.floors_above && t("storeys", r.floors_above)].filter(Boolean).join(" / "))],
      ["units", ([, r]) => esc(r.units ? t("unitsN", r.units) : "")], ["rights", ([, r]) => esc(value(r.land_rights) || "")],
      ["place", ([it]) => esc(it.town || areaName(it.area))],
    ];
    $("list").innerHTML = `<div class="compare"><table><tbody>${ROWS.filter(([, f]) => cols.some((c) => f(c))).map(([k, f]) =>
      `<tr><th scope="row">${k ? esc(t(`d.${k}`)) : ""}</th>${cols.map((c) => `<td>${f(c)}</td>`).join("")}</tr>`).join("")}</tbody></table></div>`;
  }

  let detailMap = null;
  async function showDetail(key) {
    openKey = key;
    const it = db.byKey.get(key);
    $("detail").hidden = false;
    document.body.style.overflow = "hidden";
    $("detail-back").focus();
    setFavButton();
    $("detail-link").removeAttribute("href");
    const body = $("detail-body");
    body.innerHTML = '<div class="d-head"><div class="skeleton"></div></div>';
    let r;
    try {
      r = await record(key);
    } catch (e) {  // 404: the listing ended; anything else: the network
      if (openKey === key) body.innerHTML = `<div class="empty"><h3>${esc(t(e.message === "404" ? "gone" : "offline"))}</h3></div>`;
      return;
    }
    if (openKey !== key) return;
    $("detail-link").href = safeUrl(r.url);
    $("detail-link").hidden = !!(it && it.flags & db.flags.gone);  // ended: SUUMO's page is gone too
    body.innerHTML = detailHtml(r, it);
    if (it?.lat != null) {
      if (!(await mapReady()) || openKey !== key) return;
      detailMap?.remove();
      detailMap = L.map("detail-map", { zoomControl: false, attributionControl: false, dragging: !L.Browser.mobile })
        .setView([it.lat, it.lng], 15);
      L.tileLayer(TILES).addTo(detailMap);
      L.circle([it.lat, it.lng], { radius: 180, color: getComputedStyle(document.body).getPropertyValue("--accent") }).addTo(detailMap);
    }
  }

  function detailHtml(r, it) {
    const type = r.type, L_ = (k) => t(`d.${k}`);
    const rows = (pairs) => pairs.filter(([, v]) => v != null && v !== "").map(([k, v]) => `<dt>${esc(L_(k))}</dt><dd>${v}</dd>`).join("");
    const sec = (k, pairs) => { const html = rows(pairs); return html ? `<section class="d-sec"><h2>${esc(L_(k))}</h2><dl>${html}</dl></section>` : ""; };
    const yen = (v) => (v ? esc(t("yen", num(v))) : null);
    const txt = (s) => (s == null || s === "" ? null : esc(value(s)));
    const second = (main, sub) => esc(main) + (sub ? `<small>${esc(sub)}</small>` : "");  // 坪 on a line of its own
    const unit = it?.unit ? second(t("perM2", it.unit), !isCondo(type) && t("perTsubo", Math.round(it.unit * TSUBO))) : "";
    const facts = [["layout", esc(value(r.layout) || "")], [isCondo(type) ? "floor" : "building", esc(m2(r.floor_m2 || r.building_m2))],
      [isCondo(type) ? "balcony" : "land", isCondo(type) ? esc(m2(r.balcony_m2)) : m2(r.land_m2) && second(m2(r.land_m2), tsubo(r.land_m2))],
      ["age", it ? esc(age(it)) : ""], ["station", it ? esc(station(it)) : ""], ["unit", unit]].filter(([, v]) => v);
    const changes = r.history?.length && r.history.map(([d, o, n]) => esc(`${day(d)} ${money(o)} → ${money(n)}`)).join("<br>");
    const parking = r.parking && [value(r.parking.status), r.parking.fee_min && t("perMonth", t("yen", num(r.parking.fee_min)))].filter(Boolean).join(" ");
    const road = r.road && ([value(r.road.dir), r.road.width_m && t("width", r.road.width_m)].filter(Boolean).join(" ") || r.road.text);
    const access = (r.stations || []).map((s) => esc([s.line, t("station", stationName(s.name)), s.bus ? [t("busMin", s.bus), s.walk != null && t("stopWalk", s.walk)].filter(Boolean).join(" ")
      : s.walk != null ? t("walkMin", s.walk) : ""].filter(Boolean).join(" "))).join("<br>");
    return `
      ${r.image ? `<img class="hero" src="${esc(image({ image: r.image }, 640, 480))}" alt="">` : ""}
      <div class="d-head">
        <h1 id="detail-title">${esc(r.name || [r.layout, typeName(type)].filter(Boolean).join(" "))}</h1>
        <div class="meta">${esc([r.address, r.name && typeName(type)].filter(Boolean).join(" · "))}</div>
        <div class="d-price num">${it ? price(it, true) : money(r.price, true)}${r.price_excludes_building ? `<small class="note">${esc(t("excludesBuilding"))}</small>` : ""}</div>
        <div class="tags">${it ? tags(it) : ""}</div>
      </div>
      <div class="facts">${facts.map(([k, v]) => `<div class="fact"><span>${esc(L_(k))}</span><b>${v}</b></div>`).join("")}</div>
      ${changes ? sec("history", [["price", changes]]) : ""}
      ${sec("access", [["nearest", access]])}
      ${sec("costs", [["mgmt", yen(r.mgmt_fee) && t("perMonth", yen(r.mgmt_fee)) + (r.mgmt_form ? esc(t("paren", value(r.mgmt_form))) : "")],
        ["repair", yen(r.repair_fee) && t("perMonth", yen(r.repair_fee))], ["repairOnce", yen(r.repair_fund_once)],
        ["otherFees", txt(r.other_fees)], ["parking", txt(parking)]])}
      ${sec("bldg", [["built", r.built && t("month", +r.built.slice(0, 4), +r.built.slice(5, 7)) + (r.built_planned ? t("planned") : "")],
        ["structure", txt(r.structure)], ["floors", txt([r.floor != null && t("floorN", r.floor), r.floors_above && t("storeys", r.floors_above)].filter(Boolean).join(" / "))],
        ["units", isCondo(type) && r.total_units ? t("unitsN", num(r.total_units)) : null], ["direction", txt(r.direction)],
        ["reform", txt(r.reform?.text || r.reform?.date)], ["builder", txt(r.builder)], ["handover", txt(r.handover)]])}
      ${sec("legal", [["rights", r.land_rights && esc(value(r.land_rights) + (r.land_rights_note ? t("paren", r.land_rights_note) : ""))],
        ["zoning", txt(r.zoning)], ["ratios", r.coverage_pct || r.far_pct ? esc(`${r.coverage_pct ?? "-"}% / ${r.far_pct ?? "-"}%`) : null],
        ["road", txt(road)], ["category", txt(r.land_category)], ["landStatus", txt(r.land_status)], ["condition", r.build_condition ? t("yes") : null],
        ["utilities", txt(r.utilities)], ["restrictions", txt(r.restrictions)]])}
      ${r.features?.length ? `<section class="d-sec"><h2>${esc(L_("features"))}</h2><div class="opts">${r.features.map((f) => `<span class="tag">${esc(feature(f))}</span>`).join("")}</div></section>` : ""}
      ${sec("listing", [["agent", txt(r.agent)], ["deal", txt(r.deal_type)], ["otherListings", (r.others || []).map((o) =>
        `<a href="${esc(safeUrl(o.url))}" target="_blank" rel="noopener">${esc(o.agent || "SUUMO")}</a>`).join("<br>") || null],
        ["seen", day(r.new_date || r.first_seen)]])}
      ${it?.lat != null ? `<section class="d-sec"><h2>${esc(L_("map"))}</h2><p class="meta">${esc(L_("mapNote"))}</p></section><div id="detail-map"></div>` : ""}`;
  }

  function hideDetail() {
    openKey = null;
    detailMap?.remove();
    detailMap = null;
    $("detail").hidden = true;
    document.body.style.overflow = "";
    lastFocus?.focus();
  }

  function closeDetail() {
    if (pushed) { pushed = false; history.back(); return; }  // popstate hides it
    history.replaceState(null, "", hashFor());              // opened from a link: stay on the site
    hideDetail();
  }

  // ---------- favorites ----------

  // favs from the site's saved list and this browser's pending requests (dropped once the list shows them).
  function refreshFavs() {
    const now = Date.now();
    favs.clear();
    for (const it of db.byKey.values()) if (it.flags & db.flags.saved) favs.add(it.key);
    for (const [key, [save, at]] of Object.entries(pending)) {
      if (favs.has(key) === save || now - at > PENDING_HOURS * 3600e3) delete pending[key];
      else if (save) favs.add(key); else favs.delete(key);
    }
    localStorage.setItem("suumo.pending", JSON.stringify(pending));
    $("favs-n").hidden = !favs.size;
    $("favs-n").textContent = favs.size;
  }

  function toggleFav(key) {
    const save = !favs.has(key);
    const title = `${save ? "save" : "unsave"} ${key}`;  // read by suumo/saved.py
    window.open(`${REPO}/issues/new?title=${encodeURIComponent(title)}&body=${encodeURIComponent(t("issueBody", location.origin + location.pathname + hashFor({ id: key })))}`,
      "_blank", "noopener");
    pending[key] = [save, Date.now()];
    refreshFavs();
    document.querySelectorAll(`.fav[data-fav="${CSS.escape(key)}"]`).forEach((b) => b.setAttribute("aria-pressed", favs.has(key)));
    if (openKey === key) setFavButton();
    toast(t(save ? "favAdded" : "favRemoved"));
    if (mode === "favorites") render();
  }

  function setFavButton() {
    $("detail-fav").setAttribute("aria-pressed", favs.has(openKey));
    $("detail-fav").querySelector("span").textContent = t(favs.has(openKey) ? "saved" : "save");
  }

  let toastTimer = null;
  function toast(text) {
    $("toast").textContent = text;
    $("toast").classList.add("on");
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => $("toast").classList.remove("on"), 2200);
  }

  // ---------- events ----------

  function clearAll() {
    q = { ...Filter.emptyQuery(), sort: q.sort };
    mode = "search";
    if ($("station-q")) $("station-q").value = "";
    update(false);
  }

  // On phones and tablets the filter panel is a modal sheet: dialog semantics, the page behind inert.
  function sheetSemantics() {
    const open = document.body.classList.contains("show-filters"), sheet = $("filters");
    if (WIDE.matches) { sheet.removeAttribute("role"); sheet.removeAttribute("aria-modal"); } else {
      sheet.setAttribute("role", "dialog");
      sheet.setAttribute("aria-modal", "true");
    }
    $("filters-open").setAttribute("aria-expanded", open);
    for (const el of [document.querySelector(".bar"), document.querySelector(".results")]) el.inert = open;
  }

  // section: scroll the filters to it (a chip's label edits its condition)
  function openFilters(open, section) {
    if (open && !WIDE.matches && !document.body.classList.contains("show-filters")) {
      history.pushState({ sheet: true }, "", location.href);  // browser back closes the sheet
    }
    if (!open && history.state?.sheet) { history.back(); return; }  // popstate closes it
    document.body.classList.toggle("show-filters", open && !WIDE.matches);
    sheetSemantics();
    if (open) {
      renderFilters();
      const sec = section && $("filters-body").querySelector(`[data-sec="${section}"]`);
      if (sec) sec.scrollIntoView({ block: "start" }); else $("filters-body").scrollTop = 0;
      (WIDE.matches ? (sec || $("filters-body")).querySelector("button:not(:disabled), input, select") : $("filters-close"))?.focus();
      return;
    }
    if (listStale) render();
    $("filters-open").focus();
  }

  function openSettings(open, popped) {
    if (open && $("settings").hidden) history.pushState({ settings: true }, "", location.href);  // back closes it
    if (!open && !popped && history.state?.settings) { history.back(); return; }  // popstate closes it
    $("settings").hidden = !open;
    if (open) { lastFocus = document.activeElement; renderSettings(); $("settings-close").focus(); } else lastFocus?.focus();
  }

  // Keep Tab inside an open dialog.
  function trapFocus(e, root) {
    const f = [...root.querySelectorAll("a[href], button, select, input")].filter((el) => !el.disabled && el.offsetParent);
    if (!f.length) return;
    const first = f[0], last = f[f.length - 1];
    if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
    else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
  }

  function wire() {
    let timer = null;
    $("q").addEventListener("input", (e) => {
      clearTimeout(timer);
      timer = setTimeout(() => { q.text = e.target.value.trim(); mode = "search"; update(); }, 200);
    });
    $("q").addEventListener("keydown", (e) => {  // Enter takes the suggestion (if any) and hides the phone keyboard
      if (e.key !== "Enter") return;
      clearTimeout(timer);
      q.text = e.target.value.trim();
      const [best] = suggestions();
      if (best) applySuggestion(best[0], best[1]); else update();
      if (COARSE.matches) e.target.blur(); else e.target.focus();
    });
    $("sort").addEventListener("change", (e) => { q.sort = e.target.value; update(); });
    $("filters-open").addEventListener("click", () => openFilters(true));
    $("filters-close").addEventListener("click", () => openFilters(false));
    $("apply").addEventListener("click", () => openFilters(false));
    $("clear").addEventListener("click", clearAll);
    $("filters-body").addEventListener("click", onFilterClick);
    $("filters-body").addEventListener("change", (e) => {
      const f = e.target.dataset.num;
      if (f) {
        q[f] = e.target.value === "" ? null : +e.target.value;
        update(false);
        $("filters-body").querySelector(`select[data-num="${f}"]`)?.focus();  // redrawn: keep the keyboard there
      }
    });
    $("filters-body").addEventListener("input", (e) => { if (e.target.id === "station-q") stationList(); });
    $("filters-body").addEventListener("keydown", (e) => {  // Enter in the station search takes the first match
      if (e.target.id === "station-q" && e.key === "Enter") $("station-list").querySelector('button[aria-pressed="false"]')?.click();
    });
    $("settings-open").addEventListener("click", () => openSettings(true));
    $("settings-close").addEventListener("click", () => openSettings(false));
    $("settings").addEventListener("click", (e) => {
      if (e.target === $("settings")) return openSettings(false);
      const b = e.target.closest("[data-setting]");
      if (!b) return;
      settings[b.dataset.setting] = b.dataset.value;
      if (b.dataset.setting === "theme") applyTheme();
      else { applyLanguage(); render(); if (openKey) showDetail(openKey); }
      renderSettings();
      $("settings-body").querySelector(`[data-setting="${b.dataset.setting}"][data-value="${b.dataset.value}"]`)?.focus();
    });
    DARK.addEventListener("change", () => { if (settings.theme === "auto") applyTheme(); });
    $("keep").addEventListener("click", () => {
      const key = searchKey(q), i = searches.findIndex((s) => s.c === key);
      if (i >= 0) searches.splice(i, 1); else searches.push({ c: key, seen: dataHour });
      saveSearches();
      toast(t(i >= 0 ? "searchRemoved" : "searchKept"));
      renderSearches();
    });
    $("favs").addEventListener("click", () => switchView("favorites", () => { mode = mode === "favorites" ? "search" : "favorites"; }));
    $("view").addEventListener("click", () => switchView("map", () => { showMap = !showMap; }));
    $("detail-back").addEventListener("click", closeDetail);
    $("detail").addEventListener("click", (e) => { if (e.target === $("detail")) closeDetail(); });
    document.addEventListener("click", (e) => {
      if (e.target === document.body && document.body.classList.contains("show-filters")) return openFilters(false);  // backdrop
      const fav = e.target.closest("[data-fav]");
      if (fav) { e.preventDefault(); toggleFav(fav.dataset.fav || openKey); return; }
      const link = e.target.closest("a[data-key]");
      if (link && !e.metaKey && !e.ctrlKey && !e.shiftKey) { e.preventDefault(); openDetail(link.dataset.key); return; }
      const b = e.target.closest("button");
      if (!b) return;
      if (b.dataset.remove != null) {
        activeConditions()[+b.dataset.remove][1](q);
        update();
        ($("chips").querySelector(".chip-x") || $("count")).focus();
      }
      else if (b.dataset.clear) clearAll();
      else if (b.dataset.edit) openFilters(true, b.dataset.edit);
      else if (b.dataset.suggest) { applySuggestion(b.dataset.suggest, b.dataset.value); $("q").focus(); }
      else if (b.dataset.savedSearch != null) {  // open a saved search: it's seen as of now
        const s = searches[+b.dataset.savedSearch];
        q = { ...queryFrom(new URLSearchParams(s.c)), sort: q.sort };
        s.seen = dataHour;
        saveSearches();
        daily = false;
        update();
      } else if (b.dataset.quick) {
        const f = b.dataset.quick;
        q[f] = q[f] ? Filter.emptyQuery()[f] : f === "since" ? lastVisit : true;
        update();
      } else if (b.dataset.tab && (b.dataset.tab === "daily") !== daily) switchView("daily", () => { daily = !daily; });
      else if (b.dataset.compare) switchView("compare", () => { compare = !compare; });
    });
    document.querySelectorAll(".skip").forEach((a) => a.addEventListener("click", (e) => {
      e.preventDefault();  // the hash holds the search
      const id = a.getAttribute("href").slice(1);
      if (id === "filters") openFilters(true); else $(id)?.focus();
    }));
    window.addEventListener("popstate", () => {
      if (document.body.classList.contains("show-filters")) {  // back from the sheet: keep its changes
        document.body.classList.remove("show-filters");
        sheetSemantics();
        history.replaceState(null, "", hashFor());
        if (listStale) render();
        $("filters-open").focus();
        return;
      }
      if (!$("settings").hidden) { openSettings(false, true); return; }
      const before = renderedFor;
      scrollFor[before] = window.scrollY;
      route();
      if (renderedFor !== before && $("detail").hidden) restoreScroll();
    });
    document.addEventListener("keydown", (e) => {
      const dialog = !$("detail").hidden ? $("detail") : !$("settings").hidden ? $("settings")
        : document.body.classList.contains("show-filters") ? $("filters") : null;
      if (e.key === "Tab" && dialog) trapFocus(e, dialog);
      if (e.key === "/" && !dialog && !/^(INPUT|SELECT|TEXTAREA)$/.test(document.activeElement.tagName)) { e.preventDefault(); $("q").focus(); }
      if (e.key !== "Escape") return;
      if (!$("settings").hidden) openSettings(false);
      else if (!$("detail").hidden) closeDetail();
      else if (document.body.classList.contains("show-filters")) openFilters(false);
    });
    WIDE.addEventListener("change", () => render());
    new IntersectionObserver((es) => { if (es[0].isIntersecting) more(); }, { rootMargin: "800px" }).observe($("sentinel"));
  }

  function route() {
    const key = readHash();
    if (hashFor() !== renderedFor) render();
    if (key) showDetail(key);
    else if (!$("detail").hidden) { pushed = false; hideDetail(); }
  }

  async function start() {
    applyTheme();
    applyLanguage();
    $("list").innerHTML = '<div class="skeleton"></div>'.repeat(6);
    try {
      const index = await (await fetch("data/index.json")).json();
      db = Filter.load(index);
      for (const it of db.items) {
        it.alias = I18N.en.places[it.areaName];  // "shibuya" finds 渋谷区
        for (const [s] of it.stations) stationHomes.set(s, (stationHomes.get(s) || 0) + 1);
      }
      refreshFavs();
      // a visit = a new data version: the previous one is what "since your last visit" compares with
      const visit = stored("suumo.visit", {}), now = +String(index.updated).replace(/\D/g, "").slice(0, 10) || null;
      if (now && visit.cur !== now) { visit.prev = visit.cur ?? null; visit.cur = now; localStorage.setItem("suumo.visit", JSON.stringify(visit)); }
      lastVisit = visit.prev ?? null;
      dataHour = visit.cur ?? null;
      areaNames = new Map(index.areas.map(([code, name]) => [code, name]));
    } catch {
      const [title, hint] = t("loadFailed");
      $("list").innerHTML = `<div class="empty"><h3>${esc(title)}</h3><p>${esc(hint)}</p></div>`;
      return;
    }
    const early = $("q").value.trim();  // typed while the listings were loading
    wire();
    if (!location.hash && localStorage.getItem("suumo.last")) history.replaceState(null, "", localStorage.getItem("suumo.last"));
    route();
    if (mode === "search") localStorage.setItem("suumo.last", location.hash);
    if (early && !q.text) { q.text = early; update(); }
    (window.requestIdleCallback || setTimeout)(() => mapReady());  // so the first map opens without waiting
  }

  start();
})();
