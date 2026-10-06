// The search page: conditions live in the URL (shareable), results render as a list or on a map,
// a listing opens over the page. Filtering rules are in filter.js.
(function () {
  "use strict";
  const $ = (id) => document.getElementById(id);
  const esc = (s) => String(s == null ? "" : s).replace(/[&<>"']/g, (c) => `&#${c.charCodeAt(0)};`);

  const TYPE_JA = { used_condo: "中古マンション", new_condo: "新築マンション", used_house: "中古一戸建て",
    new_house: "新築一戸建て", land: "土地" };
  const ROOMS = { 1: "ワンルーム〜1LDK", 2: "2K〜2LDK", 3: "3K〜3LDK", 4: "4K以上" };
  const SORTS = { new: "新着順", price_asc: "価格が安い順", price_desc: "価格が高い順", size_desc: "広い順",
    walk_asc: "駅から近い順", age_asc: "築年数が新しい順", unit_asc: "㎡単価が安い順" };
  const PRICES = [1000, 2000, 3000, 4000, 5000, 6000, 7000, 8000, 9000, 10000, 12000, 15000, 20000, 30000]; // 万円
  const SIZES = [40, 50, 60, 70, 80, 90, 100, 120, 150];
  const LANDS = [50, 80, 100, 120, 150, 200, 300];
  const AGES = [0, 5, 10, 15, 20, 25, 30, 40];
  const WALKS = [3, 5, 7, 10, 15, 20];
  const TSUBO = 3.30578;
  const PAGE = 40;
  const IMG = "https://img01.suumo.com/jj/resizeImage?src=";

  let db = null;
  let q = Filter.emptyQuery();
  let view = "list";
  let favOnly = false;
  let shown = PAGE;
  let hits = [];
  let sheet = null;
  let line = null;
  let map = null, cluster = null;
  let favs = new Set(JSON.parse(localStorage.getItem("suumo.favorites") || "[]"));
  let areaNames = new Map();
  let detailMap = null;     // the small map in the listing view
  let pushed = false;       // the listing view added a history entry (so closing it goes back)
  let lastFocus = null;     // element to refocus when an overlay closes
  let lastConditions = null; // the URL without the open listing: results re-render only when it changes

  // ---------- formatting ----------

  function man(yen) {
    if (yen == null) return "価格未定";
    const m = Math.round(yen / 10000), oku = Math.floor(m / 10000), rest = m % 10000;
    if (oku && rest) return `${oku}億${rest.toLocaleString()}万円`;
    return oku ? `${oku}億円` : `${m.toLocaleString()}万円`;
  }
  const price = (it) => man(it.price) + (it.priceHi !== it.price && it.priceHi ? `〜${man(it.priceHi)}` : "");
  const m2 = (v) => (v ? `${+v.toFixed(2)}㎡` : "");
  const tsubo = (v) => (v ? `（${(v / TSUBO).toFixed(1)}坪）` : "");
  function age(it) {
    if (it.type === "new_condo" || it.type === "new_house") return "新築";
    if (it.age == null) return "";
    return it.age === 0 ? "築1年未満" : `築${it.age}年`;
  }
  function size(it) {
    if (it.type === "used_condo" || it.type === "new_condo") return m2(it.size);
    if (it.type === "land") return "土地" + m2(it.land) + tsubo(it.land);
    return [it.land && "土地" + m2(it.land), it.size && "建物" + m2(it.size)].filter(Boolean).join(" ");
  }
  function station(it) {
    let best = null;
    for (const [n, w] of it.stations) if (w != null && (!best || w < best[1])) best = [n, w];
    if (best) return `${best[0]}駅 徒歩${best[1]}分`;
    return it.stations.length ? `${it.stations[0][0]}駅 バス` : "";
  }
  function image(it, w, h) {
    let p = it.image;
    if (!p) return "";
    if (p.startsWith("http")) return p;
    if (!p.startsWith("gazo/")) { // shortened by the builder: dir/sub/n/file -> full path with the listing id
      const [a, b, c, file] = p.split("/");
      p = `gazo/bukken/${a}/${b}/img/${c}/${it.id}/${it.id}_${file}`;
    }
    return `${IMG}${encodeURIComponent(p)}&w=${w}&h=${h}`;
  }

  // ---------- URL <-> state ----------

  const LISTS = { t: "types", a: "areas", st: "stations", f: "features" };
  const NUMS = { w: "walk", pmin: "priceMin", pmax: "priceMax", s: "sizeMin", l: "landMin", g: "ageMax" };
  const BOOLS = { quake: "post1981", fh: "freehold", nc: "noCondition", new: "newOnly", drop: "dropsOnly" };

  function readHash() {
    const p = new URLSearchParams(location.hash.slice(1));
    q = Filter.emptyQuery();
    q.text = p.get("q") || "";
    for (const [k, f] of Object.entries(LISTS)) q[f] = p.get(k) ? p.get(k).split(",") : [];
    q.rooms = p.get("r") ? p.get("r").split(",").map(Number) : [];
    for (const [k, f] of Object.entries(NUMS)) {
      const n = Number(p.get(k));
      q[f] = p.has(k) && Number.isFinite(n) ? n : null;
    }
    for (const [k, f] of Object.entries(BOOLS)) q[f] = p.get(k) === "1";
    q.sort = SORTS[p.get("sort")] ? p.get("sort") : "new";
    view = p.get("view") === "map" ? "map" : "list";
    favOnly = p.get("fav") === "1";
    return p.get("l");
  }

  function hashFor(extra) {
    const p = new URLSearchParams();
    if (q.text) p.set("q", q.text);
    for (const [k, f] of Object.entries(LISTS)) if (q[f].length) p.set(k, q[f].join(","));
    if (q.rooms.length) p.set("r", q.rooms.join(","));
    for (const [k, f] of Object.entries(NUMS)) if (q[f] != null) p.set(k, q[f]);
    for (const [k, f] of Object.entries(BOOLS)) if (q[f]) p.set(k, "1");
    if (q.sort !== "new") p.set("sort", q.sort);
    if (view === "map") p.set("view", "map");
    if (favOnly) p.set("fav", "1");
    for (const [k, v] of Object.entries(extra || {})) p.set(k, v);
    return "#" + p.toString();
  }

  function saveHash() {
    history.replaceState(null, "", hashFor());
    lastConditions = hashFor();
  }

  // ---------- results ----------

  function update() {
    shown = PAGE;
    saveHash();
    render();
  }

  function compute() {
    if (favOnly) {
      const fq = Object.assign(Filter.emptyQuery(), { sort: q.sort });
      return Filter.search(db, fq).filter((h) => favs.has(h.item.key) || h.others.some((o) => favs.has(o.key)));
    }
    return Filter.search(db, q);
  }

  function render() {
    hits = compute();
    $("count").textContent = `${hits.length.toLocaleString()}件`;
    $("text").value = q.text;
    $("sort").value = q.sort;
    document.querySelectorAll(".seg button").forEach((b) => {
      b.classList.toggle("on", b.dataset.view === view);
      b.setAttribute("aria-pressed", b.dataset.view === view);
    });
    renderChips();
    const notice = $("notice");
    notice.hidden = !favOnly;
    if (favOnly) notice.textContent = "⭐ お気に入りだけを表示中（ほかの条件は使いません）。掲載が終わった物件は表示されません。";
    $("list").hidden = view !== "list";
    $("more").hidden = view !== "list";
    $("map").hidden = view !== "map";
    if (view === "list") renderList();
    else renderMap();
  }

  function tags(h) {
    const it = h.item, F = db.flags, out = [];
    if (it.flags & F.new) out.push('<span class="tag new">新着</span>');
    if (it.flags & F.dropped) out.push('<span class="tag drop">値下げ</span>');
    if (it.flags & F.leasehold) out.push('<span class="tag warn">借地権</span>');
    if (it.flags & F.conditional) out.push('<span class="tag warn">建築条件付</span>');
    if (h.others.length) out.push(`<span class="tag">ほか${h.others.length}社も掲載</span>`);
    return out.join("");
  }

  function card(h) {
    const it = h.item;
    return `<article class="card" data-key="${esc(it.key)}" tabindex="0" aria-label="${esc(price(it))} ${esc(it.layout)}">
      <img loading="lazy" src="${esc(image(it, 240, 180))}" alt="">
      <div class="info">
        <div class="price">${esc(price(it))}</div>
        <div class="spec">${esc([it.layout, size(it), age(it)].filter(Boolean).join(" ・ "))}</div>
        <div class="where">${esc([it.town, station(it)].filter(Boolean).join(" ・ "))}</div>
        <div class="name">${esc(it.name)}</div>
        <div class="kind">${esc(TYPE_JA[it.type])}</div>
        <div class="tags">${tags(h)}</div>
      </div>
      <button class="star" data-star="${esc(it.key)}" aria-label="お気に入り" aria-pressed="${favs.has(it.key)}">${favs.has(it.key) ? "★" : "☆"}</button>
    </article>`;
  }

  function renderList() {
    if (!hits.length) {
      $("list").innerHTML = `<div class="empty">${favOnly ? "お気に入りはまだありません。物件の ☆ で追加できます。"
        : "条件に合う物件がありません。条件をゆるめてみてください。"}</div>`;
      $("more").innerHTML = "";
      return;
    }
    $("list").innerHTML = hits.slice(0, shown).map(card).join("");
    $("more").innerHTML = shown < hits.length
      ? `<button id="more-btn">もっと見る（残り${(hits.length - shown).toLocaleString()}件）</button>` : "";
  }

  // ---------- map ----------

  function renderMap() {
    if (!map) {
      map = L.map("map", { preferCanvas: true }).setView([35.68, 139.65], 11);
      L.tileLayer("https://cyberjapandata.gsi.go.jp/xyz/pale/{z}/{x}/{y}.png", {
        attribution: '<a href="https://maps.gsi.go.jp/development/ichiran.html" target="_blank">地理院タイル</a>',
        maxZoom: 18,
      }).addTo(map);
      cluster = L.markerClusterGroup({
        maxClusterRadius: 50, chunkedLoading: true,
        iconCreateFunction: (c) => townIcon(c.getAllChildMarkers().reduce((n, m) => n + m.options.count, 0)),
      });
      map.addLayer(cluster);
    }
    setTimeout(() => map.invalidateSize(), 0);
    const byTown = new Map();
    let unplaced = 0;
    for (const h of hits) {
      const it = h.item;
      if (it.lat == null) { unplaced++; continue; }
      const k = it.town;
      if (!byTown.has(k)) byTown.set(k, { lat: it.lat, lng: it.lng, hits: [] });
      byTown.get(k).hits.push(h);
    }
    cluster.clearLayers();
    const markers = [];
    for (const [town, t] of byTown) {
      const m = L.marker([t.lat, t.lng], { icon: townIcon(t.hits.length), count: t.hits.length });
      m.bindPopup(() => popup(town, t.hits), { maxWidth: 280 });
      markers.push(m);
    }
    cluster.addLayers(markers);
    const notice = $("notice");
    if (unplaced && !favOnly) {
      notice.hidden = false;
      notice.textContent = `地図に表示できない物件が${unplaced.toLocaleString()}件あります（位置を確認中）。`;
    }
  }

  function townIcon(n) {
    const w = n >= 1000 ? 44 : n >= 100 ? 36 : 28;
    return L.divIcon({ className: "town-icon", html: n.toLocaleString(), iconSize: [w, 24] });
  }

  function popup(town, list) {
    const div = document.createElement("div");
    div.innerHTML = `<strong>${esc(town)}</strong> ${list.length}件<ul class="popup-list">${list.slice(0, 50)
      .map((h) => `<li data-key="${esc(h.item.key)}">${esc(price(h.item))} ・ ${esc(h.item.layout || TYPE_JA[h.item.type])}
        ・ ${esc(size(h.item))}</li>`).join("")}</ul>${list.length > 50 ? `<small>ほか${list.length - 50}件</small>` : ""}`;
    div.addEventListener("click", (e) => {
      const li = e.target.closest("li[data-key]");
      if (li) openDetail(li.dataset.key);
    });
    return div;
  }

  // ---------- condition chips and sheets ----------

  const CHIPS = [
    ["types", "種別", () => q.types.map((t) => TYPE_JA[t]).join("・")],
    ["price", "価格", () => (q.priceMin != null || q.priceMax != null
      ? `${q.priceMin != null ? man(q.priceMin) : ""}〜${q.priceMax != null ? man(q.priceMax) : ""}` : "")],
    ["rooms", "間取り", () => q.rooms.map((r) => ROOMS[r]).join("・")],
    ["areas", "エリア", () => q.areas.map(areaName).join("・")],
    ["stations", "駅・徒歩", () => [q.stations.map((s) => s + "駅").join("・"),
      q.walk != null ? `徒歩${q.walk}分` : ""].filter(Boolean).join(" ")],
    ["size", "広さ・築年数", () => [q.sizeMin != null && `${q.sizeMin}㎡〜`, q.landMin != null && `土地${q.landMin}㎡〜`,
      q.ageMax != null && (q.ageMax === 0 ? "新築" : `築${q.ageMax}年以内`), q.post1981 && "新耐震"]
      .filter(Boolean).join(" ")],
    ["extra", "こだわり", () => [...q.features, q.freehold && "所有権のみ", q.noCondition && "建築条件なし",
      q.newOnly && "新着", q.dropsOnly && "値下げ"].filter(Boolean).join("・")],
  ];

  const areaName = (code) => areaNames.get(code) || code;

  function isEmpty() {
    return JSON.stringify(Object.assign({}, q, { sort: "new" })) === JSON.stringify(Filter.emptyQuery());
  }

  function renderChips() {
    const html = CHIPS.map(([id, label, value]) => {
      const v = value();
      return `<button class="chip${v ? " on" : ""}" data-sheet="${id}">${esc(v || label)} ▾</button>`;
    });
    html.push(`<button class="chip${favOnly ? " on" : ""}" data-fav="1">⭐ お気に入り ${favs.size || ""}</button>`);
    if (!isEmpty()) html.push('<button class="chip clear" data-clear="1">✕ 条件をクリア</button>');
    $("chips").innerHTML = html.join("");
  }

  function count(change) {
    return Filter.count(db, Object.assign({}, q, change));
  }

  function checkboxes(name, options, chosen) {
    return `<div class="opts">${options.map(([value, label, n]) => `<label class="${n ? "" : "zero"}">
      <input type="checkbox" name="${name}" value="${esc(value)}"${chosen.includes(value) ? " checked" : ""}>
      ${esc(label)}<span class="n">${n == null ? "" : n.toLocaleString()}</span></label>`).join("")}</div>`;
  }

  function select(name, label, options, current) {
    return `<div class="field"><span>${esc(label)}</span><select name="${name}">${options.map(([value, text]) =>
      `<option value="${esc(value)}"${String(current) === String(value) ? " selected" : ""}>${esc(text)}</option>`)
      .join("")}</select></div>`;
  }

  const SHEETS = {
    types: ["種別", () => {
      const n = Filter.facet(db, q, "types", (it) => [it.type]);
      return checkboxes("types", db.index.types.map((t) => [t, TYPE_JA[t], n[t] || 0]), q.types);
    }],
    price: ["価格", () => {
      const opts = (key, fmt) => [["", "指定なし"], ...PRICES.map((p) => [p * 10000,
        `${fmt(p * 10000)}（${count({ [key]: p * 10000 }).toLocaleString()}件）`])];
      return select("priceMin", "下限", opts("priceMin", (y) => `${man(y)}以上`), q.priceMin ?? "")
        + select("priceMax", "上限", opts("priceMax", (y) => `${man(y)}以下`), q.priceMax ?? "");
    }],
    rooms: ["間取り", () => {
      const n = Filter.facet(db, q, "rooms", (it) => [1, 2, 3, 4].filter((b) => it.rooms & (1 << (b - 1))));
      return checkboxes("rooms", Object.entries(ROOMS).map(([b, label]) => [b, label, n[b] || 0]),
        q.rooms.map(String)) + '<p class="kind">土地は間取りの条件に関係なく、種別で「土地」を選んだときに表示されます。</p>';
    }],
    areas: ["エリア", () => {
      const n = Filter.facet(db, q, "areas", (it) => [it.area]);
      const groups = { 1: "区部", 2: "市部", 3: "町村" };
      const byGroup = {};
      for (const [code, name] of db.index.areas) {
        const g = code[2] === "1" || code[2] === "2" ? code[2] : "3";
        (byGroup[g] = byGroup[g] || []).push([code, name, n[code] || 0]);
      }
      return (q.areas.length ? '<button class="link" data-reset="areas">すべて解除</button>' : "")
        + Object.keys(byGroup).sort().map((g) => `<div class="group"><h3>${groups[g]}</h3>`
          + checkboxes("areas", byGroup[g].sort((a, b) => a[0].localeCompare(b[0])), q.areas) + "</div>").join("");
    }],
    stations: ["駅・徒歩", () => {
      const walks = [["", "指定なし"], ...WALKS.map((w) => [w, `徒歩${w}分以内（${count({ walk: w }).toLocaleString()}件）`])];
      const lineCounts = Filter.facet(db, q, "stations", (it) => linesOf(it));
      const lines = Object.keys(db.index.lines).filter((l) => lineCounts[l]).sort((a, b) => lineCounts[b] - lineCounts[a]);
      let html = select("walk", "駅から徒歩", walks, q.walk ?? "");
      if (q.stations.length) html += `<div class="picked">${q.stations.map((s) =>
        `<button data-unpick="${esc(s)}">${esc(s)}駅 ✕</button>`).join("")}</div>`;
      html += select("line", "路線", [["", "路線を選ぶ"], ...lines.map((l) => [l, `${l}（${lineCounts[l].toLocaleString()}）`])],
        line ?? "");
      if (line) {
        const names = db.index.lines[line].map((i) => db.index.stations[i]);
        const n = Filter.facet(db, q, "stations", (it) => it.stations
          .filter(([s, w]) => w != null && (q.walk == null || w <= q.walk)).map(([s]) => s));
        html += checkboxes("stations", names.map((s) => [s, s + "駅", n[s] || 0])
          .sort((a, b) => b[2] - a[2]), q.stations);
      }
      return html + '<p class="kind">徒歩は、選んだ駅（なければ最寄り駅）までの時間です。バス便は含みません。</p>';
    }],
    size: ["広さ・築年数", () => {
      const any = ["", "指定なし"];
      return select("sizeMin", "広さ（専有・建物）", [any, ...SIZES.map((s) => [s, `${s}㎡以上（${count({ sizeMin: s }).toLocaleString()}件）`])], q.sizeMin ?? "")
        + select("landMin", "土地面積", [any, ...LANDS.map((s) => [s, `${s}㎡以上・${Math.round(s / TSUBO)}坪（${count({ landMin: s }).toLocaleString()}件）`])], q.landMin ?? "")
        + select("age", "築年数", [any, ...AGES.map((a) => [a, `${a === 0 ? "新築・築1年未満" : `築${a}年以内`}（${count({ ageMax: a, post1981: false }).toLocaleString()}件）`]),
          ["quake", `新耐震基準・1981年6月以降（${count({ ageMax: null, post1981: true }).toLocaleString()}件）`]],
          q.post1981 ? "quake" : (q.ageMax ?? ""));
    }],
    extra: ["こだわり", () => {
      const toggles = [["freehold", "所有権のみ（借地権を除く）"], ["noCondition", "建築条件なし（土地）"],
        ["newOnly", "新着のみ（この1週間）"], ["dropsOnly", "値下げのみ（この30日）"]];
      const n = Filter.facet(db, q, "features", (it) => it.features);
      const top = Object.keys(n).sort((a, b) => n[b] - n[a]).filter((f) => !q.features.includes(f)).slice(0, 40);
      return `<div class="toggles">${toggles.map(([k, label]) => `<label><input type="checkbox" name="toggle"
        value="${k}"${q[k] ? " checked" : ""}> ${label}${q[k] ? "" : `（${count({ [k]: true }).toLocaleString()}件）`}</label>`).join("")}</div>
        <div class="group"><h3>特徴（すべて満たす物件）</h3>${checkboxes("features",
          [...q.features, ...top].map((f) => [f, f, n[f] || 0]), q.features)}</div>
        <p class="kind">特徴は物件ページを取得済みの物件だけが対象です。</p>`;
    }],
  };

  let stationLines = null; // station name -> lines it's on
  function linesOf(it) {
    if (!stationLines) {
      stationLines = new Map();
      for (const [l, ids] of Object.entries(db.index.lines)) {
        for (const i of ids) {
          const s = db.index.stations[i];
          if (!stationLines.has(s)) stationLines.set(s, []);
          stationLines.get(s).push(l);
        }
      }
    }
    const out = new Set();
    for (const [s, w] of it.stations) if (w != null) for (const l of stationLines.get(s) || []) out.add(l);
    return out;
  }

  function openSheet(id) {
    lastFocus = document.activeElement;
    sheet = id;
    if (id === "stations" && !line && q.stations.length) {
      line = Object.keys(db.index.lines).find((l) => db.index.lines[l].some((i) => q.stations.includes(db.index.stations[i])));
    }
    $("sheet").hidden = false;
    renderSheet();
    $("sheet-close").focus();
  }

  function renderSheet() {
    const [title, body] = SHEETS[sheet];
    $("sheet-title").textContent = title;
    $("sheet-body").innerHTML = body();
    $("sheet-done").textContent = `${count({}).toLocaleString()}件を見る`;
  }

  function closeSheet() {
    $("sheet").hidden = true;
    sheet = null;
    if (lastFocus) lastFocus.focus();
  }

  function onSheetChange(e) {
    const el = e.target, name = el.name;
    const checked = (n) => [...$("sheet-body").querySelectorAll(`input[name="${n}"]:checked`)].map((x) => x.value);
    const shownValues = (n) => [...$("sheet-body").querySelectorAll(`input[name="${n}"]`)].map((x) => x.value);
    if (name === "types" || name === "areas" || name === "features") q[name] = checked(name);
    else if (name === "rooms") q.rooms = checked("rooms").map(Number);
    else if (name === "stations") {
      const visible = new Set(shownValues("stations"));
      q.stations = [...q.stations.filter((s) => !visible.has(s)), ...checked("stations")];
    } else if (name === "toggle") q[el.value] = el.checked;
    else if (name === "line") line = el.value || null;
    else if (name === "age") {
      q.post1981 = el.value === "quake";
      q.ageMax = el.value === "" || el.value === "quake" ? null : Number(el.value);
    } else q[name] = el.value === "" ? null : Number(el.value);
    update();
    renderSheet();
    // the sheet was redrawn: put the focus back on the control that changed
    const again = $("sheet-body").querySelector(el.type === "checkbox"
      ? `[name="${name}"][value="${CSS.escape(el.value)}"]` : `[name="${name}"]`);
    if (again) again.focus();
  }

  // ---------- listing ----------

  function openDetail(key) {
    lastFocus = document.activeElement;
    history.pushState(null, "", hashFor({ l: key }));
    pushed = true;
    showDetail(key, hits.find((x) => x.item.key === key) || Filter.hitFor(db, key));
  }

  async function showDetail(key, h) {
    if (detailMap) { detailMap.remove(); detailMap = null; }
    $("detail").hidden = false;
    $("detail-back").focus();
    $("detail-star").textContent = favs.has(key) ? "★ お気に入り" : "☆ お気に入り";
    $("detail-star").setAttribute("aria-pressed", favs.has(key));
    $("detail-star").dataset.star = key;
    const body = $("detail-body");
    body.innerHTML = '<p class="kind">読み込み中…</p>';
    const [type, id] = key.split(":");
    let r;
    try {
      const res = await fetch(`data/l/${type}/${id}.json`);
      if (!res.ok) throw new Error(res.status);
      r = await res.json();
    } catch {
      body.innerHTML = '<p class="empty">この物件は掲載が終わりました。</p>';
      return;
    }
    const it = h ? h.item : null;
    const rows = [];
    const add = (k, v) => { if (v != null && v !== "") rows.push(`<tr><th>${esc(k)}</th><td>${v}</td></tr>`); };
    const yen = (v) => (v ? `${v.toLocaleString()}円` : "");
    if (it && it.unit) add("㎡単価", `${(it.unit / 10000).toFixed(1)}万円${type !== "used_condo" && type !== "new_condo"
      ? `（坪${Math.round(it.unit * TSUBO / 10000)}万円）` : ""}`);
    add("管理費・修繕積立金", [r.mgmt_fee && `管理費 月${yen(r.mgmt_fee)}`, r.repair_fee && `修繕積立金 月${yen(r.repair_fee)}`]
      .filter(Boolean).map(esc).join("<br>"));
    add("間取り", esc(r.layout));
    if (type === "used_condo" || type === "new_condo") {
      add("専有面積", esc(m2(r.floor_m2) + (r.floor_m2_max ? `〜${m2(r.floor_m2_max)}` : "")));
      add("バルコニー", esc(m2(r.balcony_m2)));
    } else {
      add("土地面積", esc(m2(r.land_m2) + (r.land_m2_max ? `〜${m2(r.land_m2_max)}` : tsubo(r.land_m2))));
      add("建物面積", esc(m2(r.building_m2) + (r.building_m2_max ? `〜${m2(r.building_m2_max)}` : "")));
    }
    add("築年月", esc([r.built && `${r.built.slice(0, 4)}年${+r.built.slice(5, 7)}月`, it && age(it)].filter(Boolean).join(" ・ ")));
    add("階", esc([r.floor != null && `${r.floor}階`, r.floors_above && `${r.floors_above}階建`].filter(Boolean).join(" / ")));
    add("構造", esc(r.structure));
    add("総戸数", r.total_units && (type === "used_condo" || type === "new_condo") ? `${r.total_units.toLocaleString()}戸` : "");
    add("向き", esc(r.direction));
    add("交通", (r.stations || []).map((s) => esc(`${s.line || ""} ${s.name}${s.bus_stop ? "" : "駅"} ${s.bus
      ? `バス${s.bus}分${s.walk != null ? `・停歩${s.walk}分` : ""}` : s.walk != null ? `徒歩${s.walk}分` : ""}`)).join("<br>"));
    add("所在地", esc(r.address));
    add("土地の権利", esc(r.land_rights ? r.land_rights + (r.land_rights_note ? `（${r.land_rights_note}）` : "") : ""));
    add("用途地域", esc(r.zoning));
    if (r.coverage_pct || r.far_pct) add("建ぺい率・容積率", esc(`${r.coverage_pct ?? "-"}% / ${r.far_pct ?? "-"}%`));
    if (r.road) add("接道", esc([r.road.dir, r.road.width_m && `幅${r.road.width_m}m`].filter(Boolean).join(" ") || r.road.text));
    if (r.reform) add("リフォーム", esc(r.reform.text || r.reform.date));
    add("特徴", esc((r.features || []).join("・")));
    add("引渡し", esc(r.handover));
    add("取引態様", esc(r.deal_type));
    add("会社", esc(r.agent));
    if (r.history && r.history.length) add("価格の推移", r.history.map(([d, o, n]) =>
      esc(`${d.slice(5).replace("-", "/")} ${man(o)} → ${man(n)}`)).join("<br>"));
    if (r.others && r.others.length) add(`ほかの掲載（${r.others.length}社）`, r.others.map((o) =>
      `<a href="${esc(o.url)}" target="_blank" rel="noopener">${esc(o.agent || "掲載ページ")}</a> ${esc(man(o.price))}`).join("<br>"));
    add("掲載確認", esc(r.new_date || r.first_seen));
    body.innerHTML = `
      ${r.image ? `<img class="hero" src="${esc(r.image.replace(/w=\d+&h=\d+/, "w=800&h=600"))}" alt="">` : ""}
      <h2>${esc(r.name || r.title || TYPE_JA[type])}</h2>
      ${r.title && r.name ? `<div class="lead">${esc(r.title)}</div>` : ""}
      <div class="big">${esc(it ? price(it) : man(r.price))}${r.price_excludes_building ? '<span class="kind">（建物価格別）</span>' : ""}</div>
      <div class="tags">${h ? tags(h) : ""}<span class="tag">${esc(TYPE_JA[type])}</span></div>
      <a class="cta primary" href="${esc(r.url)}" target="_blank" rel="noopener">SUUMOで見る ↗</a>
      <table>${rows.join("")}</table>
      ${it && it.lat != null ? '<div id="detail-map"></div><p class="kind">地図の位置は町・丁目の中心です。</p>' : ""}`;
    if (it && it.lat != null) {
      detailMap = L.map("detail-map", { zoomControl: false, attributionControl: false }).setView([it.lat, it.lng], 15);
      L.tileLayer("https://cyberjapandata.gsi.go.jp/xyz/pale/{z}/{x}/{y}.png").addTo(detailMap);
      L.circle([it.lat, it.lng], { radius: 150 }).addTo(detailMap);
    }
  }

  function hideDetail() {
    if (detailMap) { detailMap.remove(); detailMap = null; }
    $("detail").hidden = true;
    if (lastFocus) lastFocus.focus();
  }

  function closeDetail() {
    if (pushed) { pushed = false; history.back(); return; }   // popstate hides it
    history.replaceState(null, "", hashFor());              // opened from a shared link: stay on the site
    hideDetail();
  }

  function toggleFav(key) {
    if (favs.has(key)) favs.delete(key); else favs.add(key);
    localStorage.setItem("suumo.favorites", JSON.stringify([...favs]));
  }

  // ---------- events ----------

  function wire() {
    let timer = null;
    $("text").addEventListener("input", (e) => {
      clearTimeout(timer);
      timer = setTimeout(() => { q.text = e.target.value.trim(); update(); }, 200);
    });
    $("sort").innerHTML = Object.entries(SORTS).map(([k, v]) => `<option value="${k}">${v}</option>`).join("");
    $("sort").addEventListener("change", (e) => { q.sort = e.target.value; update(); });
    document.querySelector(".seg").addEventListener("click", (e) => {
      const b = e.target.closest("button[data-view]");
      if (b) { view = b.dataset.view; update(); }
    });
    $("chips").addEventListener("click", (e) => {
      const b = e.target.closest("button");
      if (!b) return;
      if (b.dataset.sheet) openSheet(b.dataset.sheet);
      else if (b.dataset.fav) { favOnly = !favOnly; update(); }
      else if (b.dataset.clear) { q = Object.assign(Filter.emptyQuery(), { sort: q.sort }); line = null; update(); }
    });
    $("sheet-body").addEventListener("change", onSheetChange);
    $("sheet-body").addEventListener("click", (e) => {
      const b = e.target.closest("button");
      if (!b) return;
      if (b.dataset.unpick) q.stations = q.stations.filter((s) => s !== b.dataset.unpick);
      else if (b.dataset.reset) q[b.dataset.reset] = [];
      else return;
      update();
      renderSheet();
    });
    $("sheet-close").addEventListener("click", closeSheet);
    $("sheet-done").addEventListener("click", closeSheet);
    $("sheet").addEventListener("click", (e) => { if (e.target === $("sheet")) closeSheet(); });
    $("list").addEventListener("click", (e) => {
      const star = e.target.closest("[data-star]");
      if (star) {
        toggleFav(star.dataset.star);
        star.textContent = favs.has(star.dataset.star) ? "★" : "☆";
        star.setAttribute("aria-pressed", favs.has(star.dataset.star));
        renderChips();
        return;
      }
      const c = e.target.closest(".card");
      if (c) openDetail(c.dataset.key);
    });
    $("list").addEventListener("keydown", (e) => {
      const c = e.target.closest(".card");
      if (c && e.target === c && (e.key === "Enter" || e.key === " ")) { e.preventDefault(); openDetail(c.dataset.key); }
    });
    $("more").addEventListener("click", (e) => {
      if (e.target.id === "more-btn") { shown += PAGE; renderList(); }
    });
    $("detail-back").addEventListener("click", closeDetail);
    $("detail").addEventListener("click", (e) => { if (e.target === $("detail")) closeDetail(); });
    $("detail-star").addEventListener("click", (e) => {
      const key = e.currentTarget.dataset.star;
      toggleFav(key);
      e.currentTarget.textContent = favs.has(key) ? "★ お気に入り" : "☆ お気に入り";
      e.currentTarget.setAttribute("aria-pressed", favs.has(key));
      render();
    });
    window.addEventListener("popstate", route);
    document.addEventListener("keydown", (e) => {
      if (e.key !== "Escape") return;
      if (!$("detail").hidden) closeDetail(); else if (sheet) closeSheet();
    });
  }

  function route() {
    const key = readHash();
    const conditions = hashFor();
    if (conditions !== lastConditions) { lastConditions = conditions; render(); }
    if (key) showDetail(key, Filter.hitFor(db, key));
    else if (!$("detail").hidden) { pushed = false; hideDetail(); }
  }

  async function start() {
    try {
      const index = await (await fetch("data/index.json")).json();
      db = Filter.load(index);
      areaNames = new Map(index.areas);
      $("updated").textContent = index.updated ? `更新 ${index.updated.slice(5).replace("-", "/")}` : "";
    } catch (e) {
      $("loading").textContent = "物件データを読み込めませんでした。再読み込みしてください。";
      return;
    }
    wire();
    route();
    $("loading").hidden = true;
  }

  start();
})();
