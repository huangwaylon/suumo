// The search page. Conditions live in the URL (shareable); results show as a list, and as a map on wide screens
// or on request; a listing opens over the page. The search rules are in filter.js.
(() => {
  "use strict";
  const $ = (id) => document.getElementById(id);
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => `&#${c.charCodeAt(0)};`);
  const icon = (name) => `<svg class="i"><use href="#i-${name}"/></svg>`;

  // ---------- choices ----------

  const TYPES = { used_condo: "中古マンション", new_condo: "新築マンション", used_house: "中古一戸建て",
    new_house: "新築一戸建て", land: "土地" };
  const SORTS = { new: "新着順", price_asc: "価格が安い順", price_desc: "価格が高い順", size_desc: "広い順",
    walk_asc: "駅から近い順", age_asc: "築年数が新しい順", unit_asc: "㎡単価が安い順" };
  const PRICES = [1000, 2000, 3000, 4000, 5000, 6000, 7000, 8000, 10000, 12000, 15000, 20000, 30000].map((m) => m * 1e4);
  const PLANS = [[3, "1K"], [5, "1LDK"], [7, "2DK"], [8, "2LDK"], [10, "3DK"], [11, "3LDK"], [14, "4LDK"]];
  const SIZES = [40, 50, 60, 70, 80, 100, 120];
  const LANDS = [50, 80, 100, 120, 150, 200];
  const AGES = [[0, "新築"], [5, "5年以内"], [10, "10年以内"], [20, "20年以内"], [30, "30年以内"]];
  const WALKS = [5, 7, 10, 15, 20];
  const FLAGS = [["freehold", "所有権のみ"], ["noCondition", "建築条件なし"], ["newOnly", "新着（7日）"],
    ["dropsOnly", "値下げ（30日）"], ["post1981", "新耐震基準"]];
  const KINDS = { 1: "区部", 2: "市部" };  // third digit of a JIS municipality code; others are towns/villages
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
  const favs = new Set(JSON.parse(localStorage.getItem("suumo.favorites") || "[]"));

  // ---------- formatting ----------

  function man(yen, html = false) {
    if (yen == null) return "価格未定";
    const m = Math.round(yen / 1e4), oku = Math.floor(m / 1e4), rest = m % 1e4;
    const unit = (s) => (html ? `<small>${s}</small>` : s);
    if (oku) return `${oku}${unit("億")}${rest ? rest.toLocaleString() + unit("万円") : unit("円")}`;
    return m.toLocaleString() + unit("万円");
  }
  const price = (it, html) => man(it.price, html) + (it.priceHi && it.priceHi !== it.price ? `〜${man(it.priceHi, html)}` : "");
  const m2 = (v) => (v ? `${+v.toFixed(2)}㎡` : "");
  const tsubo = (v) => (v ? `${(v / TSUBO).toFixed(1)}坪` : "");
  const isCondo = (t) => t === "used_condo" || t === "new_condo";
  const day = (d) => { const s = String(d ?? "").replaceAll("-", ""); return s.length === 8 ? `${+s.slice(0, 4)}年${+s.slice(4, 6)}月${+s.slice(6)}日` : ""; };
  function age(it) {
    if (it.type === "new_condo" || it.type === "new_house") return "新築";
    return it.age == null ? "" : it.age === 0 ? "築1年未満" : `築${it.age}年`;
  }
  function size(it) {
    if (isCondo(it.type)) return m2(it.size);
    if (it.type === "land") return `土地 ${m2(it.land)}`;
    return [it.land && `土地 ${m2(it.land)}`, it.size && `建物 ${m2(it.size)}`].filter(Boolean).join(" / ");
  }
  function station(it) {
    let best = null;
    for (const [s, w] of it.stations) if (w != null && (!best || w < best[1])) best = [s, w];
    if (best) return `${best[0]}駅 徒歩${best[1]}分`;
    return it.stations.length ? `${it.stations[0][0]}駅 バス` : "";
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
    if (!SORTS[q.sort]) q.sort = "new";
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
    const covered = document.body.classList.contains("show-filters") && !WIDE.matches;
    if (covered) {  // only the sheet is visible: its counts are enough until it closes
      listStale = true;
      renderFilters();
      return;
    }
    listStale = false;
    hits = compute();
    document.body.classList.toggle("show-map", showMap && !WIDE.matches);
    $("q").value = q.text;
    $("sort").value = q.sort;
    $("count").textContent = `${hits.length.toLocaleString()}件`;
    const active = activeConditions();
    $("filters-n").hidden = !active.length;
    $("filters-n").textContent = active.length;
    $("favs-n").hidden = !favs.size;
    $("favs-n").textContent = favs.size;
    $("favs").setAttribute("aria-pressed", mode === "favorites");
    $("chips").innerHTML = mode === "search" ? active.map(([label], i) =>
      `<button class="chip" data-remove="${i}" aria-label="${esc(label)}を外す">${esc(label)}<svg class="i x"><use href="#i-x"/></svg></button>`).join("") : "";
    renderBanner();
    $("view").innerHTML = showMap ? `${icon("list")}<span>リスト</span>` : `${icon("map")}<span>地図</span>`;
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
    if (it.flags & F.new) out.push('<span class="tag new">新着</span>');
    if (it.flags & F.dropped) out.push('<span class="tag drop">値下げ</span>');
    if (it.flags & F.leasehold) out.push('<span class="tag warn">借地権</span>');
    if (it.flags & F.conditional) out.push('<span class="tag warn">建築条件付</span>');
    if (it.others) out.push(`<span class="tag">ほか${it.others}社も掲載</span>`);
    return out.join("");
  }

  function card(it) {
    const spec = [it.layout, ...size(it).split(" / "), age(it)].filter(Boolean).map((s) => `<span>${esc(s)}</span>`).join("");
    return `<article class="card"><a href="${esc(hashFor({ id: it.key }))}" data-key="${esc(it.key)}">
      <div class="ph">${it.image ? `<img src="${esc(image(it, 360, 270))}" alt="" loading="lazy" decoding="async" onerror="this.remove()">` : ""}</div>
      <div class="body">
        <div class="price num">${price(it, true)}</div>
        <div class="spec">${spec}</div>
        <div class="meta">${esc(station(it))}</div>
        <div class="meta">${esc([it.town, it.name || TYPES[it.type]].filter(Boolean).join(" · "))}</div>
        <div class="tags">${tags(it)}</div>
      </div></a>
      <button class="fav" data-fav="${esc(it.key)}" aria-pressed="${favs.has(it.key)}" aria-label="お気に入り">${icon("heart")}</button></article>`;
  }

  function emptyState(active) {
    if (mode !== "search") {
      return `<div class="empty"><h3>${mode === "favorites" ? "お気に入りはまだありません" : "物件が見つかりません"}</h3>
        <p>${mode === "favorites" ? "物件のハートを押すと、ここに保存されます。" : "掲載が終わった可能性があります。"}</p></div>`;
    }
    const loosen = active.map(([label, remove], i) => {
      const saved = q;
      q = JSON.parse(JSON.stringify(q));
      remove();
      const n = Filter.count(db, q);
      q = saved;
      return n ? `<button class="chip" data-remove="${i}">${esc(label)}を外す<span class="n">${n.toLocaleString()}件</span></button>` : "";
    }).join("");
    return `<div class="empty"><h3>条件に合う物件がありません</h3><p>条件をひとつ外すと見つかるかもしれません。</p>
      <div class="chips">${loosen}</div>${active.length ? '<button class="btn" data-clear="1">すべてクリア</button>' : ""}</div>`;
  }

  function renderBanner() {
    const b = $("banner");
    b.hidden = mode === "search";
    if (mode === "favorites") {
      b.innerHTML = `<span>お気に入り ${favs.size}件</span><span>${favs.size ? '<button class="btn" data-share="1">リストを共有</button>' : ""}
        <button class="btn ghost" data-search="1">検索に戻る</button></span>`;
    } else if (mode === "shared") {
      b.innerHTML = `<span>共有されたリスト ${shared.length}件</span><span><button class="btn" data-import="1">お気に入りに追加</button>
        <button class="btn ghost" data-search="1">検索に戻る</button></span>`;
    }
  }

  // The conditions in force, as [label, remove()] for the chips and the empty state.
  function activeConditions() {
    const out = [], drop = (f, v) => () => { q[f] = q[f].filter((x) => x !== v); };
    const reset = (...fs) => () => { const e = Filter.emptyQuery(); for (const f of fs) q[f] = e[f]; };
    if (q.text) out.push([`「${q.text}」`, reset("text")]);
    for (const t of q.types) out.push([TYPES[t], drop("types", t)]);
    if (q.priceMin != null || q.priceMax != null) {
      out.push([`${q.priceMin != null ? man(q.priceMin) : ""}〜${q.priceMax != null ? man(q.priceMax) : ""}`, reset("priceMin", "priceMax")]);
    }
    if (q.plan != null) out.push([`${PLANS.find(([v]) => v === q.plan)?.[1] ?? ""}以上`, reset("plan")]);
    if (q.sizeMin != null) out.push([`${q.sizeMin}㎡以上`, reset("sizeMin")]);
    if (q.landMin != null) out.push([`土地${q.landMin}㎡以上`, reset("landMin")]);
    if (q.ageMax != null) out.push([AGES.find(([v]) => v === q.ageMax)?.[1] ?? `築${q.ageMax}年以内`, reset("ageMax")]);
    for (const a of q.areas) out.push([areaName(a), drop("areas", a)]);
    for (const s of q.stations) out.push([`${s}駅`, drop("stations", s)]);
    if (q.walk != null) out.push([`徒歩${q.walk}分以内`, reset("walk")]);
    for (const [f, label] of FLAGS) if (q[f]) out.push([label, reset(f)]);
    for (const f of q.features) out.push([f, drop("features", f)]);
    return out;
  }

  let areaNames = new Map();
  const areaName = (code) => areaNames.get(code) || code;

  // ---------- filters ----------

  const filtersVisible = () => WIDE.matches || document.body.classList.contains("show-filters");
  const CHOICES = { priceMin: PRICES, priceMax: PRICES, plan: PLANS.map(([v]) => v), sizeMin: SIZES, landMin: LANDS,
    ageMax: AGES.map(([v]) => v), walk: WALKS };

  function chipBtn(attrs, label, n, on) {
    return `<button class="chip" ${attrs} aria-pressed="${on}"${!n && !on ? " disabled" : ""}>${esc(label)}${n != null ? `<span class="n">${n.toLocaleString()}</span>` : ""}</button>`;
  }
  const many = (field, value, label, n) => chipBtn(`data-many="${field}" data-value="${esc(value)}"`, label, n, q[field].includes(value));
  const one = (field, value, label, n) => chipBtn(`data-one="${field}" data-value="${value}"`, label, n, q[field] === value);
  const flag = (field, label, n) => chipBtn(`data-flag="${field}"`, label, n, q[field]);
  function select(field, label, values, fmt) {
    const counts = facets[field];
    return `<label class="field"><span>${label}</span><select class="select" data-num="${field}"><option value="">指定なし</option>${values.map((v) =>
      `<option value="${v}"${q[field] === v ? " selected" : ""}>${esc(fmt(v))}（${(counts[v] || 0).toLocaleString()}）</option>`).join("")}</select></label>`;
  }

  function renderFilters() {
    const key = JSON.stringify({ ...q, sort: "" });  // the sort doesn't change any count
    if (key !== facetsFor) { facets = Filter.facets(db, q, CHOICES); facetsFor = key; }
    const body = $("filters-body");
    if (!body.firstChild) {  // sections are built once; their contents are redrawn (the station search keeps focus)
      body.innerHTML = ["text", "types", "areas", "price", "plan", "size", "age", "stations", "extra"].map((s) =>
        `<section class="sec" data-sec="${s}"></section>`).join("");
      body.querySelector('[data-sec="stations"]').innerHTML = `<h3>駅・徒歩</h3>
        <input class="text-input" id="station-q" type="search" placeholder="駅名で探す" autocomplete="off">
        <div class="opts" id="station-list"></div><div class="opts" id="walk-list" style="margin-top:12px"></div>`;
    }
    const sec = (s, html) => { body.querySelector(`[data-sec="${s}"]`).innerHTML = html; };
    const isLand = q.types.length && q.types.every((t) => t === "land");
    sec("text", q.text ? `<div class="opts"><button class="chip" aria-pressed="true" data-cleartext="1">「${esc(q.text)}」で絞り込み中${icon("x")}</button></div>` : "");
    sec("types", `<h3>種別</h3><div class="opts">${db.index.types.map((t) => many("types", t, TYPES[t], facets.types[t] || 0)).join("")}</div>`);
    sec("price", `<h3>価格</h3>${select("priceMin", "下限", PRICES, (v) => `${man(v)}以上`)}${select("priceMax", "上限", PRICES, (v) => `${man(v)}以下`)}`);
    sec("plan", isLand ? "" : `<h3>間取り<span class="hint">以上</span></h3><div class="opts">${PLANS.map(([v, l]) =>
      one("plan", v, `${l}〜`, facets.plan[v] || 0)).join("")}</div>`);
    sec("size", `<h3>広さ</h3>${isLand ? "" : select("sizeMin", "専有・建物", SIZES, (v) => `${v}㎡以上`)}${
      q.types.length && q.types.every(isCondo) ? "" : select("landMin", "土地", LANDS, (v) => `${v}㎡以上（${Math.round(v / TSUBO)}坪）`)}`);
    sec("age", isLand ? "" : `<h3>築年数</h3><div class="opts">${AGES.map(([v, l]) => one("ageMax", v, l, facets.ageMax[v] || 0)).join("")}${
      flag("post1981", "新耐震基準", facets.post1981)}</div>`);
    stationList();
    $("walk-list").innerHTML = WALKS.map((w) => one("walk", w, `徒歩${w}分以内`, facets.walk[w] || 0)).join("");
    sec("areas", `<h3>エリア</h3>${areaGroups()}`);
    const tags = Object.keys(facets.features).sort((a, b) => facets.features[b] - facets.features[a]);
    const shownTags = [...q.features, ...tags.filter((t) => !q.features.includes(t)).slice(0, 30)];
    sec("extra", `<h3>こだわり</h3><div class="opts">${FLAGS.filter(([f]) => f !== "post1981" && (facets[f] || q[f])).map(([f, l]) =>
      flag(f, l, facets[f])).join("")}</div><div class="opts" style="margin-top:10px">${shownTags.map((t) =>
      many("features", t, t.normalize("NFKC"), facets.features[t] || 0)).join("")}</div>`);
    $("apply").textContent = `${facets.total.toLocaleString()}件を表示`;
  }

  function stationList() {
    const term = Filter.normalize($("station-q").value.trim());
    const counts = facets.stations;
    let names = Object.keys(counts);
    if (term) names = db.index.stations.filter((s) => Filter.normalize(s).includes(term));
    names = names.filter((s) => !q.stations.includes(s)).sort((a, b) => (counts[b] || 0) - (counts[a] || 0)).slice(0, 24);
    $("station-list").innerHTML = [...q.stations, ...names].map((s) => many("stations", s, `${s}駅`, counts[s] || 0)).join("")
      || `<span class="meta">${/^[ぁ-ゖァ-ヺー]+$/.test($("station-q").value.trim()) ? "駅名は漢字で入力してください" : "該当する駅がありません"}</span>`;
  }

  function areaGroups() {
    const prefs = new Set(db.index.areas.map((a) => a[2]));
    const groups = new Map();
    for (const [code, name, pref] of [...db.index.areas].sort((a, b) => a[0].localeCompare(b[0]))) {
      const g = `${prefs.size > 1 ? db.index.prefs[pref] + " " : ""}${KINDS[code[2]] || "町村"}`;
      if (!groups.has(g)) groups.set(g, []);
      groups.get(g).push(many("areas", code, name, facets.areas[code] || 0));
    }
    return [...groups].map(([g, chips], i) => {
      const open = i === 0 || q.areas.some((a) => chips.join("").includes(`data-value="${a}"`));
      return `<details class="group"${open ? " open" : ""}><summary>${esc(g)}</summary><div class="opts">${chips.join("")}</div></details>`;
    }).join("");
  }

  function onFilterClick(e) {
    const b = e.target.closest("button[data-many], button[data-one], button[data-flag]");
    if (!b) return;
    const v = b.dataset.value;
    if (b.dataset.many) {
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
    update();
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
      const label = n.toLocaleString();
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
    note.textContent = hits.length ? `${hits.length.toLocaleString()}件${unplaced ? `（位置不明 ${unplaced.toLocaleString()}件）` : ""}`
      : "条件に合う物件がありません";
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
    return `<strong>${esc(town)}</strong> ${list.length}件<ul class="popup-list">${list.slice(0, 40).map((it) =>
      `<li><a href="${esc(hashFor({ id: it.key }))}" data-key="${esc(it.key)}">${it.image ? `<img src="${esc(image(it, 120, 90))}" alt="" loading="lazy">` : "<span></span>"}
        <span><b class="num">${man(it.price)}</b><br>${esc([it.layout || TYPES[it.type], age(it)].filter(Boolean).join(" · "))}<br>${esc(station(it))}</span></a></li>`).join("")}</ul>`;
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
      body.innerHTML = '<div class="empty"><h3>この物件は掲載が終わりました</h3></div>';
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
    const type = r.type, rows = (pairs) => pairs.filter(([, v]) => v != null && v !== "")
      .map(([k, v]) => `<dt>${esc(k)}</dt><dd>${v}</dd>`).join("");
    const sec = (h, pairs) => { const html = rows(pairs); return html ? `<section class="d-sec"><h2>${h}</h2><dl>${html}</dl></section>` : ""; };
    const yen = (v) => (v ? `${v.toLocaleString()}円` : null);
    const t = (s) => (s == null ? null : esc(s));
    const nearest = it ? station(it) : "";
    const unit = it?.unit ? `${(it.unit / 10).toFixed(1)}万円/㎡` + (isCondo(type) ? "" : `（坪${Math.round(it.unit * TSUBO / 10)}万円）`) : "";
    const facts = [["間取り", r.layout], [isCondo(type) ? "専有面積" : "建物", m2(r.floor_m2 || r.building_m2)],
      [isCondo(type) ? "バルコニー" : "土地", isCondo(type) ? m2(r.balcony_m2) : m2(r.land_m2) && `${m2(r.land_m2)}・${tsubo(r.land_m2)}`],
      ["築年", it ? age(it) : ""], ["駅", nearest], ["単価", unit]].filter(([, v]) => v);
    const history = r.history?.length ? r.history.map(([d, o, n]) => esc(`${day(d)} ${man(o)} → ${man(n)}`)).join("<br>")
      : `変更なし（${day(r.new_date || r.first_seen)}から掲載）`;
    const parking = r.parking && [r.parking.status, r.parking.fee_min && `月${r.parking.fee_min.toLocaleString()}円${r.parking.fee_max ? "〜" : ""}`].filter(Boolean).join(" ");
    const road = r.road && ([r.road.dir, r.road.width_m && `幅${r.road.width_m}m`].filter(Boolean).join(" ") || r.road.text);
    return `
      ${r.image ? `<img class="hero" src="${esc(r.image.replace(/w=\d+&h=\d+/, "w=640&h=480"))}" alt="">` : ""}
      <div class="d-head">
        <h1 id="detail-title">${esc(r.name || [r.layout, TYPES[type]].filter(Boolean).join(" "))}</h1>
        <div class="meta">${esc([r.address, TYPES[type]].filter(Boolean).join(" · "))}</div>
        <div class="d-price num">${it ? price(it, true) : man(r.price, true)}${r.price_excludes_building ? "<small>建物価格別</small>" : ""}</div>
        <div class="tags">${it ? tags(it) : ""}</div>
      </div>
      <div class="facts">${facts.map(([k, v]) => `<div class="fact"><span>${k}</span><b>${esc(v)}</b></div>`).join("")}</div>
      ${sec("価格の推移", [["価格", history]])}
      ${sec("交通", [["最寄り駅", (r.stations || []).map((s) => esc(`${s.line || ""} ${s.name}${s.bus_stop ? "" : "駅"} ${s.bus
        ? `バス${s.bus}分${s.walk != null ? `・停歩${s.walk}分` : ""}` : s.walk != null ? `徒歩${s.walk}分` : ""}`)).join("<br>")]])}
      ${sec("費用", [["管理費", yen(r.mgmt_fee) && `月${yen(r.mgmt_fee)}${r.mgmt_form ? `（${esc(r.mgmt_form)}）` : ""}`],
        ["修繕積立金", yen(r.repair_fee) && `月${yen(r.repair_fee)}`], ["修繕積立基金", yen(r.repair_fund_once)],
        ["諸費用", t(r.other_fees)], ["駐車場", t(parking)]])}
      ${sec("建物", [["築年月", r.built && `${+r.built.slice(0, 4)}年${+r.built.slice(5, 7)}月${r.built_planned ? "（予定）" : ""}`],
        ["構造", t(r.structure)], ["階", t([r.floor != null && `${r.floor}階`, r.floors_above && `${r.floors_above}階建`].filter(Boolean).join(" / "))],
        ["総戸数", isCondo(type) && r.total_units ? `${r.total_units.toLocaleString()}戸` : null], ["向き", t(r.direction)],
        ["リフォーム", t(r.reform?.text || r.reform?.date)], ["施工", t(r.builder)], ["引渡し", t(r.handover)]])}
      ${sec("土地・法規", [["土地の権利", t(r.land_rights && r.land_rights + (r.land_rights_note ? `（${r.land_rights_note}）` : ""))],
        ["用途地域", t(r.zoning)], ["建ぺい率・容積率", r.coverage_pct || r.far_pct ? `${r.coverage_pct ?? "-"}% / ${r.far_pct ?? "-"}%` : null],
        ["接道", t(road)], ["地目", t(r.land_category)], ["土地状況", t(r.land_status)], ["建築条件", r.build_condition ? "あり" : null],
        ["設備", t(r.utilities)], ["制限", t(r.restrictions)]])}
      ${r.features?.length ? `<section class="d-sec"><h2>特徴</h2><div class="opts">${r.features.map((f) => `<span class="tag">${esc(f.normalize("NFKC"))}</span>`).join("")}</div></section>` : ""}
      ${sec("掲載", [["会社", t(r.agent)], ["取引態様", t(r.deal_type)], ["ほかの掲載", (r.others || []).map((o) =>
        `<a href="${esc(o.url)}" target="_blank" rel="noopener">${esc(o.agent || "掲載ページ")}</a>`).join("<br>") || null],
        ["掲載確認", day(r.new_date || r.first_seen)]])}
      ${it?.lat != null ? '<section class="d-sec"><h2>地図</h2><p class="meta">位置は町・丁目の中心です</p></section><div id="detail-map"></div>' : ""}`;
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

  function toggleFav(key) {
    if (favs.has(key)) favs.delete(key); else favs.add(key);
    localStorage.setItem("suumo.favorites", JSON.stringify([...favs]));
    document.querySelectorAll(`.fav[data-fav="${CSS.escape(key)}"]`).forEach((b) => b.setAttribute("aria-pressed", favs.has(key)));
    $("favs-n").hidden = !favs.size;
    $("favs-n").textContent = favs.size;
    toast(favs.has(key) ? "お気に入りに追加しました" : "お気に入りから外しました");
  }

  function setFavButton() {
    $("detail-fav").setAttribute("aria-pressed", favs.has(openKey));
    $("detail-fav").querySelector("span").textContent = favs.has(openKey) ? "保存済み" : "お気に入り";
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

  function wire() {
    let timer = null;
    $("q").addEventListener("input", (e) => {
      clearTimeout(timer);
      timer = setTimeout(() => { q.text = e.target.value.trim(); mode = "search"; update(); }, 200);
    });
    $("q").addEventListener("keydown", (e) => { if (e.key === "Enter" && COARSE.matches) e.target.blur(); });  // hide the keyboard
    $("sort").innerHTML = Object.entries(SORTS).map(([k, v]) => `<option value="${k}">${v}</option>`).join("");
    $("sort").addEventListener("change", (e) => { q.sort = e.target.value; update(); });
    $("filters-open").addEventListener("click", () => openFilters(true));
    $("filters-close").addEventListener("click", () => openFilters(false));
    $("apply").addEventListener("click", () => openFilters(false));
    $("clear").addEventListener("click", () => { q = { ...Filter.emptyQuery(), sort: q.sort }; update(); });
    $("filters-body").addEventListener("click", onFilterClick);
    $("filters-body").addEventListener("change", (e) => {
      const f = e.target.dataset.num;
      if (f) { q[f] = e.target.value === "" ? null : +e.target.value; update(); }
    });
    $("filters-body").addEventListener("input", (e) => { if (e.target.id === "station-q") stationList(); });
    $("favs").addEventListener("click", () => { mode = mode === "favorites" ? "search" : "favorites"; update(); window.scrollTo(0, 0); });
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
      else if (b.dataset.cleartext) { q.text = ""; update(false); }
      else if (b.dataset.clear) { q = { ...Filter.emptyQuery(), sort: q.sort }; update(); }
      else if (b.dataset.share) {
        const url = `${location.origin}${location.pathname}#ids=${[...favs].join(",")}`;
        if (navigator.share && COARSE.matches) navigator.share({ title: `お気に入り ${favs.size}件`, url }).catch(() => {});
        else navigator.clipboard?.writeText(url).then(() => toast("リンクをコピーしました"), () => prompt("このリンクを共有してください", url));
      } else if (b.dataset.import) {
        shared.forEach((k) => favs.add(k));
        localStorage.setItem("suumo.favorites", JSON.stringify([...favs]));
        toast(`${shared.length}件をお気に入りに追加しました`);
        mode = "favorites"; shared = []; update();
      } else if (b.dataset.search) { mode = "search"; shared = []; update(); }
    });
    window.addEventListener("popstate", route);
    document.addEventListener("keydown", (e) => {
      if (e.key === "Tab" && !$("detail").hidden) trapFocus(e, $("detail"));
      if (e.key !== "Escape") return;
      if (!$("detail").hidden) closeDetail(); else if (document.body.classList.contains("show-filters")) openFilters(false);
    });
    WIDE.addEventListener("change", () => render());
    new IntersectionObserver((es) => { if (es[0].isIntersecting) more(); }, { rootMargin: "800px" }).observe($("sentinel"));
  }

  // Keep Tab inside an open dialog.
  function trapFocus(e, root) {
    const f = [...root.querySelectorAll("a[href], button, select, input")].filter((el) => !el.disabled && el.offsetParent);
    if (!f.length) return;
    const first = f[0], last = f[f.length - 1];
    if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
    else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
  }

  function route() {
    const key = readHash();
    if (hashFor() !== renderedFor) render();
    if (key && db.byKey.has(key)) showDetail(key);
    else if (!$("detail").hidden) { pushed = false; hideDetail(); }
  }

  async function start() {
    $("list").innerHTML = '<div class="skeleton"></div>'.repeat(6);
    try {
      const index = await (await fetch("data/index.json")).json();
      db = Filter.load(index);
      areaNames = new Map(index.areas.map(([code, name]) => [code, name]));
      if (index.prefs.length === 1) document.title = `${index.prefs[0]}の物件さがし`;
    } catch {
      $("list").innerHTML = '<div class="empty"><h3>物件データを読み込めませんでした</h3><p>再読み込みしてください。</p></div>';
      return;
    }
    wire();
    route();
    (window.requestIdleCallback || setTimeout)(() => loadLeaflet());  // so the first map opens without waiting
  }

  start();
})();
