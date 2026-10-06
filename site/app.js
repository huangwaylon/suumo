// The search page. Conditions live in the URL (shareable); results show as a list, and as a map on wide screens
// or on request; a listing opens over the page. The search rules are in filter.js, the strings in i18n.js.
(() => {
  "use strict";
  const $ = (id) => document.getElementById(id);
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => `&#${c.charCodeAt(0)};`);
  const icon = (name) => `<svg class="i"><use href="#i-${name}"/></svg>`;

  // ---------- settings: language and theme ----------

  const settings = { lang: "ja", theme: "auto", ...JSON.parse(localStorage.getItem("suumo.settings") || "{}") };
  let S = I18N[settings.lang] || I18N.ja;
  const lookup = (strings, key) => key.split(".").reduce((o, k) => o?.[k], strings);
  const t = (key, ...args) => { const v = lookup(S, key) ?? lookup(I18N.ja, key); return typeof v === "function" ? v(...args) : v; };
  const value = (v) => (v == null ? v : S.values?.[v] ?? v);  // fixed SUUMO values (land rights, deal type...) in English
  const DARK = matchMedia("(prefers-color-scheme: dark)");

  function applySettings() {
    S = I18N[settings.lang] || I18N.ja;
    const dark = settings.theme === "dark" || (settings.theme === "auto" && DARK.matches);
    document.documentElement.dataset.theme = dark ? "dark" : "light";
    document.documentElement.lang = S.lang;
    document.querySelector('meta[name="theme-color"]').content = dark ? "#171a21" : "#ffffff";
    for (const el of document.querySelectorAll("[data-t]")) el.textContent = t(el.dataset.t);
    for (const el of document.querySelectorAll("[data-t-label]")) el.setAttribute("aria-label", t(el.dataset.tLabel));
    for (const el of document.querySelectorAll("[data-t-placeholder]")) el.placeholder = t(el.dataset.tPlaceholder);
    $("sort").innerHTML = Object.entries(t("sorts")).map(([k, v]) => `<option value="${k}">${esc(v)}</option>`).join("");
    $("filters-body").innerHTML = "";  // rebuilt in the new language
    localStorage.setItem("suumo.settings", JSON.stringify(settings));
  }

  function renderSettings() {
    const group = (name, options) => `<div class="seg" role="group">${Object.entries(options).map(([k, label]) =>
      `<button class="seg-btn" data-setting="${name}" data-value="${k}" aria-pressed="${settings[name] === k}">${esc(label)}</button>`).join("")}</div>`;
    $("settings-body").innerHTML = `<section class="sec"><h3>${esc(t("language"))}</h3>${group("lang", { ja: "日本語", en: "English" })}</section>
      <section class="sec"><h3>${esc(t("theme"))}</h3>${group("theme", t("themes"))}</section>`;
  }

  // ---------- choices ----------

  const PRICES = [1000, 2000, 3000, 4000, 5000, 6000, 7000, 8000, 10000, 12000, 15000, 20000, 30000].map((m) => m * 1e4);
  const PLANS = [[3, "1K"], [5, "1LDK"], [7, "2DK"], [8, "2LDK"], [10, "3DK"], [11, "3LDK"], [14, "4LDK"]];
  const SIZES = [40, 50, 60, 70, 80, 100, 120];
  const LANDS = [50, 80, 100, 120, 150, 200];
  const AGES = [0, 5, 10, 20, 30];
  const WALKS = [5, 7, 10, 15, 20];
  const FLAGS = ["freehold", "noCondition", "newOnly", "dropsOnly", "post1981"];
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
  let mode = "search";          // search | favorites | shared (a list opened from a link)
  let shared = [];
  let hits = [], shown = 0;
  let showMap = false;
  let openKey = null, pushed = false, lastFocus = null;
  let renderedFor = null;       // the URL (without the open listing) the results were drawn for
  let facets = null, facetsFor = null;
  let listStale = false;        // the phone filter sheet covers the list: it's redrawn when the sheet closes
  let areaNames = new Map();
  const favs = new Set(JSON.parse(localStorage.getItem("suumo.favorites") || "[]"));

  // ---------- formatting ----------

  const num = (n) => n.toLocaleString(S.lang === "en" ? "en-US" : "ja-JP");
  function money(yen, html = false) {
    if (yen == null) return t("undecided");
    if (S.lang === "en") return yen >= 1e8 ? `¥${+(yen / 1e8).toFixed(2)}B` : `¥${+(yen / 1e6).toFixed(1)}M`;
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
  function sizes(it) {
    if (isCondo(it.type)) return [m2(it.size)];
    if (it.type === "land") return [`${t("land")} ${m2(it.land)}`];
    return [it.land && `${t("land")} ${m2(it.land)}`, it.size && `${t("building")} ${m2(it.size)}`].filter(Boolean);
  }
  function station(it) {
    let best = null;
    for (const [s, w] of it.stations) if (w != null && (!best || w < best[1])) best = [s, w];
    if (best) return t("walk", best[0], best[1]);
    return it.stations.length ? t("bus", it.stations[0][0]) : "";
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
  const areaName = (code) => areaNames.get(code) || code;
  const planLabel = (v) => PLANS.find(([p]) => p === v)?.[1] ?? "";

  // ---------- URL <-> state ----------

  const KEYS = { q: "text", t: "types", a: "areas", st: "stations", f: "features", w: "walk", pmin: "priceMin",
    pmax: "priceMax", plan: "plan", s: "sizeMin", lm: "landMin", g: "ageMax", quake: "post1981", fh: "freehold",
    nc: "noCondition", new: "newOnly", drop: "dropsOnly", sort: "sort" };

  function readHash() {
    const p = new URLSearchParams(location.hash.slice(1));
    const base = Filter.emptyQuery();
    q = Filter.emptyQuery();
    for (const [k, f] of Object.entries(KEYS)) {
      if (!p.has(k)) continue;
      const v = p.get(k), d = base[f];
      if (Array.isArray(d)) q[f] = v ? v.split(",") : [];
      else if (typeof d === "boolean") q[f] = v === "1";
      else if (d === null) q[f] = Number.isFinite(+v) && v !== "" ? +v : null;
      else q[f] = v;
    }
    if (!t("sorts")[q.sort]) q.sort = "new";
    shared = p.get("ids") ? p.get("ids").split(",") : [];
    mode = shared.length ? "shared" : p.get("fav") === "1" ? "favorites" : "search";
    showMap = p.get("view") === "map";
    return p.get("id");
  }

  function hashFor(extra = {}) {
    const p = new URLSearchParams(), base = Filter.emptyQuery();
    for (const [k, f] of Object.entries(KEYS)) {
      const v = q[f];
      if (JSON.stringify(v) === JSON.stringify(base[f])) continue;
      p.set(k, Array.isArray(v) ? v.join(",") : typeof v === "boolean" ? "1" : v);
    }
    if (mode === "favorites") p.set("fav", "1");
    if (mode === "shared") p.set("ids", shared.join(","));
    if (showMap) p.set("view", "map");
    for (const [k, v] of Object.entries(extra)) p.set(k, v);
    return "#" + p.toString();
  }

  // ---------- results ----------

  function compute() {
    if (mode === "search") return Filter.search(db, q);
    const keys = mode === "favorites" ? favs : new Set(shared);
    return Filter.search(db, { ...Filter.emptyQuery(), sort: q.sort }).filter((it) => keys.has(it.key));
  }

  function update(top = true) {
    history.replaceState(null, "", hashFor());
    render();
    if (top && !WIDE.matches) window.scrollTo({ top: 0 });
    else if (top) document.querySelector(".results").scrollIntoView({ block: "start" });
  }

  function render() {
    renderedFor = hashFor();
    if (document.body.classList.contains("show-filters") && !WIDE.matches) {
      listStale = true;  // only the sheet is visible: its counts are enough until it closes
      renderFilters();
      return;
    }
    listStale = false;
    hits = compute();
    document.body.classList.toggle("show-map", showMap && !WIDE.matches);
    $("q").value = q.text;
    $("sort").value = q.sort;
    $("count").textContent = t("count", num(hits.length));
    const active = activeConditions();
    $("filters-n").hidden = !active.length;
    $("filters-n").textContent = active.length;
    $("favs-n").hidden = !favs.size;
    $("favs-n").textContent = favs.size;
    $("favs").setAttribute("aria-pressed", mode === "favorites");
    $("chips").innerHTML = mode === "search" ? active.map(([label], i) =>
      `<button class="chip" data-remove="${i}" aria-label="${esc(t("remove", label))}">${esc(label)}${icon("x")}</button>`).join("") : "";
    renderBanner();
    $("view").innerHTML = showMap ? `${icon("list")}<span>${t("list")}</span>` : `${icon("map")}<span>${t("map")}</span>`;
    $("list").innerHTML = hits.length ? "" : emptyState(active);
    shown = 0;
    more();
    if (mapVisible()) drawMap();
    if (filtersVisible()) renderFilters();
  }

  function more() {
    if (shown >= hits.length) return;
    $("list").insertAdjacentHTML("beforeend", hits.slice(shown, shown + PAGE).map(card).join(""));
    shown += PAGE;
  }

  function tags(it) {
    const F = db.flags, out = [];
    if (it.flags & F.new) out.push(`<span class="tag new">${t("tag.new")}</span>`);
    if (it.flags & F.dropped) out.push(`<span class="tag drop">${t("tag.dropped")}</span>`);
    if (it.flags & F.leasehold) out.push(`<span class="tag warn">${t("tag.leasehold")}</span>`);
    if (it.flags & F.conditional) out.push(`<span class="tag warn">${t("tag.conditional")}</span>`);
    if (it.others) out.push(`<span class="tag">${t("others", it.others)}</span>`);
    return out.join("");
  }

  function card(it) {
    const spec = [it.layout, ...sizes(it), age(it)].filter(Boolean).map((s) => `<span>${esc(s)}</span>`).join("");
    return `<article class="card"><a href="${esc(hashFor({ id: it.key }))}" data-key="${esc(it.key)}">
      <div class="ph">${it.image ? `<img src="${esc(image(it, 360, 270))}" alt="" loading="lazy" decoding="async" onerror="this.remove()">` : ""}</div>
      <div class="body">
        <div class="price num">${price(it, true)}</div>
        <div class="spec">${spec}</div>
        <div class="meta">${esc(station(it))}</div>
        <div class="meta">${esc([it.town, it.name || typeName(it.type)].filter(Boolean).join(" · "))}</div>
        <div class="tags">${tags(it)}</div>
      </div></a>
      <button class="fav" data-fav="${esc(it.key)}" aria-pressed="${favs.has(it.key)}" aria-label="${esc(t("favorites"))}">${icon("heart")}</button></article>`;
  }

  function emptyState(active) {
    if (mode !== "search") {
      const [title, hint] = t(mode === "favorites" ? "favEmpty" : "sharedEmpty");
      return `<div class="empty"><h3>${esc(title)}</h3><p>${esc(hint)}</p></div>`;
    }
    const loosen = active.map(([label, remove], i) => {
      const saved = q;
      q = JSON.parse(JSON.stringify(q));
      remove();
      const n = Filter.count(db, q);
      q = saved;
      return n ? `<button class="chip" data-remove="${i}">${esc(t("removeN", label))}<span class="n">${t("count", num(n))}</span></button>` : "";
    }).join("");
    return `<div class="empty"><h3>${esc(t("emptyTitle"))}</h3><p>${esc(t("emptyHint"))}</p>
      <div class="chips">${loosen}</div>${active.length ? `<button class="btn" data-clear="1">${esc(t("clearAll"))}</button>` : ""}</div>`;
  }

  function renderBanner() {
    const b = $("banner");
    b.hidden = mode === "search";
    if (mode === "favorites") {
      b.innerHTML = `<span>${esc(t("favBanner", favs.size))}</span><span>${favs.size ? `<button class="btn" data-share="1">${esc(t("share"))}</button>` : ""}
        <button class="btn ghost" data-search="1">${esc(t("backToSearch"))}</button></span>`;
    } else if (mode === "shared") {
      b.innerHTML = `<span>${esc(t("sharedBanner", shared.length))}</span><span><button class="btn" data-import="1">${esc(t("importFavs"))}</button>
        <button class="btn ghost" data-search="1">${esc(t("backToSearch"))}</button></span>`;
    }
  }

  // The conditions in force, as [label, remove()] for the chips and the empty state.
  function activeConditions() {
    const out = [], drop = (f, v) => () => { q[f] = q[f].filter((x) => x !== v); };
    const reset = (...fs) => () => { const e = Filter.emptyQuery(); for (const f of fs) q[f] = e[f]; };
    if (q.text) out.push([t("chipText", q.text), reset("text")]);
    for (const type of q.types) out.push([typeName(type), drop("types", type)]);
    if (q.priceMin != null || q.priceMax != null) {
      out.push([t("chipPrice", q.priceMin != null ? money(q.priceMin) : "", q.priceMax != null ? money(q.priceMax) : ""),
        reset("priceMin", "priceMax")]);
    }
    if (q.plan != null) out.push([t("chipPlan", planLabel(q.plan)), reset("plan")]);
    if (q.sizeMin != null) out.push([t("sizeMin", q.sizeMin), reset("sizeMin")]);
    if (q.landMin != null) out.push([t("chipLand", q.landMin), reset("landMin")]);
    if (q.ageMax != null) out.push([t("ages")[q.ageMax] ?? t("ageYears", q.ageMax), reset("ageMax")]);
    for (const a of q.areas) out.push([areaName(a), drop("areas", a)]);
    for (const s of q.stations) out.push([t("station", s), drop("stations", s)]);
    if (q.walk != null) out.push([t("walkWithin", q.walk), reset("walk")]);
    for (const f of FLAGS) if (q[f]) out.push([t("flags")[f], reset(f)]);
    for (const f of q.features) out.push([f, drop("features", f)]);
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
      `<option value="${v}"${q[field] === v ? " selected" : ""}>${esc(fmt(v))}（${num(facets[field][v] || 0)}）</option>`).join("")}</select></label>`;
  }

  function renderFilters() {
    const key = JSON.stringify({ ...q, sort: "" });  // the sort doesn't change any count
    if (key !== facetsFor) { facets = Filter.facets(db, q, CHOICES); facetsFor = key; }
    const body = $("filters-body");
    if (!body.firstChild) {  // sections are built once; their contents are redrawn (the station search keeps focus)
      body.innerHTML = ["text", "types", "areas", "price", "plan", "size", "age", "stations", "extra"].map((s) =>
        `<section class="sec" data-sec="${s}"></section>`).join("");
      body.querySelector('[data-sec="stations"]').innerHTML = `<h3>${esc(t("sec.stations"))}</h3>
        <input class="text-input" id="station-q" type="search" placeholder="${esc(t("stationSearch"))}" autocomplete="off">
        <div class="opts" id="station-list"></div><div class="opts" id="walk-list" style="margin-top:12px"></div>`;
    }
    const sec = (s, html) => { body.querySelector(`[data-sec="${s}"]`).innerHTML = html; };
    const h3 = (k, hint) => `<h3>${esc(t(`sec.${k}`))}${hint ? `<span class="hint">${esc(hint)}</span>` : ""}</h3>`;
    const isLand = q.types.length && q.types.every((type) => type === "land");
    sec("text", q.text ? `<div class="opts"><button class="chip" aria-pressed="true" data-cleartext="1">${esc(t("textFilter", q.text))}${icon("x")}</button></div>` : "");
    sec("types", `${h3("types")}<div class="opts">${db.index.types.map((type) => many("types", type, typeName(type), facets.types[type] || 0)).join("")}</div>`);
    sec("areas", `${h3("areas")}${areaGroups()}`);
    sec("price", `${h3("price")}${select("priceMin", t("min"), PRICES, (v) => t("priceMin", money(v)))}${
      select("priceMax", t("max"), PRICES, (v) => t("priceMax", money(v)))}`);
    sec("plan", isLand ? "" : `${h3("plan", t("sec.planHint"))}<div class="opts">${PLANS.map(([v, l]) =>
      one("plan", v, t("planFrom", l), facets.plan[v] || 0)).join("")}</div>`);
    sec("size", `${h3("size")}${isLand ? "" : select("sizeMin", t("floorArea"), SIZES, (v) => t("sizeMin", v))}${
      q.types.length && q.types.every(isCondo) ? "" : select("landMin", t("landArea"), LANDS, (v) => t("landMin", v, Math.round(v / TSUBO)))}`);
    sec("age", isLand ? "" : `${h3("age")}<div class="opts">${AGES.map((v) => one("ageMax", v, t("ages")[v], facets.ageMax[v] || 0)).join("")}${
      flag("post1981", facets.post1981)}</div>`);
    stationList();
    $("walk-list").innerHTML = WALKS.map((w) => one("walk", w, t("walkWithin", w), facets.walk[w] || 0)).join("");
    const tagNames = Object.keys(facets.features).sort((a, b) => facets.features[b] - facets.features[a]);
    const shownTags = [...q.features, ...tagNames.filter((f) => !q.features.includes(f)).slice(0, 30)];
    sec("extra", `${h3("extra")}<div class="opts">${FLAGS.filter((f) => f !== "post1981" && (facets[f] || q[f])).map((f) =>
      flag(f, facets[f])).join("")}</div><div class="opts" style="margin-top:10px">${shownTags.map((f) =>
      many("features", f, f.normalize("NFKC"), facets.features[f] || 0)).join("")}</div>`);
    $("apply").textContent = t("show", num(facets.total));
  }

  function stationList() {
    const raw = $("station-q").value.trim(), term = Filter.normalize(raw), counts = facets.stations;
    let names = term ? db.index.stations.filter((s) => Filter.normalize(s).includes(term)) : Object.keys(counts);
    names = names.filter((s) => !q.stations.includes(s)).sort((a, b) => (counts[b] || 0) - (counts[a] || 0)).slice(0, 24);
    $("station-list").innerHTML = [...q.stations, ...names].map((s) => many("stations", s, t("station", s), counts[s] || 0)).join("")
      || `<span class="meta">${esc(t(/^[ぁ-ゖァ-ヺー]+$/.test(raw) ? "stationKanji" : "noStation"))}</span>`;
  }

  function areaGroups() {
    const prefs = new Set(db.index.areas.map((a) => a[2])), kinds = t("kinds");
    const groups = new Map();
    for (const [code, name, pref] of [...db.index.areas].sort((a, b) => a[0].localeCompare(b[0]))) {
      const g = `${prefs.size > 1 ? db.index.prefs[pref] + " " : ""}${kinds[code[2]] || kinds[3]}`;
      if (!groups.has(g)) groups.set(g, []);
      groups.get(g).push([code, many("areas", code, name, facets.areas[code] || 0)]);
    }
    return [...groups].map(([g, chips], i) => {
      const open = i === 0 || chips.some(([code]) => q.areas.includes(code));
      return `<details class="group"${open ? " open" : ""}><summary>${esc(g)}</summary><div class="opts">${chips.map(([, c]) => c).join("")}</div></details>`;
    }).join("");
  }

  function onFilterClick(e) {
    const b = e.target.closest("button[data-many], button[data-one], button[data-flag], button[data-cleartext]");
    if (!b) return;
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
  const mapVisible = () => WIDE.matches || showMap;

  function loadLeaflet() {
    leaflet ||= Promise.all(LEAFLET.map(([kind, url, integrity]) => new Promise((ok, fail) => {
      const el = document.createElement(kind === "css" ? "link" : "script");
      Object.assign(el, kind === "css" ? { rel: "stylesheet", href: url } : { src: url, async: false });
      Object.assign(el, { integrity, crossOrigin: "", onload: ok, onerror: fail });
      document.head.append(el);
    })));
    return leaflet;
  }

  const pins = new Map();  // one icon per count, reused across redraws
  function pin(n) {
    if (!pins.has(n)) {
      const label = num(n);
      pins.set(n, L.divIcon({ className: "", html: `<div class="pin${n >= 100 ? " big" : ""}">${label}</div>`,
        iconSize: [Math.max(30, 16 + label.length * 8), 26] }));
    }
    return pins.get(n);
  }

  async function drawMap() {
    await loadLeaflet();
    if (!map) {
      map = L.map("map", { zoomControl: false }).setView([35.68, 139.7], 11);
      if (!COARSE.matches) L.control.zoom({ position: "topright" }).addTo(map);
      L.tileLayer(TILES, { maxZoom: 18, attribution: '<a href="https://maps.gsi.go.jp/development/ichiran.html" target="_blank">地理院タイル</a>' }).addTo(map);
      cluster = L.markerClusterGroup({ maxClusterRadius: 48, showCoverageOnHover: false, chunkedLoading: true,
        iconCreateFunction: (c) => pin(c.getAllChildMarkers().reduce((n, m) => n + m.options.count, 0)) });
      map.addLayer(cluster);
    }
    map.invalidateSize();
    const towns = new Map();
    let unplaced = 0;
    for (const it of hits) {
      if (it.lat == null) { unplaced++; continue; }
      if (!towns.has(it.town)) towns.set(it.town, [it.lat, it.lng, []]);
      towns.get(it.town)[2].push(it);
    }
    cluster.clearLayers();
    cluster.addLayers([...towns].map(([town, [lat, lng, list]]) =>
      L.marker([lat, lng], { icon: pin(list.length), count: list.length }).bindPopup(() => popup(town, list))));
    let note = $("map").querySelector(".map-note");
    if (!note) { note = document.createElement("div"); note.className = "map-note"; $("map").append(note); }
    note.textContent = hits.length ? t("mapCount", num(hits.length), unplaced && num(unplaced)) : t("mapEmpty");
    if (fittedFor !== renderedFor && towns.size) {  // follow the results when the conditions change
      fittedFor = renderedFor;
      map.fitBounds(coreBounds([...towns.values()]), { padding: [30, 30], maxZoom: 15 });
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
        <span><b class="num">${money(it.price)}</b><br>${esc([it.layout || typeName(it.type), age(it)].filter(Boolean).join(" · "))}<br>${esc(station(it))}</span></a></li>`).join("")}</ul>`;
  }

  // ---------- listing ----------

  function openDetail(key) {
    lastFocus = document.activeElement;
    history.pushState(null, "", hashFor({ id: key }));
    pushed = true;
    showDetail(key);
  }

  let detailMap = null;
  async function showDetail(key) {
    openKey = key;
    const it = db.byKey.get(key);
    $("detail").hidden = false;
    document.body.style.overflow = "hidden";
    $("detail-back").focus();
    setFavButton();
    const body = $("detail-body");
    body.innerHTML = '<div class="d-head"><div class="skeleton"></div></div>';
    let r;
    try {
      const res = await fetch(`data/l/${key.replace(":", "/")}.json`);
      if (!res.ok) throw new Error(res.status);
      r = await res.json();
    } catch {
      body.innerHTML = `<div class="empty"><h3>${esc(t("gone"))}</h3></div>`;
      return;
    }
    if (openKey !== key) return;
    $("detail-link").href = r.url;
    body.innerHTML = detailHtml(r, it);
    if (it?.lat != null) {
      await loadLeaflet();
      if (openKey !== key) return;
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
    const yen = (v) => (v ? t("yen", num(v)) : null);
    const txt = (s) => (s == null || s === "" ? null : esc(value(s)));
    const unit = it?.unit ? t("perM2", it.unit) + (isCondo(type) ? "" : `（${t("perTsubo", Math.round(it.unit * TSUBO))}）`) : "";
    const facts = [["layout", r.layout], [isCondo(type) ? "floor" : "building", m2(r.floor_m2 || r.building_m2)],
      [isCondo(type) ? "balcony" : "land", isCondo(type) ? m2(r.balcony_m2) : m2(r.land_m2) && `${m2(r.land_m2)}・${tsubo(r.land_m2)}`],
      ["age", it ? age(it) : ""], ["station", it ? station(it) : ""], ["unit", unit]].filter(([, v]) => v);
    const history = r.history?.length ? r.history.map(([d, o, n]) => esc(`${day(d)} ${money(o)} → ${money(n)}`)).join("<br>")
      : esc(t("unchanged", day(r.new_date || r.first_seen)));
    const parking = r.parking && [value(r.parking.status), r.parking.fee_min && t("perMonth", t("yen", num(r.parking.fee_min)))].filter(Boolean).join(" ");
    const road = r.road && ([value(r.road.dir), r.road.width_m && t("width", r.road.width_m)].filter(Boolean).join(" ") || r.road.text);
    const access = (r.stations || []).map((s) => esc([s.line, t("station", s.name), s.bus ? [t("busMin", s.bus), s.walk != null && t("stopWalk", s.walk)].filter(Boolean).join(" ")
      : s.walk != null ? t("walkMin", s.walk) : ""].filter(Boolean).join(" "))).join("<br>");
    return `
      ${r.image ? `<img class="hero" src="${esc(r.image.replace(/w=\d+&h=\d+/, "w=640&h=480"))}" alt="">` : ""}
      <div class="d-head">
        <h1 id="detail-title">${esc(r.name || [r.layout, typeName(type)].filter(Boolean).join(" "))}</h1>
        <div class="meta">${esc([r.address, typeName(type)].filter(Boolean).join(" · "))}</div>
        <div class="d-price num">${it ? price(it, true) : money(r.price, true)}${r.price_excludes_building ? `<small class="note">${esc(t("excludesBuilding"))}</small>` : ""}</div>
        <div class="tags">${it ? tags(it) : ""}</div>
      </div>
      <div class="facts">${facts.map(([k, v]) => `<div class="fact"><span>${esc(L_(k))}</span><b>${esc(v)}</b></div>`).join("")}</div>
      ${sec("history", [["price", history]])}
      ${sec("access", [["nearest", access]])}
      ${sec("costs", [["mgmt", yen(r.mgmt_fee) && t("perMonth", yen(r.mgmt_fee)) + (r.mgmt_form ? `（${esc(r.mgmt_form)}）` : "")],
        ["repair", yen(r.repair_fee) && t("perMonth", yen(r.repair_fee))], ["repairOnce", yen(r.repair_fund_once)],
        ["otherFees", txt(r.other_fees)], ["parking", txt(parking)]])}
      ${sec("bldg", [["built", r.built && t("month", +r.built.slice(0, 4), +r.built.slice(5, 7)) + (r.built_planned ? t("planned") : "")],
        ["structure", txt(r.structure)], ["floors", txt([r.floor != null && t("floorN", r.floor), r.floors_above && t("storeys", r.floors_above)].filter(Boolean).join(" / "))],
        ["units", isCondo(type) && r.total_units ? t("unitsN", num(r.total_units)) : null], ["direction", txt(r.direction)],
        ["reform", txt(r.reform?.text || r.reform?.date)], ["builder", txt(r.builder)], ["handover", txt(r.handover)]])}
      ${sec("legal", [["rights", r.land_rights && esc(value(r.land_rights)) + (r.land_rights_note ? `（${esc(r.land_rights_note)}）` : "")],
        ["zoning", txt(r.zoning)], ["ratios", r.coverage_pct || r.far_pct ? `${r.coverage_pct ?? "-"}% / ${r.far_pct ?? "-"}%` : null],
        ["road", txt(road)], ["category", txt(r.land_category)], ["landStatus", txt(r.land_status)], ["condition", r.build_condition ? t("yes") : null],
        ["utilities", txt(r.utilities)], ["restrictions", txt(r.restrictions)]])}
      ${r.features?.length ? `<section class="d-sec"><h2>${esc(L_("features"))}</h2><div class="opts">${r.features.map((f) => `<span class="tag">${esc(f.normalize("NFKC"))}</span>`).join("")}</div></section>` : ""}
      ${sec("listing", [["agent", txt(r.agent)], ["deal", txt(r.deal_type)], ["otherListings", (r.others || []).map((o) =>
        `<a href="${esc(o.url)}" target="_blank" rel="noopener">${esc(o.agent || "SUUMO")}</a>`).join("<br>") || null],
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

  function saveFavs() {
    localStorage.setItem("suumo.favorites", JSON.stringify([...favs]));
    $("favs-n").hidden = !favs.size;
    $("favs-n").textContent = favs.size;
  }

  function toggleFav(key) {
    if (favs.has(key)) favs.delete(key); else favs.add(key);
    saveFavs();
    document.querySelectorAll(`.fav[data-fav="${CSS.escape(key)}"]`).forEach((b) => b.setAttribute("aria-pressed", favs.has(key)));
    toast(t(favs.has(key) ? "favAdded" : "favRemoved"));
  }

  function setFavButton() {
    $("detail-fav").setAttribute("aria-pressed", favs.has(openKey));
    $("detail-fav").querySelector("span").textContent = t(favs.has(openKey) ? "saved" : "favorites");
  }

  let toastTimer = null;
  function toast(text) {
    $("toast").textContent = text;
    $("toast").hidden = false;
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => { $("toast").hidden = true; }, 2200);
  }

  // ---------- events ----------

  function openFilters(open) {
    document.body.classList.toggle("show-filters", open);
    if (open) { renderFilters(); $("filters-close").focus(); return; }
    if (listStale) render();
    $("filters-open").focus();
  }

  function openSettings(open) {
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
    $("q").addEventListener("keydown", (e) => { if (e.key === "Enter" && COARSE.matches) e.target.blur(); });  // hide the keyboard
    $("sort").addEventListener("change", (e) => { q.sort = e.target.value; update(); });
    $("filters-open").addEventListener("click", () => openFilters(true));
    $("filters-close").addEventListener("click", () => openFilters(false));
    $("apply").addEventListener("click", () => openFilters(false));
    $("clear").addEventListener("click", () => { q = { ...Filter.emptyQuery(), sort: q.sort }; update(false); });
    $("filters-body").addEventListener("click", onFilterClick);
    $("filters-body").addEventListener("change", (e) => {
      const f = e.target.dataset.num;
      if (f) { q[f] = e.target.value === "" ? null : +e.target.value; update(false); }
    });
    $("filters-body").addEventListener("input", (e) => { if (e.target.id === "station-q") stationList(); });
    $("settings-open").addEventListener("click", () => openSettings(true));
    $("settings-close").addEventListener("click", () => openSettings(false));
    $("settings").addEventListener("click", (e) => {
      if (e.target === $("settings")) return openSettings(false);
      const b = e.target.closest("[data-setting]");
      if (!b) return;
      settings[b.dataset.setting] = b.dataset.value;
      applySettings();
      renderSettings();
      render();
      if (openKey) showDetail(openKey);
      $("settings-body").querySelector(`[data-setting="${b.dataset.setting}"][data-value="${b.dataset.value}"]`)?.focus();
    });
    DARK.addEventListener("change", () => { if (settings.theme === "auto") applySettings(); });
    $("favs").addEventListener("click", () => { mode = mode === "favorites" ? "search" : "favorites"; update(); });
    $("view").addEventListener("click", () => { showMap = !showMap; update(); });
    $("detail-back").addEventListener("click", closeDetail);
    $("detail").addEventListener("click", (e) => { if (e.target === $("detail")) closeDetail(); });
    $("detail-fav").addEventListener("click", () => { toggleFav(openKey); setFavButton(); if (mode === "favorites") render(); });
    document.addEventListener("click", (e) => {
      const fav = e.target.closest("[data-fav]");
      if (fav) { e.preventDefault(); toggleFav(fav.dataset.fav); if (mode === "favorites") render(); return; }
      const link = e.target.closest("a[data-key]");
      if (link && !e.metaKey && !e.ctrlKey && !e.shiftKey) { e.preventDefault(); openDetail(link.dataset.key); return; }
      const b = e.target.closest("button");
      if (!b) return;
      if (b.dataset.remove != null) { activeConditions()[+b.dataset.remove][1](); update(); }
      else if (b.dataset.clear) { q = { ...Filter.emptyQuery(), sort: q.sort }; update(); }
      else if (b.dataset.share) {
        const url = `${location.origin}${location.pathname}#ids=${[...favs].join(",")}`;
        if (navigator.share && COARSE.matches) navigator.share({ title: t("shareTitle", favs.size), url }).catch(() => {});
        else navigator.clipboard?.writeText(url).then(() => toast(t("copied")), () => prompt(t("sharePrompt"), url));
      } else if (b.dataset.import) {
        shared.forEach((k) => favs.add(k));
        saveFavs();
        toast(t("imported", shared.length));
        mode = "favorites"; shared = []; update();
      } else if (b.dataset.search) { mode = "search"; shared = []; update(); }
    });
    window.addEventListener("popstate", route);
    document.addEventListener("keydown", (e) => {
      const dialog = !$("detail").hidden ? $("detail") : !$("settings").hidden ? $("settings") : null;
      if (e.key === "Tab" && dialog) trapFocus(e, dialog);
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
    if (key && db.byKey.has(key)) showDetail(key);
    else if (!$("detail").hidden) { pushed = false; hideDetail(); }
  }

  async function start() {
    applySettings();
    $("list").innerHTML = '<div class="skeleton"></div>'.repeat(6);
    try {
      const index = await (await fetch("data/index.json")).json();
      db = Filter.load(index);
      areaNames = new Map(index.areas.map(([code, name]) => [code, name]));
    } catch {
      const [title, hint] = t("loadFailed");
      $("list").innerHTML = `<div class="empty"><h3>${esc(title)}</h3><p>${esc(hint)}</p></div>`;
      return;
    }
    wire();
    route();
    (window.requestIdleCallback || setTimeout)(() => loadLeaflet());  // so the first map opens without waiting
  }

  start();
})();
