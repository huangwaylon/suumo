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
  const saveSettings = () => localStorage.setItem("suumo.settings", JSON.stringify(settings));
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
    showRail();  // the lines' halos follow the theme
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
    sheetSemantics();  // its labels depend on the rail's state
  }

  function renderSettings() {
    const group = (name, options, now = settings[name]) => `<div class="seg" role="group" aria-labelledby="set-${name}">${Object.entries(options).map(([k, label]) =>
      `<button class="seg-btn" data-setting="${name}" data-value="${k}" aria-pressed="${now === k}">${esc(label)}</button>`).join("")}</div>`;
    $("settings-body").innerHTML = `<section class="sec"><h3 id="set-lang">${esc(t("language"))}</h3>${group("lang", { ja: "日本語", en: "English" })}</section>
      <section class="sec"><h3 id="set-theme">${esc(t("theme"))}</h3>${group("theme", t("themes"))}</section>
      ${manifest?.fx ? `<section class="sec"><h3 id="set-usd">${esc(t("usdSetting"))}</h3>${group("usd", t("onOff"), showUsd() ? "on" : "off")}</section>` : ""}
      ${manifest?.updated ? `<p class="meta">${esc(t("updated", day(manifest.updated.slice(0, 10))))}</p>` : ""}`;
  }

  // ---------- choices ----------

  const PRICES = [1000, 2000, 3000, 4000, 5000, 6000, 7000, 8000, 10000, 12000, 15000, 20000, 30000].map((m) => m * 1e4);
  const PLANS = [[3, "1K"], [5, "1LDK"], [7, "2DK"], [8, "2LDK"], [10, "3DK"], [11, "3LDK"], [14, "4LDK"]];
  const SIZES = [40, 50, 60, 70, 80, 100, 120];
  const LANDS = [50, 80, 100, 120, 150, 200];
  const AGES = [0, 5, 10, 20, 30];
  const WALKS = [5, 7, 10, 15, 20];
  const FEES = [15000, 20000, 25000, 30000, 35000, 40000, 50000];  // condos' monthly fees, yen
  const FLAGS = ["freehold", "noCondition", "newOnly", "dropsOnly", "post1981", "south", "top", "upper", "nofee", "ready",
    "tower", "parking"];
  const PICKS = ["freehold", "south", "upper", "top", "nofee", "ready", "noCondition", "tower", "parking"];  // こだわり
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

  let db = null;                // the chosen prefectures' listings, searched as one
  let manifest = null;          // data/index.json: the prefectures there are, the saved list, when updated
  const loaded = new Map();     // prefecture slug -> its loaded index (Filter.load)
  let chosen = [];              // the prefectures searched (URL p=, default: the first, Tokyo)
  let allByKey = new Map();     // every loaded listing (favorites can be in prefectures not chosen)
  let q = Filter.emptyQuery();
  let mode = "search";          // search | favorites (the shared saved list)
  let hits = [], shown = 0;
  let mapHits = [];             // the results, on the map (hits: those in its view)
  let byDay = false;            // the list has a heading per day (what's new, newest first)
  let lastFocus = null;
  let renderedFor = null;       // the URL (without the open listing) the results were drawn for
  let facets = null, facetsFor = null;
  let listStale = false;        // the list is drawn when it's in sight again (the phone filter sheet, another tab)
  let sayTimer = null;          // the count is said aloud shortly after the conditions change
  let listSig = null;           // what the drawn cards show (render skips redrawing the same)
  let areaNames = new Map();
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
  // the price in dollars too, at the day's rate (manifest.fx, the ECB's); yen is the source of truth
  function dollars(yen) {
    const d = yen / manifest?.fx?.jpy_per_usd;
    return !(d > 0) ? "" : d >= 1e6 ? `$${+(d / 1e6).toFixed(2)}M` : `$${Math.round(d / 1e3)}K`;
  }
  const showUsd = () => (settings.usd ? settings.usd === "on" : S.lang === "en");  // default: in English only
  function usd(it) {
    if (!showUsd()) return "";
    const lo = dollars(it.price), hi = it.priceHi && it.priceHi !== it.price ? dollars(it.priceHi) : "";
    return lo && `<small class="usd" title="${esc(t("usdRate", manifest.fx.jpy_per_usd, day(manifest.fx.date)))}">${esc(t("usd", hi ? `${lo}–${hi}` : lo))}</small>`;
  }
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
    const [s, , bus, car] = (chosen && it.stations.find(([n]) => q.stations.includes(n))) || it.stations[0] || [];
    if (!s) return it.stop ? t("stopWalkTo", it.stop[0], it.stop[1]) : "";
    return car ? t("car", stationName(s), car) : t("bus", stationName(s), bus);
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
  // a plan rank (rooms × 3 + K 0 / DK 1 / LDK 2, see catalog.plan_rank) as written: 11 -> "3LDK"
  const planLabel = (v) => PLANS.find(([p]) => p === v)?.[1] ?? `${Math.floor(v / 3)}${["K", "DK", "LDK"][v % 3]}`;
  const ageLabel = (v) => t("ages")[v] ?? t("ageWithin", v);

  // ---------- URL <-> state ----------

  const KEYS = { q: "text", t: "types", a: "areas", st: "stations", f: "features", w: "walk", pmin: "priceMin",
    pmax: "priceMax", plan: "plan", s: "sizeMin", lm: "landMin", g: "ageMax", quake: "post1981", fh: "freehold",
    nc: "noCondition", new: "newOnly", drop: "dropsOnly", since: "since", sort: "sort", so: "south", top: "top",
    up: "upper", nf: "nofee", rd: "ready", tw: "tower", pk: "parking", fee: "feesMax" };

  // Thresholds can be any sensible number (a link, a typed search), not only the offered choices.
  const RANGE = { priceMin: [0, 1e11], priceMax: [0, 1e11], plan: [3, 30], sizeMin: [1, 2000], landMin: [1, 10000],
    ageMax: [0, 100], walk: [1, 120], feesMax: [0, 200000] };
  const within = (f, v) => v !== "" && Number.isFinite(+v) && RANGE[f] && +v >= RANGE[f][0] && +v <= RANGE[f][1];

  // URL parameters -> a query (unknown values dropped)
  function queryFrom(p) {
    const out = rawQuery(p);
    out.types = out.types.filter((type) => db.index.types.includes(type));
    out.areas = out.areas.filter((a) => areaNames.has(a));
    return out;
  }
  // ... of any prefecture (saved searches); an older link's conditions in today's terms (Filter.upgrade)
  function rawQuery(p) {
    let out = Filter.emptyQuery();
    const base = Filter.emptyQuery();
    for (const [k, f] of Object.entries(KEYS)) {
      if (!p.has(k)) continue;
      const v = p.get(k), d = base[f];
      if (Array.isArray(d)) out[f] = v ? v.split(",") : [];
      else if (typeof d === "boolean") out[f] = v === "1";
      else if (f === "since") out[f] = /^\d{10}$/.test(v) ? +v : null;
      else if (d === null) out[f] = within(f, v) ? +v : null;
      else out[f] = v;
    }
    if (!Object.hasOwn(t("sorts"), out.sort)) out.sort = "new";
    out = Filter.upgrade?.(out) ?? out;
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
    return p;
  }

  // The page's URL: the conditions; with tab, the open listing (or the favorites' 比較); with at, where the map is.
  function hashFor(extra = {}, { tab: withTab = true, at = true } = {}) {
    const p = paramsFor(q);
    if (chosen.join() !== manifest.prefs[0][0]) p.set("p", chosen.join(","));
    if (mode === "favorites") p.set("fav", "1");
    if (withTab && isListing()) p.set("id", tab);
    if (withTab && tab === "compare" && mode === "favorites") p.set("cmp", "1");  // (the open tabs: not in a link)
    if (at && mapAt && mode === "search") p.set("m", mapAt);
    for (const [k, v] of Object.entries(extra)) p.set(k, v);
    return "#" + p.toString();
  }
  const conditionsHash = () => hashFor({}, { tab: false, at: false });
  const saveLast = () => mode === "search" && localStorage.setItem("suumo.last", hashFor({}, { tab: false }));  // reopened later

  // ---------- results ----------

  function compute() {
    if (mode === "search") return Filter.search(db, q);
    const away = [...new Set([...favs].map((k) => manifest.saved[k]).filter((p) => p && !loaded.has(p)))];
    if (away.length) loadPrefs(away).then(() => { fitNext = "results"; render(); });  // saved listings in prefectures not loaded yet
    return Filter.search(db, { ...Filter.emptyQuery(), sort: q.sort }, [...favs].map((k) => allByKey.get(k)).filter(Boolean));
  }

  function update(top = true) {
    if ([...q.areas, ...q.stations].some((x) => !lastPlaces.includes(x))) fitNext = "results";  // a place named: show it
    // the phone sheet's entry stays one (closing it goes back to the page, keeping the changes); a view's or a
    // listing's doesn't (going back would undo the changes)
    history.replaceState(history.state?.sheet ? history.state : null, "", hashFor());
    saveLast();
    if (top) listY[mode] = 0;
    render();
    if (top) $("results").scrollTop = 0;
  }

  function render() {
    renderedFor = conditionsHash();
    lastPlaces = [...q.areas, ...q.stations];
    if (document.body.classList.contains("show-filters") && !WIDE.matches) {
      listStale = true;  // only the sheet is visible: its counts are enough until it closes
      renderFilters();
      return;
    }
    listStale = false;
    mapHits = compute();
    if ($("q").value.trim() !== q.text && document.activeElement !== $("q")) $("q").value = q.text;  // (not while typing)
    $("sort").value = q.sort;
    byDay = mode === "search" && q.sort === "new" && !!(q.newOnly || q.since);  // what's new: a heading per day
    const active = activeConditions(), set = mode === "search" ? active.filter(([, , s]) => s !== "text").length : 0;
    $("filters-n").hidden = !set;  // the text has its own box
    $("filters-n").textContent = set;
    $("favs").setAttribute("aria-pressed", mode === "favorites");
    document.body.classList.toggle("listmode", mode !== "search");
    setPanel(settings.panel ?? PANEL);  // (the rail comes and goes with the favorites)
    renderPrefBar();
    renderSearches();
    // a ward read from the search text that is also a station (or the other way round) can be switched
    const swap = (ref) => { const j = ref ? altSwaps.findIndex((a) => a.field === ref[0] && a.value === ref[1]) : -1; return j < 0 ? "" :
      `<button class="chip-alt" data-alt="${j}" aria-label="${esc(t("swapTo", altLabel(altSwaps[j].alt)))}">⇄ ${esc(altLabel(altSwaps[j].alt))}</button>`; };
    $("chips").innerHTML = mode !== "search" ? "" : active.map(([label, , s, ref], i) => s === "quick" ? "" :
      `<span class="chip on"><button class="chip-label" data-edit="${s}">${esc(label)}</button>${swap(ref)}<button class="chip-x" data-remove="${i}" aria-label="${esc(t("remove", label))}">${icon("x")}</button></span>`).join("") +
      (ignoredNote.length ? `<span class="chip muted">${esc(t("ignored", ignoredNote.join("・")))}<button class="chip-x" data-ignored-x="1" aria-label="${esc(t("close"))}">${icon("x")}</button></span>` : "") +
      (active.length > 1 && mapHits.length ? `<button class="chip ghost" data-clear="1">${esc(t("clearAll"))}</button>` : "");
    drawMap();
    listResults();
    if (filtersVisible()) renderFilters();
  }

  // The list: the results in the map's view (favorites: all of them), with the count and the quick chips.
  // moved: the map moved (a new place: the list starts from the top).
  function listResults(moved = false) {
    const here = mode === "search" && !!view;
    hits = here ? inArea(mapHits, view) : mapHits;
    $("count").textContent = mode === "favorites" ? t("favBanner", num(hits.length)) : t(here ? "inView" : "count", num(hits.length));
    // said aloud once the conditions changed (and the map followed them), not on every pan
    if (!moved && !sayTimer) sayTimer = setTimeout(() => { $("count-live").textContent = $("count").textContent; sayTimer = null; }, 700);
    $("fit").hidden = !here;
    $("fit").textContent = t("fitAll", num(mapHits.length));
    $("sort").hidden = hits.length < 2;
    $("compare").hidden = mode !== "favorites" || hits.length < 2;
    renderQuick();
    renderTabs();
    if (waiting) return;  // the skeleton stays until the map has its view
    if (tab !== "list") { listStale = true; return; }  // (drawn when it's shown)
    // the same cards as already drawn (a sheet closed, a view switched back): kept as they are, with their place
    const sig = [S.lang, mode, byDay, q.stations, showUsd(), [...favs], Object.keys(pending), tabs, hits.map((it) => it.key)].join("|");
    if (sig === listSig && hits.length) return;
    listSig = sig;
    const focused = $("list").contains(document.activeElement) && document.activeElement.closest("[data-key], [data-fav]");
    $("list").innerHTML = hits.length ? "" : mapHits.length ? `<div class="empty"><h3>${esc(t("viewEmpty"))}</h3>
      <button class="btn primary" data-fit="1">${esc(t("fitAllShow", num(mapHits.length)))}</button></div>` : emptyState(activeConditions());
    shown = 0;
    more();
    renderUnplaced();
    if (moved) $("results").scrollTop = 0;
    if (focused) {  // the keyboard stays on the same card, if it's still listed
      const key = focused.dataset.key || focused.dataset.fav;
      ($("list").querySelector(`[data-key="${CSS.escape(key)}"]`) || $("count")).focus({ preventScroll: true });
    }
  }

  // The results the map can't show (no coordinates): in every view, folded at the end of the list.
  function renderUnplaced() {
    const box = $("unplaced"), list = mode === "search" && view ? mapHits.filter((it) => it.lat == null) : [];
    box.hidden = !list.length;
    box.querySelector("summary").textContent = t("unplaced", num(list.length));
    box.querySelector(".grid").innerHTML = box.open ? list.map((it) => card(it)).join("") : "";
  }

  // The favorites are a view of their own: a history entry (back returns to the search, its place and its map).
  function toggleFavorites() {
    if (history.state?.view) { history.back(); return; }  // undoing the switch: back to where it was
    if (tab === "list") listY[mode] = $("results").scrollTop; else showTab("list");
    const before = mode;
    mode = mode === "favorites" ? "search" : "favorites";
    modeChanged(before);
    history.pushState({ view: 1 }, "", hashFor());
    render();
    restoreList();
  }
  // Entering the favorites fits the map to them; leaving puts the search's map back.
  let searchAt = null;
  function modeChanged(before) {
    if (before === mode) return;
    if (mode === "favorites") { searchAt = mapAt; fitNext = "results"; } else if (searchAt) fitNext = { m: searchAt };
  }
  function restoreList() {
    const y = listY[mode] || 0;
    while (shown < hits.length && $("list").offsetHeight < y + $("results").clientHeight) more();
    $("results").scrollTop = y;
  }

  function more() {
    if (shown >= hits.length) return;
    let html = "";
    for (let i = shown; i < Math.min(shown + PAGE, hits.length); i++) {
      const d = hits[i].newDate;
      if (byDay && (i === 0 || hits[i - 1].newDate !== d)) {  // a heading for each day (then the price cuts)
        let n = 0;
        for (let j = i; j < hits.length && hits[j].newDate === d; j++) n++;
        html += `<h3 class="day">${esc(d ? weekday(d) : t("tag.dropped"))}<span>${esc(t("count", num(n)))}</span></h3>`;
      }
      html += card(hits[i], i < 4);  // the first photos are in sight: not lazy
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
    if (it.flags & F.gone) out.push(`<span class="tag gone">${t("tag.gone")}</span>`);
    if (pending[it.key]) out.push(`<span class="tag warn">${t(pending[it.key][0] ? "tag.pending" : "tag.pendingRemove")}</span>`);
    if (it.flags & F.new) {  // under a day heading the date is said already: only the hours, today
      const when = byDay && it.newDate && !/[時h]/.test(ago(it.newAt)) ? "" : ago(it.newAt);
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

  // the walk time never breaks across lines ("徒歩5分", "5 min walk")
  const nowrapWalk = (s) => esc(s).replace(/(徒歩\d+分|停歩\d+分|\d+ min walk)/, '<span class="nw">$1</span>');
  const fees = (it) => (isCondo(it.type) && it.fees != null ? t("feesCard", it.fees) : "");

  function card(it, eager = false) {
    const spec = [value(it.layout), ...sizes(it), age(it)].filter(Boolean).map((s) => `<span>${esc(s)}</span>`).join("");
    return `<article class="card${tabs.includes(it.key) ? " open" : ""}"><a href="${esc(hashFor({ id: it.key }))}" data-key="${esc(it.key)}">
      <div class="ph">${it.image ? `<img src="${esc(image(it, 360, 270))}" alt=""${eager ? "" : ' loading="lazy"'} decoding="async" onerror="this.remove()">` : ""}</div>
      <div class="body">
        <div class="price num">${price(it, true)}${usd(it)}</div>
        <div class="spec">${spec}</div>
        <div class="meta wrap"><span class="kind">${esc(typeName(it.type))}</span> · ${nowrapWalk(station(it))}</div>
        <div class="meta">${esc([S.lang === "en" && it.town ? `${areaName(it.area)} · ${it.town.slice(it.areaName.length)}` : it.town, it.name].filter(Boolean).join(" · "))}</div>
        ${fees(it) ? `<div class="meta fees">${esc(fees(it))}</div>` : ""}
        <div class="tags">${tags(it)}</div>
      </div></a>
      <button class="fav" data-fav="${esc(it.key)}" aria-pressed="${favs.has(it.key)}" aria-label="${esc(t("favFor", money(it.price), it.layout, it.town || it.areaName))}">${icon("heart")}</button></article>`;
  }

  function emptyState(active) {
    if (mode !== "search" || (byDay && active.every(([, , s]) => s === "quick"))) {
      const [title, hint] = t(mode !== "search" ? "favEmpty" : "dailyEmpty");
      return `<div class="empty"><h3>${esc(title)}</h3><p>${esc(hint)}</p></div>`;
    }
    // what each condition's removal would show, in one pass: each removal as the fields it changes
    const patches = {};
    active.forEach(([, remove], i) => {
      const without = remove(structuredClone(q));
      patches[i] = Object.fromEntries(Object.keys(q).filter((k) => JSON.stringify(q[k]) !== JSON.stringify(without[k])).map((k) => [k, without[k]]));
    });
    const counts = Filter.counts(db, q, patches);
    const loosen = active.map(([label], i) => (counts[i]
      ? `<button class="chip" data-remove="${i}">${esc(t("remove", label))}<span class="n">${t("count", num(counts[i]))}</span></button>` : "")).join("");
    return `<div class="empty"><h3>${esc(t("emptyTitle"))}</h3><p>${esc(t("emptyHint"))}</p>
      <div class="chips">${loosen}</div>${active.length ? `<button class="btn primary" data-clear="1">${esc(t("clearAll"))}</button>` : ""}</div>`;
  }

  const freshCounts = new Map();
  // The saved searches, each with what's new or cheaper since it was last opened; the star saves the current one.
  function renderSearches() {
    const box = $("searches"), key = searchKey(q), here = mode === "search";
    const label = (query) => activeConditions(query).filter(([, , s]) => s !== "quick").map(([l]) => l).slice(0, 3).join("・");
    box.hidden = !here || !searches.length;
    if (!box.hidden) {
      box.setAttribute("aria-label", t("savedSearches"));
      box.innerHTML = searches.map((s, i) => {
        const query = queryFrom(new URLSearchParams(s.c));
        const k = `${chosen}|${s.seen}|${s.c}`;  // the counts change only with the data and when it was seen
        if (s.seen && !freshCounts.has(k)) freshCounts.set(k, Filter.count(db, { ...query, since: s.seen }));
        const n = s.seen ? freshCounts.get(k) : 0;
        const name = label(query) || t("allListings");
        return `<span class="chip saved${s.c === key ? " on" : ""}"><button class="chip-label" data-saved-search="${i}">${esc(name)}</button>${
          n ? `<span class="n fresh">${esc(t("freshN", num(n)))}</span>` : ""}<button class="chip-x" data-unsave-search="${i}" aria-label="${esc(t("unkeepSearch"))}: ${esc(name)}">${icon("x")}</button></span>`;
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
    box.hidden = mode !== "search";
    if (box.hidden) return;
    const offers = { newOnly: true, dropsOnly: true, ...(lastVisit ? { since: lastVisit } : {}) }, patches = {};
    for (const [f, v] of Object.entries(offers)) if (q[f] !== v) patches[f] = { [f]: v };
    const counts = Filter.counts(view ? { ...db, items: inView() } : db, q, patches);  // in view, in one pass
    const chip = (field, label, value) => {
      const on = q[field] === value, n = on ? hits.length : counts[field];
      return n || on ? `<button class="chip" data-quick="${field}" aria-pressed="${on}">${esc(label)}<span class="n">${num(n)}</span></button>` : "";
    };
    box.innerHTML = chip("newOnly", t("quickNew"), true) + chip("dropsOnly", t("quickDrop"), true) +
      (lastVisit ? chip("since", t("sinceVisit"), lastVisit) : "");
  }

  // ---------- the search box: a combobox of suggestions (Filter.suggest) and of the conditions read from the text
  // (Filter.parse); nothing is searched until one is chosen (Enter, a tap, leaving the box) ----------

  const NL = { features: I18N.en.features, places: I18N.en.places, year: new Date().getFullYear() };  // (filter.js caches by it)
  const KIND_ICON = { area: "map", station: "train", town: "pin", name: "building", word: "sliders" };
  let options = [], activeOption = -1, sgTimer = null;
  let altSwaps = [];     // [{field, value, label, alt}]: readings of the last search that can be switched (目黒区 ⇄ 目黒駅)
  let ignoredNote = [];  // phrases the last search understood but couldn't use (築30年以上)
  const withoutLast = (s) => s.replace(/\S+\s*$/, "").trim();
  const joined = (...parts) => parts.filter(Boolean).join(" ");
  const altLabel = (a) => (a.field === "stations" ? t("station", stationName(a.value)) : areaName(a.value));
  function suggestLabel(c) {
    if (c.kind === "area") return areaName(c.value);
    if (c.kind === "station") return t("station", stationName(c.value));
    return c.kind === "word" ? feature(c.label) : c.label;
  }

  function applyQuery(next, read = null) {
    q = next;
    mode = "search";
    altSwaps = read ? read.tokens.filter((tk) => tk.alt).map(({ field, value, label, alt }) => ({ field, value, label, alt })) : [];
    ignoredNote = read ? read.ignored : [];
    closeSuggest();
    $("q").value = q.text;
    update();
  }
  // The box's text: its conditions applied, the rest searched as text.
  function applyText(text) {
    const read = Filter.parse(text, db, NL);
    applyQuery(read.tokens.length ? Filter.applyParse(q, read) : { ...q, text: text.trim() }, read);
  }
  // A suggestion for the word being typed: the words before it stay; a town or building name is added as text.
  function pick(c, text) {
    const base = { ...q, text: withoutLast(text) };
    if (c.kind === "area" || c.kind === "station") {
      const f = c.kind === "area" ? "areas" : "stations";
      return applyQuery({ ...base, [f]: [...new Set([...q[f], c.value])] });
    }
    if (c.kind === "word") {  // a condition word or a tag: as the parser reads it
      const read = Filter.parse(c.value, db, NL), next = Filter.applyParse(base, read);
      return applyQuery({ ...next, text: joined(base.text, read.rest) }, read);
    }
    applyQuery({ ...base, text: joined(base.text, c.value) });
  }

  function updateSuggest() {
    const text = $("q").value;
    if (!text.trim() || !db) { closeSuggest(); return; }
    const read = Filter.parse(text, db, NL), applied = read.tokens.length ? Filter.applyParse(q, read) : null;
    options = [];
    if (applied) options.push({ cls: "go", run: () => applyText(text),
      html: `${icon("search")}<span class="sg-label">${esc(t("applyParsed"))}</span><span class="n">${esc(t("count", num(Filter.count(db, applied))))}</span>` });
    for (const c of Filter.suggest(db, text, NL)) {
      options.push({ run: () => pick(c, text), html: `${icon(KIND_ICON[c.kind] || "search")}<span class="sg-label">${esc(suggestLabel(c))}</span>${
        c.count != null ? `<span class="n">${num(c.count)}</span>` : ""}` });
    }
    options.push({ cls: "free", run: () => applyQuery({ ...q, text: text.trim() }), html: `${icon("search")}<span class="sg-label">${esc(t("freeword", text.trim()))}</span>` });
    activeOption = -1;
    // the conditions the text would add, as they'll show once applied (dashed: not yet)
    const now = new Set(activeConditions().map(([l]) => l));
    $("pending").innerHTML = (applied ? activeConditions(applied).filter(([l, , s]) => s !== "quick" && !now.has(l)).map(([l]) =>
      `<span class="chip pend">${esc(l)}</span>`).join("") : "") + (read.ignored.length ? `<span class="chip muted">${esc(t("ignored", read.ignored.join("・")))}</span>` : "");
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
  function moveOption(d) {
    if (!options.length) return;
    activeOption = Math.max(-1, Math.min(options.length - 1, activeOption + d));
    $("suggest").querySelectorAll(".sg").forEach((el, i) => el.setAttribute("aria-selected", i === activeOption));
    const el = $(`sg-${activeOption}`);
    if (el) { $("q").setAttribute("aria-activedescendant", el.id); el.scrollIntoView({ block: "nearest" }); } else $("q").removeAttribute("aria-activedescendant");
  }

  function wireSearch() {
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
      if (document.activeElement === box || !db) return;
      if (box.value.trim() !== q.text) applyText(box.value); else closeSuggest();
    }, 0));
    $("suggest-box").addEventListener("mousedown", (e) => e.preventDefault());  // the box keeps focus
    $("suggest-box").addEventListener("click", (e) => {
      const li = e.target.closest("[data-sg]");
      if (li) options[+li.dataset.sg]?.run();
    });
  }

  // The conditions in force, as [label, remove(query), filter section] for the chips and the empty state.
  function activeConditions(of = q) {
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
    if (of.since) out.push([t("sinceVisit"), reset("since"), "quick"]);
    for (const f of of.features) out.push([feature(f), drop("features", f), "extra"]);
    return out;
  }

  // ---------- filters ----------

  const railOn = () => WIDE.matches && !settings.railOff;  // desktop: the filter rail, unless hidden (remembered)
  const filtersVisible = () => railOn() || document.body.classList.contains("show-filters");
  const CHOICES = { priceMin: PRICES, priceMax: PRICES, plan: PLANS.map(([v]) => v), sizeMin: SIZES, landMin: LANDS,
    ageMax: AGES, walk: WALKS, feesMax: FEES };
  const openFolds = new Set();  // the collapsed sections (エリア, こだわり) the user opened

  function chipBtn(attrs, label, n, on) {
    return `<button class="chip" ${attrs} aria-pressed="${on}"${!n && !on ? " disabled" : ""}>${esc(label)}${n != null ? `<span class="n">${num(n)}</span>` : ""}</button>`;
  }
  const many = (field, v, label, n) => chipBtn(`data-many="${field}" data-value="${esc(v)}"`, label, n, q[field].includes(v));
  const one = (field, v, label, n) => chipBtn(`data-one="${field}" data-value="${v}"`, label, n, q[field] === v);
  const flag = (field, n) => chipBtn(`data-flag="${field}"`, t("flags")[field], n, q[field]);
  // A value set by a link or a typed search that isn't one of the choices: shown as an extra choice (no count).
  const withExtra = (field, values) => (q[field] == null || values.includes(q[field]) ? values : [...values, q[field]].sort((a, b) => a - b));
  const selectEl = (field, values, fmt, none, label) => `<select class="select" data-num="${field}" aria-label="${esc(label)}"><option value="">${esc(none)}</option>${
    withExtra(field, values).map((v) => `<option value="${v}"${q[field] === v ? " selected" : ""}>${esc(fmt(v))}${values.includes(v) ? esc(t("paren", num(facets[field][v] || 0))) : ""}</option>`).join("")}</select>`;
  const select = (field, label, values, fmt) => `<label class="field"><span>${esc(label)}</span>${selectEl(field, values, fmt, t("any"), label)}</label>`;
  // The share of the chosen prefectures' listings whose page was fetched (conditions from it only cover those).
  function detailShare() {
    let n = 0, sum = 0;
    for (const [slug, , c, share] of manifest.prefs) if (chosen.includes(slug) && typeof share === "number") { n += c; sum += share * c; }
    return n ? sum / n : null;
  }
  // A flag of the index: an old cached index may not have the newer ones.
  const flagKnown = (f) => f === "freehold" || f === "noCondition" || db.flags[f] != null;
  const count = (field, v) => (CHOICES[field].includes(v) ? facets[field][v] || 0 : null);  // none for an extra value

  function renderFilters() {
    const key = JSON.stringify({ ...q, sort: "" });  // the sort doesn't change any count
    if (key !== facetsFor) { facets = Filter.facets(db, q, CHOICES); facetsFor = key; }
    const body = $("filters-body");
    if (!body.firstChild) {  // sections are built once; their contents are redrawn (the station search keeps focus)
      body.innerHTML = ["text", "types", "price", "plan", "areas", "stations", "size", "age", "extra"].map((s) =>
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
    const any = (fn) => !q.types.length || q.types.some(fn), condos = any(isCondo), others = any((type) => !isCondo(type));
    // a section that opens on demand, saying what's chosen in it
    const fold = (k, summary, html) => `<details class="fold" data-fold="${k}"${openFolds.has(k) ? " open" : ""}><summary><h3>${esc(t(`sec.${k}`))}</h3>${
      `<span class="fold-sum${summary ? " on" : ""}">${esc(summary || t("any"))}</span>`}</summary><div class="fold-body">${html}</div></details>`;
    const list = (labels) => labels.slice(0, 3).join(", ") + (labels.length > 3 ? t("andMore", labels.length - 3) : "");
    sec("text", q.text ? `<div class="opts"><button class="chip" aria-pressed="true" data-cleartext="1">${esc(t("textFilter", q.text))}${icon("x")}</button></div>` : "");
    sec("types", `${h3("types")}<div class="opts">${Object.keys(t("types")).filter((type) => db.index.types.includes(type)).map((type) => many("types", type, typeName(type), facets.types[type] || 0)).join("")}</div>`);
    sec("areas", fold("areas", list(q.areas.map(areaName)), areaGroups()));
    sec("price", `${h3("price")}<div class="field-pair">${selectEl("priceMin", PRICES, (v) => money(v), t("minNone"), t("priceFrom"))}<span aria-hidden="true">${
      esc(t("to"))}</span>${selectEl("priceMax", PRICES, (v) => money(v), t("maxNone"), t("priceTo"))}</div>`);
    sec("plan", isLand ? "" : `${h3("plan", t("sec.planHint"))}<div class="opts">${withExtra("plan", CHOICES.plan).map((v) =>
      one("plan", v, t("planFrom", planLabel(v)), count("plan", v))).join("")}</div>`);
    sec("size", `${h3("size")}${isLand ? "" : select("sizeMin", t("floorArea"), SIZES, (v) => t("sizeMin", v))}${
      q.types.length && q.types.every(isCondo) ? "" : select("landMin", t("landArea"), LANDS, (v) => t("landMin", v, Math.round(v / TSUBO)))}`);
    sec("age", isLand ? "" : `${h3("age")}<div class="opts">${withExtra("ageMax", AGES).map((v) => one("ageMax", v, ageLabel(v), count("ageMax", v))).join("")}${
      flag("post1981", facets.post1981)}</div>`);
    stationList();
    $("walk-from").textContent = t(q.stations.length ? "walkFromChosen" : "walkFromNearest");
    $("walk-list").innerHTML = withExtra("walk", WALKS).filter((w) => q.walk === w || narrows(facets.walk[w] || 0)).map((w) => one("walk", w, t("walkWithin", w), count("walk", w))).join("");
    const tagNames = Object.keys(facets.features).sort((a, b) => facets.features[b] - facets.features[a]);
    const shownTags = [...q.features, ...tagNames.filter((f) => !q.features.includes(f) && narrows(facets.features[f])).slice(0, 30)];
    const tagChip = (f) => many("features", f, feature(f), facets.features[f] || 0);
    // こだわり: the curated flags that fit the chosen types, condos' fees, then SUUMO's tags
    const fits = { top: condos, upper: condos, tower: condos, parking: others, noCondition: others };
    const share = detailShare();
    const extras = activeConditions().filter(([, , s]) => s === "extra").map(([label]) => label);
    sec("extra", fold("extra", list(extras), `${share != null && share < 0.9 ? `<p class="hint share">${esc(t("detailShare", Math.round(share * 100)))}</p>` : ""}
      <div class="opts">${PICKS.filter((f) => q[f] || (flagKnown(f) && fits[f] !== false)).map((f) => flag(f, facets[f])).join("")}</div>
      ${condos || q.feesMax != null ? select("feesMax", t("feesLabel"), FEES, (v) => t("feesMax", v)) : ""}
      ${shownTags.length ? `<details class="group"${q.features.length ? " open" : ""}><summary>${esc(t("moreTags"))}</summary><div class="opts">${shownTags.map(tagChip).join("")}</div></details>` : ""}`));
    $("apply").textContent = !WIDE.matches && view && mode === "search" ? t("showInView", num(Filter.count({ ...db, items: inView() }, q)))
      : t("show", num(facets.total));
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

  // Areas grouped as the index says (23区, a designated city's wards, cities, towns), prefecture by prefecture.
  function areaGroups() {
    const prefs = new Set(db.index.areas.map((a) => a[2])), kinds = t("kinds");
    const groups = new Map();
    const rank = (pref) => manifest.prefs.findIndex(([, name]) => name === pref);
    const order = (a) => [rank(a[2]), a[3] === "ku" ? 0 : a[3] === "shi" ? 2 : a[3] === "gun" ? 3 : 1, a[0]];
    const sorted = [...db.index.areas].sort((a, b) => { const x = order(a), y = order(b); return x[0] - y[0] || x[1] - y[1] || x[2].localeCompare(y[2]); });
    for (const [code, name, pref, group] of sorted) {
      const city = !kinds[group];  // a designated city: its wards without the city's name
      const g = `${prefs.size > 1 ? place(pref) + " " : ""}${city ? place(group) : kinds[group]}`;
      if (!groups.has(g)) groups.set(g, []);
      groups.get(g).push([code, many("areas", code, city ? place(name).replace(group, "") || place(name) : place(name), facets.areas[code] || 0)]);
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

  // ---------- prefectures: the button before the count opens a picker (a sheet; a popover on desktop) ----------

  const REGIONS = {  // the usual 8 regions, by the prefecture names the manifest uses
    hokkaido: ["北海道"], tohoku: ["青森県", "岩手県", "宮城県", "秋田県", "山形県", "福島県"],
    kanto: ["茨城県", "栃木県", "群馬県", "埼玉県", "千葉県", "東京都", "神奈川県"],
    chubu: ["新潟県", "富山県", "石川県", "福井県", "山梨県", "長野県", "岐阜県", "静岡県", "愛知県"],
    kinki: ["三重県", "滋賀県", "京都府", "大阪府", "兵庫県", "奈良県", "和歌山県"],
    chugoku: ["鳥取県", "島根県", "岡山県", "広島県", "山口県"], shikoku: ["徳島県", "香川県", "愛媛県", "高知県"],
    kyushu: ["福岡県", "佐賀県", "長崎県", "熊本県", "大分県", "宮崎県", "鹿児島県", "沖縄県"],
  };
  // Every chosen prefecture is held in memory (all of Japan: ~11 MB to download, a few hundred MB loaded), so phones
  // and small-memory devices take up to 5; others may choose all of Japan.
  const BIG = matchMedia("(min-width: 700px)").matches && (navigator.deviceMemory ?? 8) >= 4;
  const maxPrefs = () => (BIG ? manifest.prefs.length : 5);
  const allPrefs = () => manifest.prefs.map(([slug]) => slug);
  const prefName = (slug) => place(manifest.prefs.find(([s]) => s === slug)?.[1] ?? slug);
  let prefsUrl = null;  // the URL the picker has chosen (kept when it closes, also by the back button)

  // The button ("東京都 ▾", "東京都 ほか1 ▾") and, with several prefectures, one removable chip each.
  function renderPrefBar() {
    const off = mode !== "search" || manifest.prefs.length < 2;
    $("pref").hidden = off;
    const everywhere = chosen.length > 1 && chosen.length === manifest.prefs.length;
    $("pref").innerHTML = `<span>${esc(everywhere ? t("prefAll") : t("prefButton", prefName(chosen[0]), chosen.length - 1))}</span>${icon("down")}`;
    $("pref-chips").innerHTML = off || chosen.length < 2 || everywhere ? "" : chosen.map((slug) =>
      `<span class="chip on"><button class="chip-label" data-pref-open="1">${esc(prefName(slug))}</button><button class="chip-x" data-pref-remove="${slug}" aria-label="${esc(t("remove", prefName(slug)))}">${icon("x")}</button></span>`).join("");
  }

  function renderPrefs() {
    const typed = Filter.normalize($("prefs-q").value.trim());
    const match = ([slug, name]) => !typed || [name, I18N.en.places[name], slug].some((n) => n && Filter.normalize(n).includes(typed));
    const groups = Object.keys(REGIONS).map((r) => [r, manifest.prefs.filter(([, name]) => REGIONS[r].includes(name))]);
    const other = manifest.prefs.filter(([, name]) => !Object.values(REGIONS).some((names) => names.includes(name)));
    if (other.length) groups.push(["other", other]);
    const want = prefsFromHash(), full = want.length >= maxPrefs();  // (the URL's: some may still be loading)
    $("prefs-list").innerHTML = groups.map(([r, prefs]) => {
      const rows = prefs.filter(match).map(([slug, name, n]) => {
        const on = want.includes(slug), only = on && want.length === 1;
        return `<li class="pref-row${on ? " on" : ""}"><button class="pref-go" data-pref-go="${slug}"${on ? ' aria-current="true"' : ""}>${esc(place(name))}<span class="n">${num(n)}</span></button>
          <button class="pref-add" data-pref-add="${slug}" aria-pressed="${on}"${only || (!on && full) ? " disabled" : ""} aria-label="${esc(t(on ? "remove" : "prefAddTo", place(name)))}">${esc(t(on ? "prefAdded" : "prefAdd"))}</button></li>`;
      }).join("");
      return rows && `<section class="pref-group"><h3>${esc(t("regions")[r])}</h3><ul>${rows}</ul></section>`;
    }).join("") || `<p class="meta">${esc(t("prefNone"))}</p>`;
    if (BIG && !typed) {  // all of Japan, with what it costs to load (~30 bytes per home, compressed)
      const n = manifest.prefs.reduce((sum, [, , count]) => sum + count, 0), all = want.length === manifest.prefs.length;
      $("prefs-list").insertAdjacentHTML("afterbegin", `<button class="btn pref-all" data-pref-all="1" aria-pressed="${all}"${all ? " disabled" : ""}>${
        esc(t("prefAllPick", num(n), Math.max(1, Math.round(n * 30 / 1e6))))}</button>`);
    }
    $("prefs-note").textContent = full && !BIG ? t("prefMax", maxPrefs()) : "";
  }

  // Back closes the picker and keeps what it chose (as the filter sheet does).
  function openPrefs(open, popped) {
    const box = $("prefs");
    if (open === !box.hidden) return;
    if (!open && !popped && history.state?.prefs) { history.back(); return; }  // popstate closes it
    if (open) {
      lastFocus = document.activeElement;
      history.pushState({ prefs: true }, "", location.href);
      prefsUrl = location.href;
      $("prefs-q").value = "";
      renderPrefs();
    }
    box.hidden = !open;
    $("pref").setAttribute("aria-expanded", open);
    const modal = open && !WIDE.matches;
    for (const el of [document.querySelector(".bar"), document.querySelector(".layout")]) el.inert = modal;
    if (!open) { lastFocus?.focus(); return; }
    if (WIDE.matches) {  // a popover under the button
      const r = $("pref").getBoundingClientRect(), panel = box.firstElementChild;
      panel.style.top = `${r.bottom + 6}px`;
      panel.style.left = `${Math.max(8, Math.min(r.left, innerWidth - panel.offsetWidth - 8))}px`;
    } else box.firstElementChild.style.top = box.firstElementChild.style.left = "";
    $("prefs-list").querySelector('[aria-current="true"]')?.scrollIntoView({ block: "center" });
    (WIDE.matches || !COARSE.matches ? $("prefs-q") : $("prefs-close")).focus();
  }

  function setPrefs(next, now = true) {  // the URL says which; route loads and shows them
    const p = new URLSearchParams(location.hash.slice(1));
    if (next.join() === manifest.prefs[0][0]) p.delete("p");
    else p.set("p", next.length > 1 && next.length === manifest.prefs.length ? "all" : next.join(","));
    history.replaceState(history.state, "", "#" + p.toString());
    prefsUrl = location.href;
    if (now) route();
  }

  function onPrefsClick(e) {
    if (e.target === $("prefs")) return openPrefs(false);  // outside the panel
    const go = e.target.closest("[data-pref-go]"), add = e.target.closest("[data-pref-add]");
    if (go) { setPrefs([go.dataset.prefGo], !history.state?.prefs); openPrefs(false); }  // (closing routes)
    else if (e.target.closest("[data-pref-all]")) { setPrefs(allPrefs(), !history.state?.prefs); openPrefs(false); }
    else if (add) {
      const s = add.dataset.prefAdd, want = prefsFromHash();
      setPrefs(want.includes(s) ? want.filter((x) => x !== s) : [...want, s].slice(0, maxPrefs()));
      renderPrefs();
      $("prefs-list").querySelector(`[data-pref-add="${s}"]`)?.focus();
    }
  }

  // ---------- map: always there; the list is what's in its view ----------

  let leaflet = null, map = null, cluster = null;
  let view = null;              // [south, west, north, east]: what the map shows (null: no map; everything is listed)
  let waiting = true;           // the first list waits for the map's view (at most MAP_WAIT ms)
  let mapAt = null;             // the view in the URL: "lat,lng,zoom" of its centre
  let fitNext = null;           // where the map goes next: "results", {m: "lat,lng,zoom"}, {b: [s, w, n, e]}, {id: key}
  let lastPlaces = [];          // the areas and stations drawn (naming another one fits the map to the results)
  let autopan = false;          // a popup moved the map: the list stays as it is
  const MAP_WAIT = 2500;
  const inArea = (list, b) => list.filter((it) => it.lat != null && it.lat >= b[0] && it.lng >= b[1] && it.lat <= b[2] && it.lng <= b[3]);
  let viewItems = null, viewItemsFor = null;  // the chosen prefectures' listings in view (the quick chips count them)
  const inView = () => (viewItemsFor === `${chosen}|${view}` ? viewItems : ((viewItemsFor = `${chosen}|${view}`), (viewItems = inArea(db.items, view))));

  // After a pan or zoom: the list, its counts and the URL follow (the URL is replaced, not a history entry).
  let moveTimer = null;
  function onMapMove() {
    clearTimeout(moveTimer);
    moveTimer = setTimeout(updateView, 150);
  }
  function updateView() {
    const size = map.getSize(), h = size.y - hidden();
    if (h < 40) return;  // the sheet covers the map: the list stays as it was
    const nw = map.containerPointToLatLng([0, 0]), se = map.containerPointToLatLng([size.x, h]), c = map.containerPointToLatLng([size.x / 2, h / 2]);
    view = [se.lat, nw.lng, nw.lat, se.lng];
    mapAt = `${c.lat.toFixed(5)},${c.lng.toFixed(5)},${map.getZoom()}`;
    waiting = false;
    if (!railAsked) { railAsked = true; (window.requestIdleCallback || setTimeout)(drawRail); }  // the lines: once listed
    history.replaceState(history.state, "", hashFor());
    saveLast();
    if (autopan) { autopan = false; return; }
    if (document.body.classList.contains("resizing")) return;  // (the divider: once it's let go)
    if (document.body.classList.contains("show-filters") && !WIDE.matches) listStale = true;
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
  function markOpen() {
    const it = isListing() && allByKey.get(tab);
    openMarker?.remove();
    openMarker = it?.lat != null && map ? L.marker([it.lat, it.lng], { icon: L.divIcon({ className: "", html: '<div class="open-pin"></div>', iconSize: [30, 30] }),
      interactive: false, keyboard: false, zIndexOffset: 1000 }).addTo(map) : null;
  }

  // Moves the map as fitNext says, once it's there.
  function placeMap() {
    const how = fitNext, it = how?.id && allByKey.get(how.id), [lat, lng, z] = how?.m?.split(",").map(Number) ?? [];
    if (!map || !how) return;
    fitNext = null;
    if (how.m) map.setView(centreFor([lat, lng], z), z);
    else if (how.b) map.fitBounds([[how.b[0], how.b[1]], [how.b[2], how.b[3]]], { paddingBottomRight: [0, hidden()] });
    else if (it?.lat != null) map.setView(centreFor([it.lat, it.lng], 15), 15);
    else {  // the results (none: the prefectures)
      const placed = mapHits.filter((x) => x.lat != null).map((x) => [x.lat, x.lng]);
      const points = placed.length ? placed : db.index.towns.filter((tw) => tw[1] != null).map((tw) => tw.slice(1));
      if (points.length) map.fitBounds(coreBounds(points), { paddingTopLeft: [30, 30], paddingBottomRight: [30, 30 + hidden()], maxZoom: 15 });
    }
    onMapMove();  // (also when it was there already)
  }

  // Leaflet loaded (true) or not available (false; retried next time).
  function mapReady() {
    leaflet ||= Promise.all(LEAFLET.map(([kind, url, integrity]) => new Promise((ok, fail) => {
      const el = document.createElement(kind === "css" ? "link" : "script");
      Object.assign(el, kind === "css" ? { rel: "stylesheet", href: url } : { src: url, async: false });
      Object.assign(el, { integrity, crossOrigin: "", onload: ok, onerror: fail });
      document.head.append(el);
    }))).then(() => true, () => { leaflet = null; return false; });
    return leaflet;
  }

  // Markers: a town (丁目) is a pill with its count, or from PRICE_ZOOM a price tag (its lowest price, and how many);
  // a cluster of towns is a filled circle sized by its count (a tap zooms in). Icons are reused across redraws.
  const PRICE_ZOOM = 15;
  const pins = new Map();
  const cachedIcon = (key, make) => pins.get(key) || pins.set(key, make()).get(key);
  function townIcon(list, priced) {
    if (!priced) {
      const label = num(list.length);
      return cachedIcon(`t${label}`, () => L.divIcon({ className: "", html: `<div class="pin">${label}</div>`, iconSize: [Math.max(28, 14 + label.length * 8), 24] }));
    }
    const lo = list.reduce((m, it) => (it.price != null && (m == null || it.price < m) ? it.price : m), null);
    const label = lo == null ? t("undecided") : t("pinPrice", lo), more = list.length > 1 ? list.length : 0;
    return cachedIcon(`p${label}|${more}`, () => {
      const w = 18 + label.length * (S.lang === "en" ? 7 : 10) + (more ? 10 + String(more).length * 7 : 0);
      return L.divIcon({ className: "", html: `<div class="pin price">${esc(label)}${more ? `<i>${num(more)}</i>` : ""}</div>`,
        iconSize: [w, 26], iconAnchor: [w / 2, 32] });
    });
  }
  function clusterIcon(n) {
    const size = n < 10 ? 30 : n < 100 ? 36 : n < 1000 ? 44 : 52;
    return cachedIcon(`c${n}`, () => L.divIcon({ className: "", html: `<div class="cl" style="width:${size}px;height:${size}px">${num(n)}</div>`, iconSize: [size, size] }));
  }
  let priced = false;  // the town markers show prices (zoomed in)
  function onZoom() {
    const now = map.getZoom() >= PRICE_ZOOM;
    if (now === priced) return;
    priced = now;
    cluster.eachLayer((m) => m.setIcon(townIcon(m.options.list, priced)));
    cluster.refreshClusters();
  }

  function createMap() {
    const motion = !matchMedia("(prefers-reduced-motion: reduce)").matches;
    map = L.map("map", { zoomControl: false, zoomAnimation: motion, fadeAnimation: motion, markerZoomAnimation: motion,
      wheelPxPerZoomLevel: 120, wheelDebounceTime: 80 });  // a wheel notch: about one level
    map.setView([35.68, 139.76], 10);  // a view before the layers (the cluster layer needs one); placed later
    // Leaflet measures its box only on window resizes; the layout also resizes it (the filter rail, the panel), and a
    // stale size leaves part of the map without tiles
    new ResizeObserver(() => map.invalidateSize({ pan: false, debounceMoveend: true })).observe(map.getContainer());
    map.on("popupopen", (e) => e.popup.getElement()?.querySelector(".popup-list a")?.focus({ preventScroll: true }));
    map.on("autopanstart", () => { autopan = true; });
    map.on("moveend", onMapMove);
    map.on("zoomend", onZoom);
    if (!COARSE.matches) L.control.zoom({ position: "topright" }).addTo(map);
    map.attributionControl.setPrefix(false);  // no "Leaflet" and flag: only the data credits, kept short
    map.attributionControl.setPosition(SPLIT.matches ? "bottomright" : "topleft");  // (phones: the sheet is at the bottom)
    L.tileLayer(TILES, { maxZoom: 18, attribution: '<a href="https://maps.gsi.go.jp/development/ichiran.html" target="_blank" rel="noopener">地理院タイル</a>' }).addTo(map);
    cluster = L.markerClusterGroup({ maxClusterRadius: 48, showCoverageOnHover: false, chunkedLoading: true,
      iconCreateFunction: (c) => clusterIcon(c.getAllChildMarkers().reduce((n, m) => n + m.options.list.length, 0)) });
    map.addLayer(cluster);
  }

  async function drawMap() {
    if (!(await mapReady())) {  // (unpkg unreachable) everything is listed
      $("map").innerHTML = `<p class="map-failed">${esc(t("mapFailed"))}</p>`;
      return listResults((waiting = false));
    }
    if (!map) createMap();
    const towns = new Map();
    priced = map.getZoom() >= PRICE_ZOOM;
    for (const it of mapHits) {
      if (it.lat == null) continue;
      if (!towns.has(it.town)) towns.set(it.town, [it.lat, it.lng, []]);
      towns.get(it.town)[2].push(it);
    }
    cluster.clearLayers();
    cluster.addLayers([...towns].map(([town, [lat, lng, list]]) =>
      L.marker([lat, lng], { icon: townIcon(list, priced), list, title: `${town} ${t("count", num(list.length))}` }).bindPopup(() => popup(town, list), { autoPanPadding: [56, 56] })));
    showRail();  // the chosen stations' lines stand out
    markOpen();
    placeMap();
  }

  // ---------- railway lines and stations (data/rail.json: MLIT 鉄道データ; colours, English names from ODPT) ----------
  let railData = null;              // loading or loaded with the first map: {operators, lines, stations}
  let railLoaded = null;            // ... and once loaded
  const loadRail = () => (railData ||= fetch("data/rail.json").then((r) => r.json()).then((d) => {
    railLoaded = d;
    for (const line of d.lines) {    // each line's box, to find what's in view
      const pts = line[4].flat();
      line.box = [Math.min(...pts.map((p) => p[1])), Math.min(...pts.map((p) => p[0])), Math.max(...pts.map((p) => p[1])), Math.max(...pts.map((p) => p[0]))];
    }
    return d;
  }).catch(() => { railData = null; return null; }));
  const stationRadius = (z, lines) => (z < 13 ? 3 : z < 15 ? 4.5 : 6) + (lines > 1 ? 1.5 : 0);
  const halo = () => (document.documentElement.dataset.theme === "dark" ? "#0d0f13" : "#ffffff");  // under each line
  const latlngs = (parts) => parts.map((p) => p.map(([lng, lat]) => [lat, lng]));
  const lineName = (line) => (S.lang === "en" && line[2]) || line[1];
  const operatorName = (op) => (S.lang === "en" && op[1]) || op[3] || op[0];  // JR東日本 rather than 東日本旅客鉄道
  let rail = null, railLines = [], railHalos = [], railStations = null, railLabels = null, railAsked = false;
  const hiddenOps = new Set([].concat(settings.railHidden || []));  // operators switched off (remembered)

  async function drawRail() {
    rail = await loadRail();
    if (!rail) return;  // the map works without it
    const canvas = L.canvas({ padding: 0.3 });
    railHalos = rail.lines.map(([, , , , parts]) => L.polyline(latlngs(parts), { renderer: canvas, interactive: false }));
    railLines = rail.lines.map(([, , , colour, parts]) => L.polyline(latlngs(parts), { renderer: canvas, color: colour, interactive: false }));
    // stations as on a transit map: a white dot in a dark ring, bigger where lines meet and as you zoom in
    railStations = L.layerGroup(rail.stations.map(([name, lat, lng, lines]) => L.circleMarker([lat, lng], { renderer: canvas, radius: 4,
      color: "#1d2026", weight: 2, fillColor: "#fff", fillOpacity: 1, lines: lines.length }).bindTooltip(name, { direction: "top", offset: [0, -5] })
      .bindPopup(() => stationPopup(name, lines))));
    railLabels = L.layerGroup();
    map.attributionControl.addAttribution(`<span title="${esc(rail.attribution)}">${esc(t("railCredit"))}</span>`);
    const open = document.createElement("button"), panel = document.createElement("div");
    open.className = "rail-btn";
    panel.className = "rail-panel";
    panel.hidden = true;
    open.addEventListener("click", () => { panel.hidden = !panel.hidden; if (!panel.hidden) railPanel(); });
    panel.addEventListener("keydown", (e) => { if (e.key === "Escape") { panel.hidden = true; open.focus(); } });
    panel.addEventListener("change", (e) => {  // a switch: all lines, or one operator
      const op = e.target.dataset.op;
      if (op == null) settings.rail = e.target.checked;
      else if (e.target.checked) hiddenOps.delete(op); else hiddenOps.add(op);
      settings.railHidden = [...hiddenOps];
      saveSettings();
      showRail();
    });
    panel.addEventListener("click", (e) => {
      const b = e.target.closest("button[data-ops]");
      if (!b) return;
      for (const op of railOperatorsInView()) if (b.dataset.ops === "none") hiddenOps.add(op[0]); else hiddenOps.delete(op[0]);
      panel.dispatchEvent(new Event("change"));
      railPanel();
    });
    for (const el of [open, panel]) { L.DomEvent.disableClickPropagation(el); L.DomEvent.disableScrollPropagation(el); $("map").append(el); }
    map.on("zoomend", showRail);
    map.on("moveend", () => { if (!panel.hidden) railPanel(); stationLabels(); });
    showRail();
  }

  // Station names on the map once zoomed in (14+): only those in view, so a few dozen labels at most.
  function stationLabels() {
    railLabels.clearLayers();
    if (!map.hasLayer(railStations) || map.getZoom() < 14) return;
    const b = map.getBounds().pad(0.1);
    const shown = rail.stations.filter(([, lat, lng]) => b.contains([lat, lng])).slice(0, 200);
    for (const [name, lat, lng] of shown) {
      L.marker([lat, lng], { interactive: false, keyboard: false,
        icon: L.divIcon({ className: "station-label", html: esc(stationName(name)), iconSize: null, iconAnchor: [-9, 8] }) }).addTo(railLabels);
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
  function chosenLines() {
    const names = new Set(q.stations.map((s) => s.replace(/（.*）$/, "")));
    return new Set(names.size ? rail.stations.filter(([n]) => names.has(n)).flatMap(([, , , lines]) => lines) : []);
  }

  // Lines unless switched off (all, or by operator), each on a halo so it reads on any part of the map; stations once
  // there's room for them (zoom 11+), their names from 14.
  function showRail() {
    if (!rail || !map) return;
    const z = map.getZoom(), on = settings.rail !== false, focus = chosenLines(), colour = halo();
    const base = z < 11 ? 2 : z < 13 ? 3 : z < 15 ? 4.5 : 6;
    railLines.forEach((l, i) => {
      const visible = on && !hiddenOps.has(rail.operators[rail.lines[i][0]][0]), h = railHalos[i];
      if (visible !== map.hasLayer(l)) { if (visible) { h.addTo(map); l.addTo(map); } else { h.remove(); l.remove(); } }
      if (!visible) return;
      const lit = focus.has(i), faded = focus.size && !lit, weight = base + (lit ? 3 : 0);
      l.setStyle({ weight, opacity: faded ? 0.25 : z < 11 ? 0.75 : 0.95 });
      h.setStyle({ weight: weight + 3, color: colour, opacity: faded ? 0 : z < 11 ? 0.5 : 0.85 });
      if (lit) { h.bringToFront(); l.bringToFront(); }
    });
    const stations = on && z >= 11;
    if (stations !== map.hasLayer(railStations)) {
      if (stations) { railStations.addTo(map); railLabels.addTo(map); } else { railStations.remove(); railLabels.remove(); }
    }
    if (stations) railStations.eachLayer((m) => m.setStyle({ radius: stationRadius(z, m.options.lines) }));
    stationLabels();
    const b = $("map").querySelector(".rail-btn");
    b.textContent = t("railLayer");
    b.setAttribute("aria-pressed", on);
  }

  // A station on the map: its lines, and its homes under the current conditions in one tap.
  function stationPopup(name, lines) {
    const s = db.index.stations.find((n) => n === name || n.startsWith(name + "（"));
    const n = s ? Filter.count(db, { ...q, stations: [s] }) : 0;  // under the current conditions
    return `<strong>${esc(t("station", stationName(s || name)))}</strong><div class="meta wrap">${esc(lines.map((i) => lineName(rail.lines[i])).join("・"))}</div>${
      n ? `<button class="btn primary station-pick" data-station-pick="${esc(s)}">${esc(t("stationHomes", num(n)))}</button>` : ""}`;
  }

  // Where the results are, without far outliers (islands, a stray geocode): the 2nd-98th percentile box; all of
  // them when there are few.
  function coreBounds(points) {
    const pick = (i) => points.map((p) => p[i]).sort((a, b) => a - b), cut = points.length < 20 ? 0 : 0.02;
    const lat = pick(0), lng = pick(1), at = (a, f) => a[Math.min(a.length - 1, Math.floor((a.length - 1) * f + 0.5))];
    return [[at(lat, cut), at(lng, cut)], [at(lat, 1 - cut), at(lng, 1 - cut)]];
  }

  function popup(town, list) {
    return `<strong>${esc(town)}</strong> ${t("count", num(list.length))}<ul class="popup-list">${list.slice(0, 40).map((it) =>
      `<li><a href="${esc(hashFor({ id: it.key }))}" data-key="${esc(it.key)}">${it.image ? `<img src="${esc(image(it, 120, 90))}" alt="" loading="lazy">` : "<span></span>"}
        <span><b class="num">${money(it.price)}</b> ${usd(it)}<br>${esc([value(it.layout) || typeName(it.type), age(it)].filter(Boolean).join(" · "))}<br>${esc(station(it))}</span></a></li>`).join("")}</ul>`;
  }

  // ---------- listing ----------

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

  // Listings side by side (the 比較 tab: the open listings): a row per fact, a column per listing (× closes its tab).
  // A numeric row marks its best value (lowest price, largest area...).
  async function compareHtml(list) {
    const recs = await Promise.all(list.map((it) => record(it.key).catch(() => null)));
    const cols = list.map((it, i) => [it, recs[i] || {}]);
    const yen = (v) => (v ? t("yen", num(v)) : "");
    const monthly = ([it, r]) => (isCondo(it.type) ? it.fees ?? (r.mgmt_fee && r.repair_fee ? r.mgmt_fee + r.repair_fee + (r.other_monthly || 0) : null) : null);
    const walk = ([it]) => it.stations.reduce((m, [, w]) => (w != null && (m == null || w < m) ? w : m), null);
    const newBuild = (it) => it.type === "new_condo" || it.type === "new_house";
    // [label, cell html, best: [value of a column, "min" | "max"]]
    const ROWS = [
      ["", ([it]) => `<div class="cmp-top"><a href="${esc(hashFor({ id: it.key }))}" data-key="${esc(it.key)}"><div class="ph">${it.image ? `<img src="${esc(image(it, 240, 180))}" alt="" loading="lazy" onerror="this.remove()">` : ""}</div></a>${
        `<button class="cmp-x" data-tab-close="${esc(it.key)}" aria-label="${esc(t("cmpHide", money(it.price)))}">${icon("x")}</button>`}</div>`],
      ["name", ([it]) => `<a class="cmp-name" href="${esc(hashFor({ id: it.key }))}" data-key="${esc(it.key)}">${esc(it.name || typeName(it.type))}</a>`],
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
      ["units", ([, r]) => esc(r.units ? t("unitsN", r.units) : "")], ["rights", ([, r]) => esc(value(r.land_rights) || "")],
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
  async function renderListing(box, key) {
    const it = allByKey.get(key);
    box.dataset.key = key;
    box.innerHTML = '<div class="d-head"><div class="skeleton"></div></div>';
    let r;
    try {
      r = await record(key);
    } catch (e) {  // 404: the listing ended; anything else: the network
      if (box.dataset.key === key) box.innerHTML = `<div class="empty"><h3>${esc(t(e.message === "404" ? "gone" : "offline"))}</h3></div>`;
      return null;
    }
    if (box.dataset.key !== key) return null;
    box.innerHTML = detailHtml(r, it);
    if (S.lang === "en" && r.stations?.length && !railLoaded) {  // line names in English once the rail data is in
      loadRail().then(() => { const dd = box.querySelector(".d-access"); if (dd && box.dataset.key === key) dd.innerHTML = accessHtml(r); });
    }
    return r;
  }

  function detailHtml(r, it) {
    const type = r.type, L_ = (k) => t(`d.${k}`);
    const rows = (pairs) => pairs.filter(([, v]) => v != null && v !== "").map(([k, v]) => `<dt>${esc(L_(k))}</dt><dd>${v}</dd>`).join("");
    const sec = (k, pairs) => { const html = rows(pairs); return html ? `<section class="d-sec"><h3>${esc(L_(k))}</h3><dl>${html}</dl></section>` : ""; };
    const yen = (v) => (v ? esc(t("yen", num(v))) : null);
    const txt = (s) => (s == null || s === "" ? null : esc(value(s)));
    const second = (main, sub) => esc(main) + (sub ? `<small>${esc(sub)}</small>` : "");  // 坪 on a line of its own
    const unit = it?.unit ? second(t("perM2", it.unit), !isCondo(type) && t("perTsubo", Math.round(it.unit * TSUBO))) : "";
    const monthly = it?.fees ?? (r.mgmt_fee && r.repair_fee ? r.mgmt_fee + r.repair_fee + (r.other_monthly || 0) : null);
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
      ${r.image ? `<img class="hero" src="${esc(image({ image: r.image }, 640, 480))}" alt="" onerror="this.remove()">` : ""}
      <div class="d-head">
        <h2 tabindex="-1">${esc(r.name || [r.layout, typeName(type)].filter(Boolean).join(" "))}</h2>
        <div class="meta">${esc([r.address, r.name && typeName(type)].filter(Boolean).join(" · "))}</div>
        ${it?.lat != null && map ? `<button class="btn ghost on-map" data-on-map="${esc(it.key)}">${icon("pin")}${esc(t("showOnMap"))}</button>` : ""}
        <div class="d-price num">${it ? price(it, true) + usd(it) : money(r.price, true) + usd({ price: r.price })}${r.price_excludes_building ? `<small class="note">${esc(t("excludesBuilding"))}</small>` : ""}</div>
        <div class="tags">${it ? tags(it) : ""}</div>
      </div>
      <div class="facts">${facts.map(([k, v]) => `<div class="fact"><span>${esc(L_(k))}</span><b>${v}</b></div>`).join("")}</div>
      ${changes ? `<section class="d-sec"><h3>${esc(L_("history"))}</h3>${historyChart(r, it)}<dl>${rows([["price", changes]])}</dl></section>` : ""}
      ${it ? market(it) : ""}
      ${access ? `<section class="d-sec"><h3>${esc(L_("access"))}</h3><dl><dt>${esc(L_("nearest"))}</dt><dd class="d-access">${access}</dd></dl></section>` : ""}
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
        ["seen", day(r.new_date || r.first_seen)]])}`;
  }

  // The stations of a listing: line, station, how far.
  function accessHtml(r) {
    return (r.stations || []).map((s) => nowrapWalk([lineLabel(s.line), s.bus_stop ? t("busStop", s.name) : t("station", stationName(s.name)), s.bus ? [t("busMin", s.bus), s.walk != null && t("stopWalk", s.walk)].filter(Boolean).join(" ")
      : s.walk != null ? t("walkMin", s.walk) : s.car_km ? t("carKm", s.car_km_max ? `${s.car_km}–${s.car_km_max}` : s.car_km) : ""].filter(Boolean).join(" "))).join("<br>");
  }

  // A listing's line name in English when the rail data knows it ("ＪＲ東海道本線" -> "JR Tokaido Line"), else as
  // written with full-width letters made plain ("JR東海道本線").
  const lineNames = new Map();
  function lineLabel(name) {
    const plain = (name || "").normalize("NFKC");
    if (S.lang !== "en" || !railLoaded || !plain) return S.lang === "en" ? plain : name;
    if (!lineNames.has(plain)) {
      const key = (n) => n.normalize("NFKC").replace(/本線$/, "線").replace(/[・\s]/g, "");
      const short = (op) => (op[3] || op[0]).replace(/(電気鉄道|電鉄|鉄道|��式会社)$/, "");
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

  // The price over time: a step line with a dot per change (the text rows say the same).
  function historyChart(r, it) {
    const h = r.history || [];
    if (!h.length) return "";
    const pts = [[r.new_date || r.first_seen || h[0][0], h[0][1]], ...h.map(([d, , p]) => [d, p])];
    const time = (d) => Date.parse(d), t0 = time(pts[0][0]);
    const end = [(manifest.updated || "").slice(0, 10), pts.at(-1)[0]].sort().at(-1);  // the line runs to today
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
  // within 10 years of it). Condos and land only (a house's price per ㎡ of building is mostly its land).
  function market(it) {
    if (!it.unit || !(isCondo(it.type) || it.type === "land")) return "";
    const band = it.type.startsWith("used") && it.ageMonths != null, units = [];
    for (const o of (it.part || db).items) {
      if (o.area === it.area && o.type === it.type && o.unit && (!band || (o.ageMonths != null && Math.abs(o.ageMonths - it.ageMonths) <= 120))) units.push(o.unit);
    }
    if (units.length < 10) return "";
    units.sort((a, b) => a - b);
    const at = (f) => units[Math.round(f * (units.length - 1))], lo = at(0.05), hi = at(0.95), mid = at(0.5);
    const pos = (v) => Math.max(0, Math.min(100, hi > lo ? ((v - lo) / (hi - lo)) * 100 : 50)).toFixed(1);
    const diff = Math.round((it.unit / mid - 1) * 100), where = place(it.areaName), kind = typeName(it.type);
    return `<section class="d-sec market"><h3>${esc(t("market"))}</h3>
      <div class="mbar" aria-hidden="true"><i style="left:${pos(at(0.25))}%"></i><i class="mid" style="left:${pos(mid)}%"></i><i style="left:${pos(at(0.75))}%"></i><b style="left:${pos(it.unit)}%"></b></div>
      <div class="mscale" aria-hidden="true"><span>${esc(t("perM2", lo))}</span><span>${esc(t("perM2", hi))}</span></div>
      <p>${esc(Math.abs(diff) < 3 ? t("marketSame", where, kind) : t("marketDiff", where, kind, Math.abs(diff), diff < 0))}</p>
      <p class="meta">${esc(t("marketN", num(units.length), band))}</p></section>`;
  }

  // ---------- the panel's tabs: the list, the listings opened from it, 比較 ----------
  // Opening a listing from the list is a history entry (back returns to the list); switching and closing tabs aren't.
  const MAX_TABS = 12;
  const tabs = [];          // listing keys, in the order opened
  let tab = "list";         // "list", a listing key or "compare"
  let openedFrom = null;    // the card whose listing is open (focus returns to it)
  const listY = {};         // the list's scroll position per mode (search, favorites)
  const isListing = (k = tab) => k !== "list" && k !== "compare";
  const addTabs = (keys) => { for (const k of keys) if (!tabs.includes(k)) tabs.push(k); tabs.splice(0, Math.max(0, tabs.length - MAX_TABS)); };

  // A listing (from a card, the map, the strip) or 比較.
  function openTab(key) {
    if (key === "list") return toList();
    if (isListing(key)) addTabs([key]);
    const from = tab;
    if (from === "list") openedFrom = key;
    showTab(key);
    if (from === "list") history.pushState({ tab: 1 }, "", hashFor()); else history.replaceState(history.state, "", hashFor());
  }
  // Favorites side by side: they open as tabs (the newest twelve), shown in 比較.
  function compareFavs(push = true) {
    addTabs(hits.slice(0, MAX_TABS).map((it) => it.key));
    if (push) openTab("compare"); else showTab("compare");
  }
  // drawn again (another language, USD on or off)
  function rerenderTab() {
    $("panel-body").dataset.key = "";
    showTab(tab);
  }

  function closeTab(key) {
    if (key === "all") tabs.length = 0; else if (tabs.includes(key)) tabs.splice(tabs.indexOf(key), 1);
    if (tab !== "list" && (key === "all" || key === tab || (tab === "compare" && tabs.length < 2))) toList();
    else if (tab === "compare") rerenderTab(); else renderTabs();
    if (!tabs.length) $("count").focus({ preventScroll: true });  // (its button is gone)
  }

  // Back to the list: the listing's history entry goes (a shared link to a listing stays on the site).
  function toList() {
    if (tab === "list") return;
    if (history.state?.tab) { history.back(); return; }  // popstate shows the list
    showTab("list");
    history.replaceState(null, "", hashFor());
  }

  function showTab(next) {
    if (next === "compare" && tabs.length < 2) next = "list";
    if (isListing(next) && !tabs.includes(next)) next = "list";
    const was = tab;
    if (was === "list" && next !== "list") listY[mode] = $("results").scrollTop;
    // phones: a listing (or 比較) takes the whole sheet; the list gets its height back
    if (was === "list" && next !== "list" && !SPLIT.matches) { sheetBefore = $("panel").dataset.sheet; setSheet("full", false); }
    if (next === "list" && sheetBefore) { setSheet(sheetBefore, false); sheetBefore = null; }
    tab = next;
    $("panel").dataset.tab = isListing() ? "listing" : tab;
    $("results").hidden = tab !== "list";
    $("panel-body").hidden = tab === "list";
    renderTabs();
    markOpen();
    const body = $("panel-body");
    if (tab === "list") {
      body.dataset.key = "";
      body.innerHTML = "";
      if (was === "list") return;
      if (listStale) render();  // the map moved meanwhile
      restoreList();
      const from = openedFrom && $("list").querySelector(`[data-key="${CSS.escape(openedFrom)}"]`);
      (from || $("count")).focus({ preventScroll: true });
      return;
    }
    if (was === "list") body.scrollTop = 0;
    if (tab === "compare") {
      body.dataset.key = "compare";
      body.innerHTML = '<div class="skeleton"></div>';
      compareHtml(tabs.map((k) => allByKey.get(k)).filter(Boolean)).then((html) => { if (body.dataset.key === "compare") body.innerHTML = html; });
    } else if (body.dataset.key !== tab) {
      const key = tab;
      renderListing(body, key).then((r) => {
        if (body.dataset.key !== key) return;
        const ended = !r || allByKey.get(key)?.flags & db.flags.gone;  // ended: SUUMO's page is gone too
        body.insertAdjacentHTML("beforeend", `<div class="actions">
          <button class="btn fav-btn" data-fav="${esc(key)}" aria-pressed="${favs.has(key)}">${icon("heart")}<span>${esc(t(favs.has(key) ? "saved" : "save"))}</span></button>
          ${ended ? "" : `<a class="btn primary" href="${esc(safeUrl(r.url))}" target="_blank" rel="noopener">${esc(t("openSuumo"))}${icon("external")}</a>`}</div>`);
        body.querySelector("h2")?.focus({ preventScroll: true });
      });
    }
  }

  // The tab strip, shown once a listing is open: the list, each listing (×), 比較 with two or more, すべて閉じる.
  function renderTabs() {
    const strip = $("tabs");
    document.querySelectorAll(".card").forEach((c) => c.classList.toggle("open", tabs.includes(c.querySelector("[data-key]")?.dataset.key)));
    strip.hidden = !tabs.length;
    if (strip.hidden) return;
    const button = (id, label, x) => `<span class="tab${tab === id ? " on" : ""}" role="presentation">
      <button role="tab" aria-selected="${tab === id}" aria-controls="${id === "list" ? "results" : "panel-body"}" data-tab-show="${esc(id)}">${
      id === "list" ? icon("back") : ""}${esc(label)}</button>${
      x ? `<button class="tab-x" data-tab-close="${esc(id)}" aria-label="${esc(t("closeTab", label))}">${icon("x")}</button>` : ""}</span>`;
    strip.innerHTML = button("list", `${t(mode === "favorites" ? "favorites" : "listTab")} ${num(hits.length)}`) +
      tabs.map((key) => {
        const it = allByKey.get(key);
        return button(key, it ? `${money(it.price)} ${value(it.layout) || typeName(it.type)}` : key, true);
      }).join("") + (tabs.length > 1 ? button("compare", t("compareTab")) : "") +
      `<button class="tab-all" data-tab-close="all">${esc(t("closeAll"))}</button>`;
    strip.querySelector(".on")?.scrollIntoView({ block: "nearest", inline: "nearest" });
  }

  // ---------- the bottom sheet (below 1024 px): the panel over the map, at peek, half or full height ----------

  const SPLIT = matchMedia("(min-width: 1024px)");  // map and panel side by side (style.css too)
  const SHEETS = ["peek", "half", "full"];
  let sheetBefore = null;   // the sheet's state before a listing took all of it
  let dragEnd = 0;
  function setSheet(state, save = true) {
    $("panel").dataset.sheet = state;
    sheetSemantics();
    if (save) { settings.sheet = state; saveSettings(); }
    if (map) setTimeout(onMapMove, 300);  // what's in view: what the sheet leaves visible (once it's there)
  }
  function wireSheet() {
    const panel = $("panel"), head = panel.querySelector(".panel-head");
    $("sheet-handle").addEventListener("click", () => setSheet(SHEETS[(SHEETS.indexOf(panel.dataset.sheet) + 1) % 3]));
    head.addEventListener("click", (e) => { if (Date.now() - dragEnd < 300) { e.stopPropagation(); e.preventDefault(); } }, true);
    // dragged by its head: it snaps to the nearest state
    head.addEventListener("pointerdown", (e) => {
      if (SPLIT.matches || e.button) return;
      const y0 = e.clientY, from = panel.getBoundingClientRect().top - panel.offsetTop;  // (offsetTop: a full sheet's top)
      let moved = false;
      const move = (ev) => {
        if (!moved && Math.abs(ev.clientY - y0) < 8) return;
        if (!moved) { moved = true; head.setPointerCapture(e.pointerId); panel.classList.add("dragging"); }
        panel.style.transform = `translateY(${Math.max(0, from + ev.clientY - y0)}px)`;
      };
      const end = () => {
        head.removeEventListener("pointermove", move);
        if (!moved) return;
        const at = panel.getBoundingClientRect().top;
        panel.style.transform = "";
        const tops = SHEETS.map((s) => { panel.dataset.sheet = s; return Math.abs(panel.getBoundingClientRect().top - at); });
        panel.classList.remove("dragging");
        dragEnd = Date.now();
        setSheet(SHEETS[tops.indexOf(Math.min(...tops))]);
      };
      head.addEventListener("pointermove", move);
      head.addEventListener("pointerup", end, { once: true });
      head.addEventListener("pointercancel", end, { once: true });
    });
    panel.addEventListener("focusin", (e) => { if (panel.dataset.sheet === "peek" && !head.contains(e.target)) setSheet("half"); });
    SPLIT.addEventListener("change", () => {  // one layout to the other: the sheet back as it was, the map resized
      sheetBefore = null;
      setSheet(SPLIT.matches || tab === "list" ? settings.sheet || "half" : "full", false);
      map?.attributionControl.setPosition(SPLIT.matches ? "bottomright" : "topleft");
      if (document.activeElement?.closest("[inert]")) $("count").focus();
    });
    setSheet(settings.sheet || "half", false);
  }

  // ---------- the divider between the map and the list (wide screens): drag, arrow keys, double click ----------

  const PANEL = 440, PANEL_MIN = 340, MAP_MIN = 360;  // px
  function setPanel(px, save = false) {
    const layout = document.querySelector(".layout"), rail = railOn() && mode === "search" ? 280 : 0;
    const max = Math.max(PANEL_MIN, layout.clientWidth - rail - 8 - MAP_MIN), w = Math.round(Math.min(max, Math.max(PANEL_MIN, px)));
    layout.style.setProperty("--panel", `${w}px`);
    for (const [k, v] of Object.entries({ min: PANEL_MIN, max, now: w })) $("split").setAttribute(`aria-value${k}`, v);
    if (save) { settings.panel = w; saveSettings(); }
  }
  function wireSplit() {
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
    split.addEventListener("dblclick", () => setPanel(PANEL, true));
    addEventListener("resize", () => setPanel(settings.panel ?? PANEL));
    setPanel(settings.panel ?? PANEL);
  }

  // ---------- favorites ----------

  // favs from the site's saved list and this browser's pending requests (dropped once the list shows them).
  function refreshFavs() {
    const now = Date.now();
    favs.clear();
    for (const key of Object.keys(manifest.saved)) favs.add(key);
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
    setFavButtons(key);
    toast(t(save ? "favAdded" : "favRemoved"));
    if (mode === "favorites") render();
  }

  // Every heart for the listing (its card's, its tab's) says whether it's saved.
  function setFavButtons(key) {
    for (const b of document.querySelectorAll(`[data-fav="${CSS.escape(key)}"]`)) {
      b.setAttribute("aria-pressed", favs.has(key));
      b.querySelector("span")?.replaceChildren(t(favs.has(key) ? "saved" : "save"));
    }
  }

  let toastTimer = null;
  // action: [label, onclick] for a button in it (shown longer)
  function toast(text, action) {
    $("toast").textContent = text;
    if (action) {
      const b = Object.assign(document.createElement("button"), { className: "toast-btn", textContent: action[0] });
      b.addEventListener("click", () => { $("toast").classList.remove("on"); action[1](); });
      $("toast").append(b);
    }
    $("toast").classList.add("on");
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => $("toast").classList.remove("on"), action ? 12000 : 2200);
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
    $("filters-open").setAttribute("aria-expanded", WIDE.matches ? railOn() : open);
    for (const el of [document.querySelector(".bar"), $("panel")]) el.inert = open;
    $("map").inert = open || (!SPLIT.matches && $("panel").dataset.sheet === "full");
    const full = $("panel").dataset.sheet === "full";
    $("panel").style.setProperty("--peek", `${$("panel").querySelector(".panel-head").offsetHeight}px`);  // peek: its head
    $("sheet-handle").setAttribute("aria-expanded", full);
    $("sheet-handle").setAttribute("aria-label", t(full ? "sheetCollapse" : "sheetExpand"));
    // desktop: the rail can be hidden, giving its width to the results
    document.body.classList.toggle("rail-off", WIDE.matches && !railOn());
    setPanel(settings.panel ?? PANEL);
    $("filters-open").setAttribute("aria-label", t(WIDE.matches ? (railOn() ? "filtersHide" : "filtersShow") : "filtersOpen"));
    $("filters-close").setAttribute("aria-label", t(WIDE.matches ? "filtersHide" : "close"));
  }

  // section: scroll the filters to it (a chip's label edits its condition)
  function openFilters(open, section) {
    if (WIDE.matches && railOn() !== open) {
      settings.railOff = !open;
      saveSettings();
      sheetSemantics();
      if (!open) { $("filters-open").focus(); return; }
    }
    if (open && !WIDE.matches && !document.body.classList.contains("show-filters")) {
      history.pushState({ sheet: true }, "", location.href);  // browser back closes the sheet
    }
    if (!open && history.state?.sheet) { history.back(); return; }  // popstate closes it
    document.body.classList.toggle("show-filters", open && !WIDE.matches);
    sheetSemantics();
    if (open) {
      if (section) openFolds.add(section);
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
    wireSearch();
    wireSplit();
    wireSheet();
    $("sort").addEventListener("change", (e) => { q.sort = e.target.value; update(); });
    $("filters-open").addEventListener("click", () => openFilters(!railOn()));
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
    $("unplaced").addEventListener("toggle", renderUnplaced);
    $("filters-body").addEventListener("input", (e) => { if (e.target.id === "station-q") stationList(); });
    $("filters-body").addEventListener("toggle", (e) => {  // (toggle doesn't bubble: caught on the way down)
      const k = e.target.dataset?.fold;
      if (k) { if (e.target.open) openFolds.add(k); else openFolds.delete(k); }
    }, true);
    $("filters-body").addEventListener("keydown", (e) => {  // Enter in the station search takes the first match
      if (e.target.id === "station-q" && e.key === "Enter") $("station-list").querySelector('button[aria-pressed="false"]')?.click();
    });
    $("pref").addEventListener("click", () => openPrefs(true));
    $("prefs-close").addEventListener("click", () => openPrefs(false));
    $("prefs").addEventListener("click", onPrefsClick);
    $("prefs-q").addEventListener("input", renderPrefs);
    $("prefs-q").addEventListener("keydown", (e) => { if (e.key === "Enter") $("prefs-list").querySelector(".pref-go")?.click(); });
    $("settings-open").addEventListener("click", () => openSettings(true));
    $("settings-close").addEventListener("click", () => openSettings(false));
    $("settings").addEventListener("click", (e) => {
      if (e.target === $("settings")) return openSettings(false);
      const b = e.target.closest("[data-setting]");
      if (!b) return;
      settings[b.dataset.setting] = b.dataset.value;
      saveSettings();
      if (b.dataset.setting === "theme") applyTheme();
      else if (b.dataset.setting === "usd") { render(); rerenderTab(); }
      else { applyLanguage(); render(); rerenderTab(); }
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
    $("compare").addEventListener("click", () => compareFavs());
    $("favs").addEventListener("click", toggleFavorites);
    $("fit").addEventListener("click", () => { fitNext = "results"; placeMap(); });
    document.querySelector(".brand").addEventListener("click", () => { fitNext = "results"; });  // a new search: all of it
    document.addEventListener("click", (e) => {
      if (e.target === document.body && document.body.classList.contains("show-filters")) return openFilters(false);  // backdrop
      const fav = e.target.closest("[data-fav]");
      if (fav) { e.preventDefault(); toggleFav(fav.dataset.fav); return; }
      const link = e.target.closest("a[data-key]");
      if (link && !e.metaKey && !e.ctrlKey && !e.shiftKey) { e.preventDefault(); openTab(link.dataset.key); return; }
      const b = e.target.closest("button");
      if (!b) return;
      if (b.dataset.remove != null) {
        activeConditions()[+b.dataset.remove][1](q);
        update();
        ($("chips").querySelector(".chip-x") || $("count")).focus();
      }
      else if (b.dataset.clear) clearAll();
      else if (b.dataset.edit) openFilters(true, b.dataset.edit);
      else if (b.dataset.alt != null) {  // 目黒区 ⇄ 目黒駅
        const a = altSwaps[+b.dataset.alt];
        q[a.field] = q[a.field].filter((v) => v !== a.value);
        q[a.alt.field] = [...new Set([...q[a.alt.field], a.alt.value])];
        altSwaps[+b.dataset.alt] = { ...a.alt, alt: { field: a.field, value: a.value, label: a.label } };
        update(false);
      } else if (b.dataset.ignoredX) { ignoredNote = []; render(); }
      else if (b.dataset.prefOpen) openPrefs(true);
      else if (b.dataset.prefRemove) setPrefs(prefsFromHash().filter((s) => s !== b.dataset.prefRemove));
      else if (b.dataset.unsaveSearch != null) {
        searches.splice(+b.dataset.unsaveSearch, 1);
        saveSearches();
        toast(t("searchRemoved"));
        renderSearches();
      } else if (b.dataset.fit) { fitNext = "results"; placeMap(); }
      else if (b.dataset.onMap) {  // a listing's 地図で見る: the map goes there (phones: the sheet makes room)
        fitNext = { id: b.dataset.onMap };
        placeMap();
        if (!SPLIT.matches) setSheet("half", false);
      }
      else if (b.dataset.savedSearch != null) {  // open a saved search: what's new in it, if anything; then seen
        const s = searches[+b.dataset.savedSearch], query = queryFrom(new URLSearchParams(s.c));
        const fresh = s.seen && Filter.count(db, { ...query, since: s.seen });
        q = { ...query, sort: "new", since: fresh ? s.seen : null };
        s.seen = dataHour;
        saveSearches();
        fitNext = "results";
        update();
      } else if (b.dataset.quick) {
        const f = b.dataset.quick;
        q[f] = q[f] ? Filter.emptyQuery()[f] : f === "since" ? lastVisit : true;
        if (f === "dropsOnly") q.sort = q[f] ? "drop_desc" : "new";  // price cuts: biggest first
        else if (q[f]) q.sort = "new";                               // what's new: by day, newest first
        update();
      }
      else if (b.dataset.tabShow) openTab(b.dataset.tabShow);
      else if (b.dataset.tabClose) closeTab(b.dataset.tabClose);
      else if (b.dataset.stationPick) {  // from a station on the map
        q.stations = [b.dataset.stationPick];
        lastPlaces.push(b.dataset.stationPick);  // it's in sight already: the map stays
        map?.closePopup();
        update(false);
      }
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
      if (document.body.classList.contains("show-filters")) {  // back from the sheet: keep its changes
        history.replaceState(null, "", hashFor());
        openFilters(false);
        return;
      }
      if (!$("settings").hidden) { openSettings(false, true); return; }
      if (!$("prefs").hidden) {  // back from the picker: keep what it chose
        history.replaceState(null, "", prefsUrl);
        openPrefs(false, true);
        route();
        return;
      }
      const before = mode;
      if (tab === "list") listY[mode] = $("results").scrollTop;
      route();
      if (mode !== before && tab === "list") restoreList();
    });
    document.addEventListener("keydown", (e) => {
      const dialog = !$("settings").hidden ? $("settings") : !$("prefs").hidden ? $("prefs")
        : document.body.classList.contains("show-filters") ? $("filters") : null;
      if (e.key === "Tab" && dialog) trapFocus(e, dialog);
      if (e.key === "/" && !dialog && !/^(INPUT|SELECT|TEXTAREA)$/.test(document.activeElement.tagName)) { e.preventDefault(); $("q").focus(); }
      if (e.key !== "Escape" || /^(INPUT|SELECT|TEXTAREA)$/.test(e.target.tagName)) return;
      if (!$("settings").hidden) openSettings(false);
      else if (!$("prefs").hidden) openPrefs(false);
      else if (document.body.classList.contains("show-filters")) openFilters(false);
      else if (tab !== "list" && (e.target === document.body || $("panel").contains(e.target))) toList();
    });
    WIDE.addEventListener("change", () => { sheetSemantics(); render(); });
    new IntersectionObserver((es) => { if (es[0].isIntersecting) more(); }, { root: $("results"), rootMargin: "800px" }).observe($("sentinel"));
  }

  // The prefectures the URL asks for (p=tokyo,kanagawa), else the first one.
  function prefsFromHash() {
    const raw = new URLSearchParams(location.hash.slice(1)).get("p") || "";
    const p = raw === "all" ? allPrefs() : raw.split(",");
    const known = p.filter((s) => manifest.prefs.some(([slug]) => slug === s)).slice(0, maxPrefs());
    return known.length ? known : [manifest.prefs[0][0]];
  }

  async function loadPrefs(slugs) {
    const todo = slugs.filter((s) => !loaded.has(s));
    let done = 0;
    await Promise.all(todo.map(async (slug) => {
      const part = Filter.load(await (await fetch(`data/p/${slug}.json`)).json());
      if (todo.length > 1) $("count").textContent = t("loadingSome", ++done, todo.length);  // all of Japan: 47 files
      for (const it of part.items) {
        it.alias = I18N.en.places[it.areaName];  // "shibuya" finds 渋谷区
      }
      loaded.set(slug, part);
    }));
    allByKey = new Map([...loaded.values()].flatMap((d) => [...d.byKey]));
    offerNewer();
  }

  // The service worker (sw.js) shows a prefecture's index from this browser's cache, then fetches it again; when the
  // new copy differs it says so here, and the newer data can be loaded in place.
  function watchUpdates() {
    if (!("serviceWorker" in navigator)) return;
    navigator.serviceWorker.register("sw.js").catch(() => {});
    navigator.serviceWorker.addEventListener("message", (e) => {
      const slug = e.data?.type === "updated" && /\/data\/p\/([^/]+)\.json$/.exec(new URL(e.data.url).pathname)?.[1];
      if (slug) { newer.add(slug); offerNewer(); }
    });
  }

  const newer = new Set();  // prefectures with a newer index than the one shown (it can come before ours is loaded)
  function offerNewer() {
    const ready = [...newer].filter((s) => loaded.has(s));
    if (!ready.length) return;
    for (const s of ready) newer.delete(s);
    toast(t("dataUpdated"), [t("refresh"), () => ready.forEach(reloadPref)]);
  }

  async function reloadPref(slug) {
    loaded.delete(slug);
    await loadPrefs([slug]);
    choosePrefs(chosen);
    renderedFor = listSig = null;  // the same listings may have changed
    render();
  }

  function choosePrefs(slugs) {
    chosen = slugs;
    db = Filter.combine(slugs.map((s) => loaded.get(s)));
    areaNames = new Map(db.index.areas.map(([code, name]) => [code, name]));
    facetsFor = null;
    fitNext = "results";
    $("filters-body").innerHTML = "";  // the area lists change
  }

  function route() {
    const want = prefsFromHash(), missing = want.filter((s) => !loaded.has(s));
    if (missing.length) {  // load them, then come back
      $("count").textContent = t("loading");
      loadPrefs(missing).then(route, () => toast(t("offline")));
      return;
    }
    if (want.join() !== chosen.join()) choosePrefs(want);
    const before = mode, p = readHash(), key = p.get("id");
    modeChanged(before);
    if (conditionsHash() !== renderedFor) render(); else placeMap();
    if (key) { addTabs([key]); showTab(key); }  // a listing: its tab (opened when it isn't, as from a link)
    else if (p.get("cmp") === "1" && mode === "favorites") compareFavs(false);
    else if (tab !== "list") showTab("list");
  }

  async function start() {
    history.scrollRestoration = "manual";  // the list keeps its place itself (per tab and view)
    watchUpdates();
    mapReady();  // the map loads while the listings do
    setTimeout(() => { if (waiting) { waiting = false; if (renderedFor) listResults(); } }, MAP_WAIT);  // or list them all
    applyTheme();
    applyLanguage();
    $("list").innerHTML = '<div class="skeleton"></div>'.repeat(6);
    try {
      manifest = await (await fetch("data/index.json")).json();
      if (!location.hash && localStorage.getItem("suumo.last")) history.replaceState(null, "", localStorage.getItem("suumo.last"));
      await loadPrefs(prefsFromHash());
      choosePrefs(prefsFromHash());
      // where the map opens: the link's place (old links: the area they showed), its listing, else the results
      const p = new URLSearchParams(location.hash.slice(1)), m = p.get("m"), b = (p.get("b") || "").split(",").map(Number);
      if (/^-?[\d.]+,-?[\d.]+,\d+$/.test(m || "")) fitNext = { m: (mapAt = m) };
      else if (p.get("view") === "area" && b.length === 4 && b.every(Number.isFinite)) fitNext = { b };
      else if (p.get("id")) fitNext = { id: p.get("id") };
      refreshFavs();
      for (const s of searches) s.c = searchKey(rawQuery(new URLSearchParams(s.c)));  // in today's terms
      // a visit = a new data version: the previous one is what "since your last visit" compares with
      const visit = stored("suumo.visit", {}), now = +String(manifest.updated).replace(/\D/g, "").slice(0, 10) || null;
      if (now && visit.cur !== now) { visit.prev = visit.cur ?? null; visit.cur = now; localStorage.setItem("suumo.visit", JSON.stringify(visit)); }
      lastVisit = visit.prev ?? null;
      dataHour = visit.cur ?? null;
    } catch {
      const [title, hint] = t("loadFailed");
      $("list").innerHTML = `<div class="empty"><h3>${esc(title)}</h3><p>${esc(hint)}</p></div>`;
      return;
    }
    const early = $("q").value.trim();  // typed while the listings were loading
    wire();
    route();
    history.replaceState(history.state, "", hashFor());  // (old links: in today's terms)
    saveLast();
    if (early && !q.text) applyText(early);
  }

  start();
})();
