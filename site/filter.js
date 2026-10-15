// Search rules for the site: load the index, match, sort, count choices.
// Loaded by the page and, in tests, by Node (tests/test_site.py runs these rules on built indexes).
(function (root, factory) {
  if (typeof module === "object" && module.exports) module.exports = factory();
  else root.Filter = factory();
})(typeof self !== "undefined" ? self : this, function () {
  "use strict";

  const emptyQuery = () => ({
    text: "", types: [], areas: [], stations: [], walk: null, priceMin: null, priceMax: null, plan: null,
    sizeMin: null, landMin: null, ageMax: null, post1981: false, features: [], freehold: false,
    noCondition: false, newOnly: false, dropsOnly: false, since: null, sort: "new",
    south: false, top: false, upper: false, nofee: false, ready: false, tower: false, parking: false, feesMax: null,
  });
  const isCondo = (type) => type === "used_condo" || type === "new_condo";
  const isHouse = (type) => type === "used_house" || type === "new_house";

  // Tags the site now offers as conditions (the index no longer carries them, site.HIDDEN_TAGS).
  const SOUTH_TAGS = ["南向き", "全室南向き", "東南向き", "南西向き", "全室東南向き", "全室南西向き"];
  const READY_TAGS = ["即引渡可", "即引渡し可"], TOP_TAG = "最上階・上階なし";
  const TSUBO = 3.30578;  // ㎡ per 坪
  const LAND_TAGS = { 土地50坪以上: Math.round(50 * TSUBO), 土地100坪以上: Math.round(100 * TSUBO) };
  // A query from an older link: those tags -> their conditions (land: at least the plot size the tag meant).
  function upgrade(q) {
    const out = { ...q, features: [] };
    for (const f of q.features || []) {
      if (SOUTH_TAGS.includes(f)) out.south = true;
      else if (READY_TAGS.includes(f)) out.ready = true;
      else if (f === TOP_TAG) out.top = true;
      else if (f === "建築条件なし") out.noCondition = true;
      else if (LAND_TAGS[f]) out.landMin = Math.max(out.landMin ?? 0, LAND_TAGS[f]);
      else out.features.push(f);
    }
    return out;
  }

  // NFKC (full-width -> half-width), lower case, hiragana -> katakana, no accents: "ぱーく", "ﾊﾟｰｸ" and "パーク"
  // all match, and so do "jiyugaoka" and "Jiyūgaoka".
  // (Accents are only stripped from strings that can have them: Latin, Greek, Cyrillic; kana's dakuten stay.)
  const normalize = (s) => {
    const k = (s || "").normalize("NFKC").toLowerCase().replace(/[ぁ-ゖ]/g, (c) => String.fromCharCode(c.charCodeAt(0) + 0x60));
    return /[\u00c0-\u1fff]/.test(k) ? k.normalize("NFD").replace(/[\u0300-\u036f]/g, "").normalize("NFC") : k;
  };
  // A station name as typed: "Futako-Tamagawa", "futakotamagawa" and "ふたこたまがわ" are the same.
  const stationKey = (s) => normalize(s).replace(/[\s\-・]/g, "");

  // A listing's tags in the index: a bit string, 6 bits per character, bit k = index.features[k] (see site._mask).
  const B64 = Object.fromEntries([..."ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"].map((ch, v) => [ch, v]));

  // A listing's photo as SUUMO's path (or the URL), from what the builder stored (site._image): a number is the
  // usual path, "dir/sub/n/file" a shortened one. Items keep what's stored (photo 0 is one); the page asks for the
  // path when it shows the photo.
  function photo(v, id) {
    if (typeof v === "number") v = `030/N010000/${id.slice(-3)}/${String(v).padStart(4, "0")}.jpg`;
    if (typeof v !== "string" || v.startsWith("http") || v.startsWith("gazo/")) return v;
    const [a, b, c, file] = v.split("/");
    return `gazo/bukken/${a}/${b}/img/${c}/${id}/${id}_${file}`;
  }

  // ---------- the store: each prefecture's listings as columns, a listing as a number ----------
  // A loaded index (a "part") keeps its listings in typed arrays, one per field (NaN or -1: none). A listing is a
  // handle: its part's number (one per load) × 2^20 + its row. Lists of listings (results, a map view) are
  // Int32Arrays of handles. item(h) makes the plain object the page shows (a few thousand are cached); get.<field>(h)
  // reads one field.
  const SHIFT = 20, ROWS = 1 << SHIFT, MASK = ROWS - 1, MAX_PARTS = 2048;
  const parts = [];                // part number -> the part (null once released)
  const NO_ACCESS = -0x80000000;   // a station's access when none is known
  const EMPTY = [];
  const nul = (v) => (v === v ? v : null);  // NaN -> null

  // opts.places: area name -> English name, for text search ("shibuya" finds 渋谷区).
  // An index older than this file may lack columns: they read as none.
  function load(index, opts = {}) {
    const c = index.columns, n = c.id.length, no = parts.length;
    if (no >= MAX_PARTS || n > ROWS) throw new RangeError("Filter.load: too many listings");
    const col = (k) => c[k] || EMPTY;
    const nums = (k, T = Float64Array) => { const v = col(k), out = new T(n); for (let i = 0; i < n; i++) out[i] = v[i] ?? NaN; return out; };
    const ids = (k) => { const v = col(k), out = new Int32Array(n); for (let i = 0; i < n; i++) out[i] = v[i] ?? -1; return out; };
    const idn = new Float64Array(n), priceHi = new Float64Array(n), others = new Int32Array(n), firstSeen = new Int32Array(n);
    const cId = c.id, cMax = col("priceMax"), cPrice = col("price"), cOthers = col("others"), cFirst = col("firstSeen");
    for (let i = 0, id = 0; i < n; i++) {
      idn[i] = id += cId[i];
      priceHi[i] = cMax[i] ?? cPrice[i] ?? NaN;
      others[i] = cOthers[i] || 0;
      firstSeen[i] = cFirst[i] || 0;
    }
    const dropAt = new Float64Array(n), prevPrice = new Float64Array(n), cDrop = col("drop");
    for (let i = 0; i < n; i++) { const d = cDrop[i]; dropAt[i] = d?.[0] ?? NaN; prevPrice[i] = d?.[1] ?? NaN; }
    // stations: row i's are stId/stAcc[stOff[i]..stOff[i + 1]] (access: minutes on foot; -minutes by bus; 1000 + km × 10 by car)
    const cSt = col("stations"), cAcc = col("access");
    let total = 0;
    for (let i = 0; i < n; i++) total += cSt[i]?.length || 0;
    const stOff = new Int32Array(n + 1), stId = new Int32Array(total), stAcc = new Int32Array(total);
    for (let i = 0, j = 0; i < n; i++) {
      const s = cSt[i], a = cAcc[i];
      if (s) for (let k = 0; k < s.length; k++, j++) { stId[j] = s[k]; stAcc[j] = a?.[k] ?? NO_ACCESS; }
      stOff[i + 1] = j;
    }
    // tags: W 32-bit words per row, bit k = index.features[k]
    const W = Math.ceil(index.features.length / 32), tags = new Uint32Array(n * W), cTags = col("features");
    for (let i = 0; i < n; i++) {
      const s = cTags[i];
      if (s) for (let k = 0; k < s.length; k++) {
        const v = B64[s[k]];
        for (let b = 0; b < 6; b++) if (v & (1 << b) && k * 6 + b < W * 32) tags[i * W + ((k * 6 + b) >>> 5)] |= 1 << ((k * 6 + b) & 31);
      }
    }
    // the bus stop (one per listing at most): its name in a table of its own (for text search) and the minutes
    const stops = new Map(), stopId = new Int32Array(n), stopWalk = new Float32Array(n), cStop = col("stop");
    for (let i = 0; i < n; i++) {
      const s = cStop[i];
      if (!s) { stopId[i] = -1; stopWalk[i] = NaN; continue; }
      if (!stops.has(s[0])) stops.set(s[0], stops.size);
      stopId[i] = stops.get(s[0]);
      stopWalk[i] = s[1] ?? NaN;
    }
    // photos: the usual number, or a path or URL (in a list of their own, as -2 - its position); -1 none
    const image = new Int32Array(n), imageText = [], cImage = col("image");
    for (let i = 0; i < n; i++) {
      const v = cImage[i];
      image[i] = typeof v === "number" ? v : v == null ? -1 : -2 - (imageText.push(v) - 1);
    }
    const area = ids("area"), town = ids("town");
    // towns: coordinates, and the name without the prefecture (one string per town)
    const T = index.towns.length, townLat = new Float64Array(T).fill(NaN), townLng = new Float64Array(T).fill(NaN);
    const townName = new Array(T).fill(null);
    for (let t = 0; t < T; t++) { townLat[t] = index.towns[t][1] ?? NaN; townLng[t] = index.towns[t][2] ?? NaN; }
    for (let i = 0; i < n; i++) {
      const t = town[i];
      if (t >= 0 && townName[t] === null) townName[t] = index.towns[t][0].slice(index.areas[area[i]][2].length);
    }
    const cols = {
      idn, type: Uint8Array.from(col("type")), area, price: nums("price"), priceHi, plan: nums("plan", Float32Array),
      size: nums("size"), land: nums("land"), age: nums("age", Float32Array), flags: Int32Array.from(col("flags"), (v) => v || 0),
      others, newAt: nums("newAt"), dropAt, prevPrice, firstSeen, unit: nums("unit", Float32Array), town, name: ids("name"),
      layout: ids("layout"), image, fees: nums("fees", Float32Array), stopId, stopWalk, stOff, stId, stAcc, tags,
    };
    index.columns = null;  // the typed columns hold everything now; free the parsed JSON
    const base = no * ROWS, gone = index.flags.gone || 0;  // ended listings kept because saved: in the saved list, never in search
    let shown = 0;
    for (let i = 0; i < n; i++) if (!(cols.flags[i] & gone)) shown++;
    const items = new Int32Array(shown);
    for (let i = 0, k = 0; i < n; i++) if (!(cols.flags[i] & gone)) items[k++] = base + i;
    const part = { no, base, n, W, c: cols, index, items, flags: index.flags, places: opts.places || null,
      townLat, townLng, townName, imageText, stops: [...stops.keys()], parts: null,
      // station -> [kana, English] (null for bus stops: kanji only)
      stationNames: new Map(index.stations.map((s, i) => [s, index.stationNames?.[i] || null])) };
    part.parts = [part];  // the prefectures searched: one here, several after combine
    parts[no] = part;
    return part;
  }

  // Several prefectures' indexes (each loaded with `load`) searched as one.
  function combine(dbs) {
    const all = (f) => dbs.flatMap(f);
    const index = { types: [...new Set(all((d) => d.index.types))], areas: all((d) => d.index.areas),
      stations: [...new Set(all((d) => d.index.stations))], towns: all((d) => d.index.towns), flags: dbs[0].flags };
    const items = new Int32Array(dbs.reduce((n, d) => n + d.items.length, 0));
    dbs.reduce((at, d) => (items.set(d.items, at), at + d.items.length), 0);
    return { index, items, flags: index.flags, stationNames: new Map(all((d) => [...d.stationNames])), parts: all((d) => d.parts) };
  }

  // Forgets loaded indexes (replaced by newer data): their handles read as nothing from now on.
  function release(db) {
    for (const p of db.parts) parts[p.no] = null;
    for (const h of [...cache.keys()]) if (!parts[h >>> SHIFT]) cache.delete(h);
  }

  // Every listing of db, the ended ones kept because saved included (db.items: those searched).
  function every(db) {
    const out = new Int32Array(db.parts.reduce((n, p) => n + p.n, 0));
    let k = 0;
    for (const p of db.parts) for (let r = 0; r < p.n; r++) out[k++] = p.base + r;
    return out;
  }

  // A listing by its key ("used_condo:12345"): its handle, or -1. Each type's rows are one run of ascending ids (as
  // site.build_index writes them): a binary search; an index written otherwise gets a map.
  function find(db, key) {
    const at = key.indexOf(":"), type = key.slice(0, at), id = +key.slice(at + 1);
    for (const p of db.parts) {
      const t = p.index.types.indexOf(type), k = (p.keys ??= keyed(p));
      if (t < 0 || parts[p.no] !== p) continue;  // (released)
      if (k.map) { const r = k.map.get(`${t}:${id}`); if (r != null) return p.base + r; continue; }
      let lo = k.start[t], hi = k.end[t];
      while (lo < hi) { const m = (lo + hi) >> 1; if (p.c.idn[m] < id) lo = m + 1; else hi = m; }
      if (lo < k.end[t] && p.c.idn[lo] === id) return p.base + lo;
    }
    return -1;
  }
  function keyed(p) {
    const { type, idn } = p.c, start = new Int32Array(p.index.types.length), end = new Int32Array(p.index.types.length);
    for (let r = 0; r < p.n; r++) {
      const t = type[r];
      if (end[t] === 0) { start[t] = r; end[t] = r + 1; } else if (end[t] === r && idn[r] > idn[r - 1]) end[t] = r + 1;
      else return { map: new Map(Array.from({ length: p.n }, (_, i) => [`${type[i]}:${idn[i]}`, i])) };
    }
    return { start, end };
  }

  // A station's [name, walk minutes, bus minutes, car km] (nulls when not) from its access value.
  const walkOf = (a) => (a >= 0 && a < 1000 ? a : null);
  const station = (p, j) => {
    const a = p.c.stAcc[j];
    return [p.index.stations[p.c.stId[j]], walkOf(a), a < 0 && a !== NO_ACCESS ? -a : null, a >= 1000 ? (a - 1000) / 10 : null];
  };

  // The listing as an object (the page's cards, popups and listing view): null when its index was released.
  const cache = new Map(), CACHE = 3000;
  function item(h) {
    let it = cache.get(h);
    if (it) return it;
    const p = parts[h >>> SHIFT], r = h & MASK;
    if (!p || r >= p.n) return null;
    const c = p.c, ix = p.index, type = ix.types[c.type[r]], area = ix.areas[c.area[r]], id = String(c.idn[r]);
    const stations = [];
    for (let j = c.stOff[r]; j < c.stOff[r + 1]; j++) stations.push(station(p, j));
    const t = c.town[r], age = c.age[r], newAt = c.newAt[r], img = c.image[r], fees = c.fees[r];
    it = {
      h, id, idn: c.idn[r], type, key: type + ":" + id, area: area[0], areaName: area[1],
      price: nul(c.price[r]), priceHi: nul(c.priceHi[r]), plan: nul(c.plan[r]), size: nul(c.size[r]), land: nul(c.land[r]),
      age: age === age ? Math.floor(age / 12) : null, ageMonths: nul(age), stations,
      stop: c.stopId[r] >= 0 ? [p.stops[c.stopId[r]], nul(c.stopWalk[r])] : null, flags: c.flags[r], others: c.others[r],
      newAt: nul(newAt), newDate: newAt ? Math.floor(newAt / 100) : null, dropAt: nul(c.dropAt[r]), prevPrice: nul(c.prevPrice[r]),
      firstSeen: c.firstSeen[r], unit: nul(c.unit[r]), town: t >= 0 ? p.townName[t] : null,
      lat: t >= 0 ? nul(p.townLat[t]) : null, lng: t >= 0 ? nul(p.townLng[t]) : null,
      name: ix.names[c.name[r]] ?? "", layout: ix.layouts[c.layout[r]] ?? "",
      image: img >= 0 ? img : img === -1 ? null : p.imageText[-2 - img],
      fees: fees === fees ? fees * 100 : null,  // condos' monthly fees, yen (the index stores ¥100)
    };
    if (cache.size >= CACHE) cache.delete(cache.keys().next().value);
    cache.set(h, it);
    return it;
  }
  // One field of a listing without making its object (null: none, or released).
  const reader = (fn) => (h) => { const p = parts[h >>> SHIFT]; return p ? fn(p, h & MASK, p.c) : null; };
  const get = {
    key: reader((p, r, c) => `${p.index.types[c.type[r]]}:${c.idn[r]}`),
    type: reader((p, r, c) => p.index.types[c.type[r]]),
    area: reader((p, r, c) => p.index.areas[c.area[r]][0]),
    price: reader((p, r, c) => nul(c.price[r])),
    unit: reader((p, r, c) => nul(c.unit[r])),
    ageMonths: reader((p, r, c) => nul(c.age[r])),
    newDate: reader((p, r, c) => (c.newAt[r] ? Math.floor(c.newAt[r] / 100) : null)),
    flags: reader((p, r, c) => c.flags[r]),
    lat: reader((p, r, c) => (c.town[r] >= 0 ? nul(p.townLat[c.town[r]]) : null)),
    lng: reader((p, r, c) => (c.town[r] >= 0 ? nul(p.townLng[c.town[r]]) : null)),
    town: reader((p, r, c) => (c.town[r] >= 0 ? p.townName[c.town[r]] : null)),
    // its town as a number (towns of different prefectures differ)
    townKey: reader((p, r, c) => (c.town[r] >= 0 ? p.base + c.town[r] : null)),
  };
  // The loaded index a listing is in (its .items: that prefecture's listings).
  const partOf = (h) => parts[h >>> SHIFT] || null;
  // Each listing's position in a list: h -> position, -1 when it isn't there (a row per listing of the prefectures in it).
  function positions(list) {
    const at = new Map();  // part number -> per row its position + 1
    for (let i = 0; i < list.length; i++) {
      const h = list[i], no = h >>> SHIFT;
      let a = at.get(no);
      if (!a) at.set(no, (a = new Int32Array(parts[no]?.n || 0)));
      a[h & MASK] = i + 1;
    }
    return (h) => (at.get(h >>> SHIFT)?.[h & MASK] ?? 0) - 1;
  }

  // A tag's position in its prefecture's table (index.features), null when that index doesn't have it.
  const tagPos = (p, f) => (p.tagPos ??= new Map(p.index.features.map((t, k) => [t, k]))).get(f) ?? null;
  // Per position of a table, whether it's one of the chosen values: marks(["a", "b", "c"], ["c"]) -> [0, 0, 1]
  const marksOf = (table, chosen) => Uint8Array.from(table, (v) => (chosen.includes(v) ? 1 : 0));

  // Text search: a word matches a listing when it is part of its name, town, area (or its English name, from load's
  // opts.places), layout, a station (with 駅, kana or English) or its bus stop. Each prefecture's tables are
  // normalized once, the first time a text search runs; a search marks the table entries containing each word, and
  // listings are tested by their positions in those tables.
  function textTables(p) {
    if (p.textTables) return p.textTables;
    const ix = p.index, norm = (v) => (v ? normalize(v) : null);
    return (p.textTables = {
      names: ix.names.map(norm), towns: p.townName.map(norm), layouts: ix.layouts.map(norm),
      areas: ix.areas.map((a) => [norm(a[1]), norm(p.places?.[a[1]])].filter(Boolean)),
      stations: ix.stations.map((st, k) => [st + "駅", ...(ix.stationNames?.[k] || [])].filter(Boolean).map(normalize)),
      stops: p.stops.map(norm),
    });
  }
  const marks = (list, w) => Uint8Array.from(list, (v) => (v && v.includes(w) ? 1 : 0));
  const marksAny = (list, w) => Uint8Array.from(list, (vs) => (vs.some((v) => v.includes(w)) ? 1 : 0));
  let textCache = [null, new Map()];  // the last text searched, and per prefecture the marks of each of its words
  function wordMarks(p, v) {
    if (textCache[0] !== v) textCache = [v, new Map()];
    let m = textCache[1].get(p);
    if (!m) {
      const t = textTables(p);
      textCache[1].set(p, m = words(v).map((w) => ({ names: marks(t.names, w), towns: marks(t.towns, w),
        layouts: marks(t.layouts, w), areas: marksAny(t.areas, w), stations: marksAny(t.stations, w), stops: marks(t.stops, w) })));
    }
    return m;
  }
  function textTest(p, v) {
    const ms = wordMarks(p, v), { name, town, area, layout, stopId, stOff, stId } = p.c;
    return (r) => {
      for (const m of ms) {
        const nm = name[r], tw = town[r], ly = layout[r], sp = stopId[r];
        if ((nm >= 0 && m.names[nm]) || (tw >= 0 && m.towns[tw]) || m.areas[area[r]] || (ly >= 0 && m.layouts[ly])
          || (sp >= 0 && m.stops[sp])) continue;
        let hit = false;
        for (let j = stOff[r]; j < stOff[r + 1]; j++) if (m.stations[stId[j]]) { hit = true; break; }
        if (!hit) return false;
      }
      return true;
    };
  }

  // The shortest walk from a row to a station (one of the chosen ones: marks of the prefecture's stations, or null:
  // any), NaN when none is on foot.
  function walkTo(p, r, chosen) {
    const { stOff, stId, stAcc } = p.c;
    let best = NaN;
    for (let j = stOff[r]; j < stOff[r + 1]; j++) {
      const a = stAcc[j];
      if (a >= 0 && a < 1000 && (!chosen || chosen[stId[j]]) && !(a >= best)) best = a;
    }
    return best;
  }
  const chosenStations = (p, q) => (q.stations.length ? marksOf(p.index.stations, q.stations) : null);

  const STATION_WORDS = new Set(["station", "sta", "sta.", "駅"]);  // "Shibuya Station" finds what "Shibuya" does
  let typed = [null, []];  // the last text searched and its words: split once per search, not once per listing
  const words = (v) => (typed[0] === v ? typed : (typed = [v, normalize(v).split(/\s+/).filter((w) => w && !STATION_WORDS.has(w))]))[1];

  // A flag bit of a prefecture's index; 0 when the index predates it (a cached old index after a deploy): nothing has it.
  const bit = (p, name) => p.flags[name] || 0;
  const ALWAYS = () => true, NEVER = () => false;
  const flagTest = (p, name, set) => {
    const b = bit(p, name), flags = p.c.flags;
    if (!b) return set ? NEVER : ALWAYS;
    return set ? (r) => (flags[r] & b) !== 0 : (r) => (flags[r] & b) === 0;
  };
  const condoTypes = (p) => Uint8Array.from(p.index.types, (t) => (isCondo(t) ? 1 : 0));

  // One check per condition: (prefecture, value, query) -> a test of its rows (row -> passes). Only set values are
  // checked (see conditions).
  const CHECKS = {
    types: (p, v) => { const m = marksOf(p.index.types, v), type = p.c.type; return (r) => m[type[r]] === 1; },
    areas: (p, v) => { const m = marksOf(p.index.areas.map((a) => a[0]), v), area = p.c.area; return (r) => m[area[r]] === 1; },
    stations: (p, v) => {
      const m = marksOf(p.index.stations, v), { stOff, stId } = p.c;
      return (r) => { for (let j = stOff[r]; j < stOff[r + 1]; j++) if (m[stId[j]]) return true; return false; };
    },
    walk: (p, v, q) => { const chosen = chosenStations(p, q); return (r) => walkTo(p, r, chosen) <= v; },
    priceMax: (p, v) => { const a = p.c.price; return (r) => a[r] <= v; },
    priceMin: (p, v) => { const a = p.c.priceHi; return (r) => a[r] >= v; },
    landMin: (p, v) => { const a = p.c.land; return (r) => a[r] >= v; },
    features: (p, v) => {
      const pos = v.map((f) => tagPos(p, f));
      if (pos.includes(null)) return NEVER;
      const { tags } = p.c, W = p.W, word = pos.map((k) => k >>> 5), mask = pos.map((k) => 1 << (k & 31));
      return (r) => { for (let i = 0; i < pos.length; i++) if (!(tags[r * W + word[i]] & mask[i])) return false; return true; };
    },
    freehold: (p) => flagTest(p, "leasehold", false),
    noCondition: (p) => flagTest(p, "conditional", false),
    newOnly: (p) => flagTest(p, "new", true),
    dropsOnly: (p) => flagTest(p, "dropped", true),
    // new or cheaper since an hour (YYYYMMDDHH): "since your last visit"
    since: (p, v) => { const { newAt, dropAt } = p.c; return (r) => newAt[r] > v || dropAt[r] > v; },
    // flags of the listing (top and upper: condos only; parking: houses and land only)
    ...Object.fromEntries(["south", "top", "upper", "nofee", "ready", "tower", "parking"].map((f) => [f, (p) => flagTest(p, f, true)])),
    // monthly fees (yen) of condos; houses and land have none and pass
    feesMax: (p, v) => { const condo = condoTypes(p), { type, fees } = p.c; return (r) => !condo[type[r]] || fees[r] * 100 <= v; },
    text: (p, v) => textTest(p, v),
  };
  // Conditions on the building: they don't apply to land when 土地 is chosen; otherwise land fails them.
  const BUILDING = {
    plan: (p, v) => { const a = p.c.plan; return (r) => a[r] >= v; },
    sizeMin: (p, v) => { const a = p.c.size; return (r) => a[r] >= v; },
    ageMax: (p, v) => { const a = p.c.age; return (r) => Math.floor(a[r] / 12) <= v; },
    post1981: (p) => flagTest(p, "post1981", true),
  };
  const GROUP = { walk: "stations" };  // walk depends on the chosen stations: count them together
  // The yes/no conditions (FLAGS) as bits of the index: [flag name, passes when set (true) or when not set (false)]
  const FLAG_BITS = { freehold: ["leasehold", false], noCondition: ["conditional", false], newOnly: ["new", true],
    dropsOnly: ["dropped", true], post1981: ["post1981", true], south: ["south", true], top: ["top", true],
    upper: ["upper", true], nofee: ["nofee", true], ready: ["ready", true], tower: ["tower", true],
    parking: ["parking", true] };
  const FLAGS = Object.keys(FLAG_BITS), FLAG_AT = new Map(FLAGS.map((f, k) => [f, k]));
  const ALL = [...Object.entries(CHECKS).map(([d, check]) => [d, check, false]),
    ...Object.entries(BUILDING).map(([d, check]) => [d, check, true])];

  const isSet = (v) => v != null && v !== false && v !== "" && !(Array.isArray(v) && !v.length);
  // The query's conditions that are set, as [name, check, on the building]: the others pass every listing.
  const conditions = (q) => ALL.filter(([d]) => isSet(q[d]));

  // The conditions compiled for each prefecture (made the first time a listing of it is tested): its tests, which
  // are on the building, each one's dimension for faceting, and whether its land is exempt (土地 chosen).
  function compiler(q, conds) {
    const made = new Map(), landChosen = q.types.includes("land");
    return (p) => {
      let c = made.get(p);
      if (!c) {
        const landId = p.index.types.indexOf("land");
        made.set(p, c = { tests: conds.map(([d, check]) => check(p, q[d], q)), building: conds.map((x) => x[2]),
          dims: conds.map(([d]) => GROUP[d] || d), landId, exempt: landChosen && landId >= 0, type: p.c.type });
      }
      return c;
    };
  }
  // Whether a row passes the compiled conditions (stops at the first failure).
  function passes(c, r) {
    const tests = c.tests, exempt = c.exempt && c.type[r] === c.landId;
    for (let k = 0; k < tests.length; k++) if (!(exempt && c.building[k]) && !tests[k](r)) return false;
    return true;
  }
  // The conditions a row fails, by dimension: how many (2 at most are counted: that's all faceting needs); the
  // first goes into out[0].
  function failing(c, r, out) {
    const tests = c.tests, land = c.landId >= 0 && c.type[r] === c.landId, exempt = c.exempt && land;
    let n = 0;
    for (let k = 0; k < tests.length; k++) {
      if ((c.building[k] && exempt) || tests[k](r)) continue;
      // land isn't a building: choosing 土地 is what would let it through
      const dim = c.building[k] && land ? "types" : c.dims[k];
      if (n === 0) { out[0] = dim; n = 1; } else if (out[0] !== dim) return 2;
    }
    return n;
  }
  // Calls fn(c, r, h) for each listing (handles of any prefectures), with the conditions compiled for its prefecture.
  function each(items, at, fn) {
    let no = -1, c = null;
    for (let i = 0; i < items.length; i++) {
      const h = items[i];
      if (h >>> SHIFT !== no) { no = h >>> SHIFT; c = parts[no] ? at(parts[no]) : null; }
      if (c) fn(c, h & MASK, h);
    }
  }

  const matches = (db, h, q) => { const p = parts[h >>> SHIFT]; return !!p && passes(compiler(q, conditions(q))(p), h & MASK); };

  const onlyLand = (q) => q.types.length > 0 && q.types.every((t) => t === "land");
  const onlyHouses = (q) => q.types.length > 0 && q.types.every(isHouse);
  const orLast = (v) => (v === v ? v : Infinity);  // missing (NaN) last
  // The sort's keys for a prefecture's rows: [row -> number], compared in turn (ties: the results' order).
  function sortKeys(p, q) {
    const c = p.c, { idn, price, priceHi, type, size, land, age, unit, fees, prevPrice, newAt, firstSeen } = c;
    const typeIs = (f) => Uint8Array.from(p.index.types, (t) => (f(t) ? 1 : 0)), byId = (r) => idn[r];
    switch (q.sort) {
      case "price_asc": return [(r) => orLast(price[r]), byId];
      case "price_desc": return [(r) => -(priceHi[r] || 0), byId];
      // land after the buildings, unless only land is chosen
      case "size_desc": {
        const last = typeIs((t) => t === "land" && !onlyLand(q));
        return [(r) => last[type[r]], (r) => -(size[r] || land[r] || 0), byId];
      }
      case "walk_asc": { const chosen = chosenStations(p, q); return [(r) => orLast(walkTo(p, r, chosen)), byId]; }
      case "age_asc": return [(r) => orLast(age[r]), byId];
      // a house's price per ㎡ of building isn't comparable with a condo's or a plot's: houses last unless only
      // houses are chosen
      case "unit_asc": { const last = typeIs((t) => isHouse(t) && !onlyHouses(q)); return [(r) => (last[type[r]] ? Infinity : orLast(unit[r])), byId]; }
      case "fees_asc": { const condo = condoTypes(p); return [(r) => (condo[type[r]] ? orLast(fees[r] * 100) : Infinity), byId]; }
      case "drop_desc": return [(r) => (prevPrice[r] && price[r] ? price[r] / prevPrice[r] : Infinity), byId];  // biggest cut first
      // newest first (missing last), then the newest listed, then the highest id
      default: return [(r) => (newAt[r] ? -Math.floor(newAt[r] / 100) : Infinity), (r) => (firstSeen[r] ? -firstSeen[r] : Infinity), (r) => -idn[r]];
    }
  }

  // The matching listings (of all, or of the given handles), sorted: an Int32Array of handles.
  function search(db, q, items = db.items) {
    const hits = new Int32Array(items.length);
    let n = 0;
    each(items, compiler(q, conditions(q)), (c, r, h) => { if (passes(c, r)) hits[n++] = h; });
    if (n < 2) return hits.slice(0, n);
    // sort keys in columns of numbers, and a stable sort of positions
    const keyFns = new Map(), keyOf = (p) => keyFns.get(p) || keyFns.set(p, sortKeys(p, q)).get(p);
    const width = keyOf(parts[hits[0] >>> SHIFT]).length, keys = Array.from({ length: width }, () => new Float64Array(n));
    for (let i = 0, no = -1, fns = null; i < n; i++) {
      const h = hits[i];
      if (h >>> SHIFT !== no) { no = h >>> SHIFT; fns = keyOf(parts[no]); }
      for (let l = 0; l < width; l++) keys[l][i] = fns[l](h & MASK);
    }
    const [a0, a1, a2] = keys, order = new Int32Array(n);
    for (let i = 0; i < n; i++) order[i] = i;
    const cmp = (x, y, a) => (a[x] < a[y] ? -1 : a[x] > a[y] ? 1 : 0);
    order.sort(width === 2 ? (x, y) => cmp(x, y, a0) || cmp(x, y, a1) || x - y
      : (x, y) => cmp(x, y, a0) || cmp(x, y, a1) || cmp(x, y, a2) || x - y);
    const out = new Int32Array(n);
    for (let i = 0; i < n; i++) out[i] = hits[order[i]];
    return out;
  }

  function count(db, q) {
    let n = 0;
    each(db.items, compiler(q, conditions(q)), (c, r) => { if (passes(c, r)) n++; });
    return n;
  }

  // The listings with coordinates on a grid of GRID° cells, so those in a box (the map's view, at every pan) are
  // found without testing every one: within([south, west, north, east]) -> the handles in it, in the list's order.
  const GRID = 0.05;
  function spatial(items) {
    const n = items.length, la = new Float64Array(n).fill(NaN), lo = new Float64Array(n).fill(NaN);  // (NaN = none)
    let s = Infinity, w = Infinity, north = -Infinity, e = -Infinity, placed = 0;
    for (let i = 0, no = -1, p = null; i < n; i++) {
      const h = items[i];
      if (h >>> SHIFT !== no) { no = h >>> SHIFT; p = parts[no]; }
      const t = p ? p.c.town[h & MASK] : -1;
      if (t < 0) continue;
      const y = p.townLat[t], x = p.townLng[t];
      la[i] = y;
      lo[i] = x;
      if (y !== y || x !== x) continue;
      placed++;
      if (y < s) s = y;
      if (y > north) north = y;
      if (x < w) w = x;
      if (x > e) e = x;
    }
    if (!placed) return { within: () => new Int32Array(0) };
    const rows = Math.floor((north - s) / GRID) + 1, cols = Math.floor((e - w) / GRID) + 1;
    const row = (lat) => Math.floor((lat - s) / GRID), col = (lng) => Math.floor((lng - w) / GRID);
    // positions sorted by cell (a counting sort: each cell's listings in their order), cell k at start[k]..start[k + 1]
    const cell = new Int32Array(n).fill(-1), start = new Int32Array(rows * cols + 1);
    for (let i = 0; i < n; i++) if (la[i] === la[i] && lo[i] === lo[i]) start[(cell[i] = row(la[i]) * cols + col(lo[i])) + 1]++;
    for (let k = 0; k < rows * cols; k++) start[k + 1] += start[k];
    const pos = new Int32Array(placed), lat = new Float64Array(placed), lng = new Float64Array(placed), next = start.slice();
    for (let i = 0; i < n; i++) {
      if (cell[i] < 0) continue;
      const j = next[cell[i]]++;
      pos[j] = i;
      lat[j] = la[i];
      lng[j] = lo[i];
    }
    const found = new Int32Array(placed);
    let seen = null;
    return {
      within([bs, bw, bn, be]) {
        const r0 = Math.max(0, row(bs)), r1 = Math.min(rows - 1, row(bn)), c0 = Math.max(0, col(bw)), c1 = Math.min(cols - 1, col(be));
        let m = 0;
        for (let r = r0; r <= r1; r++) {
          for (let j = start[r * cols + c0], end = c1 >= c0 ? start[r * cols + c1 + 1] : j; j < end; j++) {
            if (lat[j] >= bs && lat[j] <= bn && lng[j] >= bw && lng[j] <= be) found[m++] = pos[j];
          }
        }
        const out = new Int32Array(m);
        if (m * 8 < n) {  // a few: sorted back into the list's order
          const picked = found.subarray(0, m).sort();
          for (let k = 0; k < m; k++) out[k] = items[picked[k]];
        } else {  // most of them: marked, then taken in order
          seen ??= new Uint8Array(n);
          for (let k = 0; k < m; k++) seen[found[k]] = 1;
          for (let i = 0, k = 0; k < m; i++) if (seen[i]) { seen[i] = 0; out[k++] = items[i]; }
        }
        return out;
      },
    };
  }

  // Threshold conditions as (value of a row, comparison): "max" choices pass rows whose value is at most the
  // choice, "min" ones rows whose value is at least it; NaN: the row passes no choice, true: every choice.
  // (prefecture, query) -> row -> value
  const THRESHOLDS = {
    priceMax: ["max", (p) => { const a = p.c.price; return (r) => a[r]; }],
    priceMin: ["min", (p) => { const a = p.c.priceHi; return (r) => a[r]; }],
    plan: ["min", (p) => { const a = p.c.plan; return (r) => a[r]; }],
    sizeMin: ["min", (p) => { const a = p.c.size; return (r) => a[r]; }],
    landMin: ["min", (p) => { const a = p.c.land; return (r) => a[r]; }],
    ageMax: ["max", (p) => { const a = p.c.age; return (r) => Math.floor(a[r] / 12); }],
    walk: ["max", (p, q) => { const chosen = chosenStations(p, q); return (r) => walkTo(p, r, chosen); }],
    feesMax: ["max", (p) => { const condo = condoTypes(p), { type, fees } = p.c; return (r) => (condo[type[r]] ? fees[r] * 100 : true); }],
  };
  const sortedChoices = new WeakMap();  // choice list -> its distinct values ascending
  function ascending(values) {
    let s = sortedChoices.get(values);
    if (!s) sortedChoices.set(values, s = [...new Set(values)].sort((a, b) => a - b));
    return s;
  }
  // Position of the first value >= x (max) or the last value <= x (min) in ascending values (-1 / length: none).
  // (A scan: choice lists are short.)
  function bucketMax(values, x) {
    let i = 0;
    while (i < values.length && values[i] < x) i++;
    return i;
  }
  function bucketMin(values, x) {
    let i = values.length - 1;
    while (i >= 0 && values[i] > x) i--;
    return i;
  }

  // Counts per position of a table (Int32Array) plus the order positions were first counted in, so the result keeps
  // the order a plain object filled listing by listing would have.
  class Tally {
    constructor(n) { this.n = new Int32Array(n); this.order = []; }
    add(k) { if (this.n[k]++ === 0) this.order.push(k); }
    into(out, name) { for (const k of this.order) out[name(k)] = (out[name(k)] || 0) + this.n[k]; }
  }

  // Counts for every choice of every condition, in one pass: a listing that fails no condition counts for
  // all choices it satisfies; one that fails exactly one condition counts for that condition's choices.
  // choices: {condition: [values]} for thresholds (priceMax, plan, walk...); sets are counted by value.
  function facets(db, q, choices) {
    const out = { total: 0, types: {}, areas: {}, stations: {}, features: {} };
    const dims = [];  // {d, group, kind, max, values (and as numbers), hist}
    for (const d in choices) {
      const t = THRESHOLDS[d], values = ascending(choices[d]);
      dims.push({ d, group: GROUP[d] || d, kind: t ? t[0] : null, max: t?.[0] === "max", values, nums: Float64Array.from(values),
        hist: new Int32Array(values.length) });
    }
    const flagN = new Int32Array(FLAGS.length);
    // per prefecture: its compiled conditions, tallies, flags as bit tests (bits, and whether a flag passes when set or
    // when not: freehold, noCondition), and per dimension the value of a row (or, without a known shape, a test per choice)
    const conds = conditions(q), compiled = compiler(q, conds), walk = q.walk;
    const tallies = new Map();
    const at = (p) => {
      let t = tallies.get(p);
      if (!t) {
        tallies.set(p, t = { p, c: compiled(p), areas: new Tally(p.index.areas.length), stations: new Tally(p.index.stations.length),
          tags: new Tally(p.index.features.length), types: new Tally(p.index.types.length),
          bits: Int32Array.from(FLAGS, (f) => bit(p, FLAG_BITS[f][0])), want: Uint8Array.from(FLAGS, (f) => (FLAG_BITS[f][1] ? 1 : 0)),
          values: dims.map((dim) => (dim.kind ? THRESHOLDS[dim.d][1](p, q)
            : dim.values.map((v) => (isSet(v) ? (CHECKS[dim.d] || BUILDING[dim.d])(p, v, q) : ALWAYS)))) });
      }
      return t;
    };
    const failed = [null];
    each(db.items, at, (t, r) => {
      const n = conds.length ? failing(t.c, r, failed) : 0;
      if (n > 1) return;
      const p = t.p, c = p.c, only = n ? failed[0] : null, free = only === null;
      if (free) {
        out.total++;
        // a tag counts when the listing has all chosen tags but it, i.e. (it is among its tags) all of them
        for (let w = 0, o = r * p.W; w < p.W; w++) {
          for (let x = c.tags[o + w]; x; x &= x - 1) t.tags.add(w * 32 + 31 - Math.clz32(x & -x));
        }
      }
      if (free || only === "types") t.types.add(c.type[r]);
      if (free || only === "areas") t.areas.add(c.area[r]);
      if (free || only === "stations") {
        for (let j = c.stOff[r]; j < c.stOff[r + 1]; j++) {
          const a = c.stAcc[j];
          if (walk == null || (a >= 0 && a < 1000 && a <= walk)) t.stations.add(c.stId[j]);
        }
      }
      for (let k = 0; k < dims.length; k++) {
        const dim = dims[k];
        if (!free && only !== dim.group) continue;
        const values = dim.values, hist = dim.hist;
        if (!dim.kind) {  // a condition without a known shape: check every choice
          const tests = t.values[k];
          for (let i = 0; i < values.length; i++) if (tests[i](r)) hist[i]++;
          continue;
        }
        const x = t.values[k](r);
        if (x === true) hist[dim.max ? 0 : values.length - 1]++;
        else if (x === x) {
          const b = dim.max ? bucketMax(dim.nums, x) : bucketMin(dim.nums, x);
          if (b >= 0 && b < values.length) hist[b]++;
        }
      }
      const flags = c.flags[r], bits = t.bits, want = t.want;
      if (free) { for (let k = 0; k < bits.length; k++) if (((flags & bits[k]) !== 0) === (want[k] === 1)) flagN[k]++; }
      else if (FLAG_AT.has(only)) { const k = FLAG_AT.get(only); if (((flags & bits[k]) !== 0) === (want[k] === 1)) flagN[k]++; }
    });
    for (let k = 0; k < FLAGS.length; k++) out[FLAGS[k]] = flagN[k];
    for (const [p, t] of tallies) {
      t.types.into(out.types, (k) => p.index.types[k]);
      t.areas.into(out.areas, (k) => p.index.areas[k][0]);
      t.stations.into(out.stations, (k) => p.index.stations[k]);
      t.tags.into(out.features, (k) => p.index.features[k]);
    }
    for (const { d, kind, values, hist } of dims) {
      const n = new Array(values.length);
      if (kind === "max") for (let i = 0, sum = 0; i < values.length; i++) n[i] = sum += hist[i];
      else if (kind === "min") for (let i = values.length - 1, sum = 0; i >= 0; i--) n[i] = sum += hist[i];
      else for (let i = 0; i < values.length; i++) n[i] = hist[i];
      const byValue = new Map(values.map((v, i) => [v, n[i]]));
      out[d] = Object.fromEntries(choices[d].map((v) => [v, byValue.get(v)]));
    }
    return out;
  }

  // The number of listings matching q, and q changed by each patch ({name: {condition: value}}), in one pass:
  // counts(db, q, {fresh: {newOnly: true}, cheaper: {dropsOnly: true}}) -> {total, fresh, cheaper}.
  function counts(db, q, patches = {}) {
    const names = Object.keys(patches), touched = new Set();
    for (const n of names) for (const d in patches[n]) touched.add(d);
    if (touched.has("stations")) touched.add("walk");             // walk is measured to the chosen stations
    if (touched.has("types")) for (const d in BUILDING) touched.add(d);  // land is exempt when 土地 is chosen
    const conds = conditions(q), shared = compiler(q, conds.filter(([d]) => !touched.has(d))),
      own = compiler(q, conds.filter(([d]) => touched.has(d)));
    const variants = names.map((n) => {
      const qq = { ...q, ...patches[n] };
      return compiler(qq, conditions(qq).filter(([d]) => touched.has(d)));
    });
    const out = { total: 0 }, n = new Int32Array(names.length);
    const made = new Map(), at = (p) => made.get(p) || made.set(p, [shared(p), own(p), variants.map((v) => v(p))]).get(p);
    each(db.items, at, ([s, o, vs], r) => {
      if (!passes(s, r)) return;
      if (passes(o, r)) out.total++;
      for (let k = 0; k < vs.length; k++) if (passes(vs[k], r)) n[k]++;
    });
    names.forEach((name, k) => { out[name] = n[k]; });
    return out;
  }


  // How well a station's kanji, kana or English name matches what was typed: 0 same, 1 starts with it,
  // 2 contains it, 3 no match.
  function stationMatch(db, s, typed) {
    const term = stationKey(typed);
    let best = 3;
    for (const n of [s, ...(db.stationNames.get(s) || [])]) {
      const k = n ? stationKey(n) : "";
      best = Math.min(best, k === term ? 0 : k.startsWith(term) ? 1 : k.includes(term) ? 2 : 3);
    }
    return best;
  }

  // ---------- Words typed into the search box: names and words they can mean (Filter.suggest, Filter.parse) ----------

  // A name as it is matched: normalized, without spaces, hyphens, ・ or a （…） suffix, with SUUMO's spelling variants
  // between kanji made one (市ヶ谷 = 市ケ谷 = 市が谷, 駒澤 = 駒沢, 江曾島 = 江曽島; の is ノ after normalize).
  const KANJI = "\\u3400-\\u9fff々〆";
  const VARIANT = new RegExp(`(?<=[${KANJI}])[ヶヵケガ](?=[${KANJI}])`, "g");
  const nameKey = (s) => normalize(s).replace(/\s*\(.*\)$/, "").replace(/[\s\-・'’.]/g, "").replace(VARIANT, "ケ")
    .replace(/澤/g, "沢").replace(/曾/g, "曽");
  const hasKanji = (s) => new RegExp(`[${KANJI}]`).test(s);
  // area name -> English keys: "Shibuya" -> shibuya, shibuyaku, shibuyaward ("Shibuya-ku" and "Shibuya ward" too)
  const SUFFIX = { 区: ["ku", "ward"], 市: ["shi", "city"], 町: ["machi", "cho", "town"], 村: ["mura", "son", "village"] };
  function areaKeys(name, opts) {
    const keys = [nameKey(name)], short = name.replace(/[区市町村]$/, "");
    if (short !== name && short.length >= 2) keys.push(nameKey(short));
    const en = opts.places?.[name];
    if (en) {
      const k = nameKey(en);
      keys.push(k, ...(SUFFIX[name.slice(-1)] || []).map((x) => k + x));
    }
    return keys;
  }
  const stationKeys = (name, readings) => [nameKey(name).replace(/駅$/, ""), ...(readings || []).filter(Boolean).map(nameKey)];

  // Words with a meaning in the query: property types, conditions, tag synonyms, and place words (stop: "周辺",
  // "near"; marker: 駅, station). The first word of each entry is its label.
  const WORDS = [
    [["マンション", "condo", "condos", "apartment", "mansion"], { cat: "condo" }],
    [["戸建て", "戸建", "一戸建て", "一戸建", "一軒家", "house", "houses"], { cat: "house" }],
    [["土地", "land", "lot"], { cat: "land" }],
    [["新築", "new"], { age: "new" }],
    [["中古", "used"], { age: "used" }],
    [["南向き", "南向", "south"], { flag: "south" }],
    [["角部屋", "角住戸", "corner"], { corner: true }],
    [["タワーマンション", "タワマン", "tower"], { flag: "tower" }],
    [["最上階"], { flag: "top" }],
    [["所有権", "freehold"], { flag: "freehold" }],
    [["建築条件なし", "建築条件無し"], { flag: "noCondition" }],
    [["新着"], { flag: "newOnly" }],
    [["値下げ", "値下がり"], { flag: "dropsOnly" }],
    [["仲介手数料なし", "仲介手数料無料", "仲介手数料不要", "手数料なし", "手数料無料", "手数料不要"], { flag: "nofee" }],
    [["即入居", "即入居可", "即引渡", "即引渡し", "即引渡可", "即引渡し可"], { flag: "ready" }],
    [["駐車場", "駐車場あり", "車庫", "parking"], { flag: "parking" }],
    [["新耐震"], { flag: "post1981" }],
    [["ペット可", "ペット", "ペット相談", "ペット相談可", "pet", "pets"], { tag: "ペット相談" }],
    [["リノベーション", "リノベ", "renovated"], { tag: "リノベーション" }],
    [["リフォーム", "リフォーム済"], { tag: "内装リフォーム" }],
    [["築浅"], { scalar: ["ageMax", 5] }],
    [["駅近", "駅チカ"], { scalar: ["walk", 5] }],
    [["周辺", "付近", "近く", "近辺", "エリア", "辺り", "あたり", "around", "in", "at", "the", "area"], { stop: true }],
    [["near"], { stop: true, near: true }],
    [["駅", "station", "sta"], { stop: true, marker: true }],
  ].map(([words, meaning]) => ({ ...meaning, label: words[0], keys: words.map(nameKey) }));

  // Per prefecture, built the first time it's needed (cached for the same opts): what each name key can mean, and
  // the candidates suggest offers, with how many properties each has.
  function nameTables(part, opts) {
    if (part.names && part.names.opts === opts) return part.names;
    const ix = part.index, n = (len) => new Int32Array(len);
    const areaN = n(ix.areas.length), stationN = n(ix.stations.length), townN = n(ix.towns.length),
      nameN = n(ix.names.length), tagN = n(ix.features.length), town = new Array(ix.towns.length).fill(null);
    const c = part.c, W = part.W;
    for (let i = 0; i < part.items.length; i++) {
      const r = part.items[i] & MASK, t = c.town[r];
      areaN[c.area[r]]++;
      for (let j = c.stOff[r]; j < c.stOff[r + 1]; j++) stationN[c.stId[j]]++;
      if (t >= 0) { townN[t]++; town[t] ??= part.townName[t]; }
      if (c.name[r] >= 0) nameN[c.name[r]]++;
      for (let w = 0; w < W; w++) for (let x = c.tags[r * W + w]; x; x &= x - 1) tagN[w * 32 + 31 - Math.clz32(x & -x)]++;
    }
    const meanings = new Map(), add = (key, m) => { if (key) (meanings.get(key) || meanings.set(key, []).get(key)).push(m); };
    const candidates = [];
    ix.areas.forEach(([code, name, , group], k) => {
      const keys = areaKeys(name, opts);
      for (const key of keys) add(key, { kind: "area", value: code, label: name });
      // a designated city's name means all its wards (横浜, 横浜市 -> 横浜市西区, 横浜市中区, ...)
      if (group && !["ku", "shi", "gun"].includes(group)) {
        for (const key of areaKeys(group, opts)) add(key, { kind: "area", value: code, label: name });
      }
      candidates.push({ kind: "area", value: code, label: name, keys, count: areaN[k] });
    });
    ix.stations.forEach((name, k) => {
      const readings = ix.stationNames?.[k], keys = stationKeys(name, readings);
      keys.forEach((key, j) => add(key, { kind: "station", value: name, label: name, reading: j > 0 && /^[\u30a0-\u30ff]+$/.test(key) }));  // kana
      candidates.push({ kind: "station", value: name, label: name, keys, count: stationN[k] });
    });
    const tags = new Map(ix.features.map((f, k) => [f, tagN[k]]));
    for (const w of WORDS) {
      if (w.tag && !tags.has(w.tag)) continue;  // a tag no listing here has
      for (const key of w.keys) add(key, { kind: "word", ...w });
      const keys = w.tag ? [nameKey(w.tag), ...w.keys.filter((k) => k !== nameKey(w.tag))] : [...w.keys];  // label first
      candidates.push({ kind: "word", value: w.tag || w.label, label: w.tag || w.label, keys,
        count: w.tag ? tags.get(w.tag) : null });
    }
    ix.features.forEach((f, k) => {  // a tag by its own name (one word: no spaces or numbers), in Japanese or English
      const en = opts.features?.[f], keys = [f, ...(en && !/\s/.test(en) ? [en] : [])].filter((x) => !/[\s\d]/.test(x)).map(nameKey);
      for (const key of keys) if (!(meanings.get(key) || []).some((m) => m.kind === "word")) add(key, { kind: "word", tag: f, label: f });
      const same = candidates.find((c) => c.kind === "word" && c.value === f);  // ペット相談 is a synonym's tag too
      if (same) same.keys.push(...keys.filter((key) => !same.keys.includes(key)));
      else if (keys.length) candidates.push({ kind: "word", value: f, label: f, keys, count: tagN[k] });
    });
    town.forEach((t, k) => { if (t) candidates.push({ kind: "town", value: t, label: t, keys: [nameKey(t)], count: townN[k] }); });
    ix.names.forEach((name, k) => { if (nameN[k]) candidates.push({ kind: "name", value: name, label: name, keys: [nameKey(name)], count: nameN[k] }); });
    return (part.names = { opts, meanings, candidates, tags: new Set(ix.features) });
  }

  const KIND_ORDER = { area: 0, station: 1, word: 2, town: 3, name: 4 };
  const CAPS = { station: 3, area: 2, town: 2, name: 2, word: 2 };
  // Candidates for the word being typed (after the last space; at least one kanji, or two other characters):
  // [{kind: area|station|town|name|word, value, label, count}], best first. value: an area code, a station or tag
  // name, or text (towns, building names); label: the Japanese name (the page translates it).
  function suggest(db, text, opts = {}) {
    const typed = (text || "").split(/\s+/).pop(), key = nameKey(typed);
    if (!key || (!hasKanji(typed) && key.length < 2)) return [];
    const found = new Map();  // kind + value -> candidate
    for (const part of db.parts || [db]) {
      for (const c of nameTables(part, opts).candidates) {
        // the name itself may contain what was typed; other keys (readings, English) must start with it
        let quality = 3;
        for (let j = 0; j < c.keys.length; j++) {
          const k = c.keys[j], m = k === key ? 0 : k.startsWith(key) ? 1 : j === 0 && k.includes(key) ? 2 : 3;
          if (m < quality) quality = m;
        }
        if (quality === 3) continue;
        // names and towns that differ only in width or spacing are one suggestion (text search treats them alike)
        const id = c.kind + "\u0000" + (c.kind === "name" || c.kind === "town" ? nameKey(c.value) : c.value), seen = found.get(id);
        if (seen) {
          seen.count = (seen.count ?? 0) + (c.count ?? 0) || seen.count;
          seen.quality = Math.min(seen.quality, quality);
          if (c.label.length < seen.label.length) Object.assign(seen, { value: c.value, label: c.label });  // the plain spelling
        }
        else found.set(id, { kind: c.kind, value: c.value, label: c.label, count: c.count, quality });
      }
    }
    const ranked = [...found.values()].sort((a, b) => a.quality - b.quality || KIND_ORDER[a.kind] - KIND_ORDER[b.kind]
      || (b.count ?? 0) - (a.count ?? 0) || a.label.length - b.label.length);
    const taken = {}, out = [];
    for (const c of ranked) {
      if ((taken[c.kind] = (taken[c.kind] || 0) + 1) > CAPS[c.kind]) continue;
      out.push({ kind: c.kind, value: c.value, label: c.label, count: c.count });
    }
    return out;
  }

  // What each name key can mean in these prefectures (cached per combined db and opts).
  function meaningsOf(db, opts) {
    const parts = db.parts || [db];
    if (parts.length === 1) return nameTables(parts[0], opts).meanings;
    if (db.meanings && db.meanings.opts === opts) return db.meanings.map;
    const map = new Map();
    for (const part of parts) {
      for (const [key, ms] of nameTables(part, opts).meanings) {
        const list = map.get(key) || map.set(key, []).get(key);
        for (const m of ms) if (!list.some((x) => x.kind === m.kind && x.value === m.value && x.label === m.label)) list.push(m);
      }
    }
    db.meanings = { opts, map };
    return map;
  }

  const NUM = "(\\d[\\d,]*(?:\\.\\d+)?)";
  const num = (s) => parseFloat(s.replace(/,/g, ""));
  const MAX_WORDS = "以下|まで|未満|以内", MIN_WORDS = "以上|から|超";
  const SIZE = new RegExp(`(?:(土地|敷地|land|lot)(\\s*))?(?:(over|above|at least|from|min|under|below|up to|max|less than)\\s*)?(?<![\\d.])${NUM}\\s*(m2|平米|平方メートル|sqm|sq\\s?m|坪)(?![a-z\\d])\\s*(${MAX_WORDS}|${MIN_WORDS})?`, "g");
  const LAYOUT = /(?<![a-z\d.])(\d+)\s*(sldk|ldk|sdk|slk|dk|lk|sk|k|r)(?:\s*\+\s*\d*s)?(?![a-z])(?:\s*以上)?|ワンルーム|studio/g;
  const LAYOUT_KIND = { r: 0, k: 0, sk: 0, dk: 1, sdk: 1, lk: 1, slk: 1, ldk: 2, sldk: 2 };
  const WALK = /(駅)?\s*(徒歩|歩いて)\s*(\d+)\s*分\s*(?:以内|以下|まで)?|駅\s*(\d+)\s*分\s*(?:以内|以下|まで)?|(?:(?:within|in|under)\s*)?(?<![\d.])(\d+)\s*(?:min|mins|minutes?)\s*(?:walk|on foot)|walk(?:ing)?\s*(?:within\s*)?(\d+)\s*(?:min|mins|minutes?)/g;
  const AGE = /築\s*(\d+)\s*年\s*(?:以内|未満|以下|まで)?|(?<![\d.])(\d+)\s*years?\s*old|((?:19|20)\d\d)\s*年\s*(?:築\s*)?(?:以降|以後|から)/g;
  // (English: 100M, 1.2B, 100 million; a lone m or b only right after the number: "100 m" is no price)
  const AMOUNT = `¥?${NUM}\\s*(?:(億)\\s*(?:(\\d[\\d,]*)\\s*万)?|(万)|(million|billion|(?<=\\d)[mb])(?![a-z\\d]))?\\s*(円)?`;
  const MONEY = new RegExp(`(?<![\\d.a-z])(?:(under|below|up to|less than|within|max|over|above|from|at least|more than|min)\\s*)?${AMOUNT}(?:\\s*(?:〜|-|から)\\s*${AMOUNT})?\\s*(${MAX_WORDS}|${MIN_WORDS})?`, "g");
  const IGNORED = /築\s*\d+\s*年\s*(?:以上|超)|(?:バス|bus)\s*\d+\s*(?:分|min\w*)|\d+\s*分\s*(?:以上|超)|\d[\d,.]*\s*(?:億|万)\s*円?\s*台/g;
  const isMax = (w) => /^(under|below|up to|less than|within|max|以下|まで|未満|以内)$/.test(w || "");
  const isMin = (w) => /^(over|above|from|at least|more than|min|以上|から|超)$/.test(w || "");
  const SCALARS = ["walk", "priceMin", "priceMax", "plan", "sizeMin", "landMin", "ageMax"];
  const QUAKE_YEAR = 1981;

  // A search box text -> query conditions. Pure (reads db's names only): returns
  //   patch: the conditions recognised ({types, areas, stations, features, walk, priceMin, priceMax, plan, sizeMin,
  //          landMin, ageMax} and flags like south: true), never text;
  //   tokens: [{field, value, label?, alt?, text}] in the order typed (alt: {field, value, label}, another reading:
  //          a ward named like a station);
  //   rest: what wasn't understood, for the text search; ignored: phrases understood but not usable (築30年以上,
  //          バス10分, or a value overridden by a later one).
  // opts: {places: area name -> English, features: tag -> English, year}.
  function parse(text, db, opts = {}) {
    const meanings = meaningsOf(db, opts), tags = new Set((db.parts || [db]).flatMap((p) => p.index.features));
    const orig = (text || "").normalize("NFKC");
    let low = orig.toLowerCase();
    if (low.length !== orig.length) low = orig;  // (lower-casing never changes lengths in practice)
    low = low.replace(/[〜～~]/g, "〜");
    const used = new Uint8Array(low.length), found = [], ignored = [], rest = [], markers = new Set();
    const masked = () => low.replace(/[^]/g, (c, i) => (used[i] ? "\u0000" : c));
    const mark = (a, b) => used.fill(1, a, b);
    const spanText = (a, b) => orig.slice(a, b).trim();
    const emit = (start, end, field, value, extra = {}) => found.push({ start, field, value, text: spanText(start, end), ...extra });
    // run a pattern over the text not used yet; fn returns false to leave a match, or [start, end] to use part of it
    const pass = (re, fn) => {
      for (const m of masked().matchAll(re)) {
        const r = fn(m);
        if (r === false) continue;
        const [a, b] = Array.isArray(r) ? r : [m.index, m.index + m[0].length];
        mark(a, b);
      }
    };
    pass(IGNORED, (m) => { ignored.push({ start: m.index, text: spanText(m.index, m.index + m[0].length) }); });
    // sizes before prices (80m2 isn't 80 million); 坪 -> ㎡; land when said so or when only land is asked for
    const onlyLand = /土地|敷地|\bland\b|\blot\b/.test(low) && !/マンション|戸建|一軒家|condo|apartment|house/.test(low);
    pass(SIZE, (m) => {
      const [, landWord, gap, pre, n, unit, post] = m, end = m.index + m[0].length;
      const start = landWord && gap ? m.index + landWord.length + gap.length : m.index;  // "土地 30坪": 土地 is a type too
      if (isMax(pre) || isMax(post)) { ignored.push({ start, text: spanText(start, end) }); return [start, end]; }
      const v = unit === "坪" ? Math.round(num(n) * TSUBO) : num(n);
      emit(start, end, landWord || onlyLand ? "landMin" : "sizeMin", v);
      return [start, end];
    });
    pass(LAYOUT, (m) => {
      const rank = m[1] ? +m[1] * 3 + LAYOUT_KIND[m[2]] : 3;
      emit(m.index, m.index + m[0].length, "plan", rank);
    });
    pass(WALK, (m) => {
      if (m[1] || m[2]) markers.add(m.index);  // "渋谷駅徒歩5分", "渋谷 徒歩5分": the name before is a station
      emit(m.index, m.index + m[0].length, "walk", +(m[3] || m[4] || m[5] || m[6]));
    });
    pass(AGE, (m) => {
      const end = m.index + m[0].length;
      if (m[3]) {
        if (+m[3] <= QUAKE_YEAR) emit(m.index, end, "post1981", true);
        else ignored.push({ start: m.index, text: spanText(m.index, end) });
      } else emit(m.index, end, "ageMax", +(m[1] || m[2]));
    });
    pass(/(\d+)\s*階\s*以上/g, (m) => {
      if (+m[1] === 2) emit(m.index, m.index + m[0].length, "upper", true);
      else ignored.push({ start: m.index, text: spanText(m.index, m.index + m[0].length) });
    });
    pass(/(?:仲介)?手数料\s*0\s*円?/g, (m) => { emit(m.index, m.index + m[0].length, "nofee", true); });
    pass(/\bsouth[\s-]*facing\b|\btop[\s-]*floor\b/g, (m) => { emit(m.index, m.index + m[0].length, m[0][0] === "s" ? "south" : "top", true); });
    pass(MONEY, (m) => {
      const [, pre, n1, oku1, man1b, man1, mil1, yen1, n2, oku2, man2b, man2, mil2, yen2, post] = m;
      const unit = (oku, man, mil) => (oku ? 1e8 : man ? 1e4 : mil ? (mil[0] === "b" ? 1e9 : 1e6) : 1);
      const amount = (n, oku, manB, man, mil, yen) => (oku || man || mil ? num(n) * unit(oku, man, mil) + (manB ? num(manB) * 1e4 : 0) : yen ? num(n) : null);
      const end = m.index + m[0].length;
      let a = amount(n1, oku1, man1b, man1, mil1, yen1);
      if (n2 != null) {  // a range: a bare first number takes the second one's unit (5000〜8000万, 50-80M)
        let b = amount(n2, oku2, man2b, man2, mil2, yen2);
        if (a == null && b != null) a = num(n1) * unit(oku2, man2, mil2);
        if (a == null && b == null && num(n1) >= 100 && num(n2) >= 100) { a = num(n1) * 1e4; b = num(n2) * 1e4; }
        if (a == null || b == null) return false;
        emit(m.index, end, "priceMin", Math.round(Math.min(a, b)));
        emit(m.index, end, "priceMax", Math.round(Math.max(a, b)));
        return undefined;
      }
      const q = isMax(pre) || isMax(post) ? "priceMax" : isMin(pre) || isMin(post) ? "priceMin" : null;
      if (a == null) {  // a bare number is 万 only next to a qualifier (6000以下)
        if (!q || num(n1) < 100) return false;
        a = num(n1) * 1e4;
      }
      emit(m.index, end, q || "priceMax", Math.round(a));
      return undefined;
    });

    // what's left, chunk by chunk: names and words, all of a chunk or none of it
    const segments = [];
    const work = masked();
    for (const m of work.matchAll(/[^\s\u0000、,，/／]+/g)) {
      const chunk = m[0], start = m.index, latin = /^[a-z0-9\-'.&]+$/.test(chunk);
      const segs = latin ? latinChunk(chunk, meanings) : cjkChunk(chunk, meanings);
      if (segs) for (const sg of segs) segments.push({ ...sg, start: start + sg.start, end: start + sg.end });
      else rest.push({ start, text: spanText(start, start + chunk.length) });
    }
    const between = (a, b) => low.slice(a, b).replace(/\u0000/g, "").trim() === "";
    const nextMarker = (k) => {
      const sg = segments[k], next = segments[k + 1];
      if (next && next.meanings.some((x) => x.marker) && between(sg.end, next.start)) return true;
      const after = low.slice(sg.end).search(/\S/);
      return after >= 0 && markers.has(sg.end + after);
    };
    const cats = new Set(), ages = new Set(), typeWords = [];
    let corner = null, tower = false;
    segments.forEach((sg, k) => {
      const text = spanText(sg.start, sg.end), word = sg.meanings.find((x) => x.kind === "word");
      if (word) {
        if (word.cat) { cats.add(word.cat); typeWords.push(sg); }
        else if (word.age) { ages.add(word.age); typeWords.push(sg); }
        else if (word.flag) { emit(sg.start, sg.end, word.flag, true); if (word.flag === "tower") tower = true; }
        else if (word.corner) corner = sg;
        else if (word.tag) {
          if (tags.has(word.tag)) emit(sg.start, sg.end, "features", word.tag, { label: word.tag });
          else rest.push({ start: sg.start, text });
        } else if (word.scalar) emit(sg.start, sg.end, word.scalar[0], word.scalar[1]);
        return;  // stop words and markers: nothing
      }
      const areas = sg.meanings.filter((x) => x.kind === "area"), stations = sg.meanings.filter((x) => x.kind === "station");
      // a ward named like a station (渋谷, 新宿) is the ward, with the station as the other reading, unless 駅/徒歩
      // follows or "near" comes before; a kana reading is a station
      const prev = segments[k - 1], uniq = (list) => [...new Map(list.map((x) => [x.value, x])).values()];
      const near = prev && prev.meanings.some((x) => x.near) && between(prev.end, sg.start);
      if (stations.length && (!areas.length || near || nextMarker(k) || stations.every((x) => x.reading))) {
        for (const x of uniq(stations)) emit(sg.start, sg.end, "stations", x.value, { label: x.label });
      } else {
        const alt = stations.length ? { field: "stations", value: stations[0].value, label: stations[0].label } : undefined;
        for (const x of uniq(areas)) emit(sg.start, sg.end, "areas", x.value, { label: x.label, ...(alt && { alt }) });
      }
    });

    // property types: the kinds named, each new and/or used as said (land is neither); tower means condos
    if (tower && !cats.size && !ages.size) cats.add("condo");
    const kinds = cats.size ? [...cats] : ages.size ? ["condo", "house"] : [];
    const when = ages.size ? [...ages] : ["new", "used"];
    const want = new Set(kinds.flatMap((c) => (c === "land" ? ["land"] : when.map((a) => `${a}_${c}`))));
    const types = (db.index.types || []).filter((t) => want.has(t));
    if (types.length) {
      const sg = typeWords[0] || { start: 0, end: 0 };
      found.push({ start: sg.start, field: "types", value: types, text: typeWords.map((t) => spanText(t.start, t.end)).join(" ") });
    }
    if (corner) {  // a corner: 角地 for land, 角住戸 for a flat
      const tag = types.length && types.every((t) => t === "land") ? "角地" : "角住戸";
      if (tags.has(tag)) emit(corner.start, corner.end, "features", tag, { label: tag });
      else rest.push({ start: corner.start, text: spanText(corner.start, corner.end) });
    }

    // the patch: sets add up; for a number, the last one said wins (the others are ignored)
    found.sort((a, b) => a.start - b.start);
    const patch = {}, last = {};
    for (const t of found) if (SCALARS.includes(t.field)) last[t.field] = t;
    const tokens = [];
    for (const t of found) {
      if (SCALARS.includes(t.field) && last[t.field] !== t) {
        if (!ignored.some((x) => x.start === t.start)) ignored.push({ start: t.start, text: t.text });
        continue;
      }
      if (t.field === "types") patch.types = t.value;
      else if (["areas", "stations", "features"].includes(t.field)) {
        (patch[t.field] ||= []).includes(t.value) || patch[t.field].push(t.value);
      } else patch[t.field] = t.value;
      const { start, ...token } = t;
      tokens.push(token);
    }
    if (patch.priceMin != null && patch.priceMax != null && patch.priceMin > patch.priceMax) {
      [patch.priceMin, patch.priceMax] = [patch.priceMax, patch.priceMin];
    }
    const byStart = (list) => list.sort((a, b) => a.start - b.start).map((x) => x.text);
    return { patch, tokens, rest: byStart(rest).join(" "), ignored: [...new Set(byStart(ignored))] };
  }

  // A latin word: the whole word only ("shibuya", "shibuya-ku", "jiyugaoka").
  function latinChunk(chunk, meanings) {
    const ms = meanings.get(nameKey(chunk));
    return ms ? [{ start: 0, end: chunk.length, meanings: ms }] : null;
  }
  // A run of kanji and kana: cut into known names and words, all of it, in as few pieces as possible
  // (「渋谷駅」 = 渋谷 + 駅, 「港区・渋谷区」); 「パークタワー勝どき」 stays text (パークタワー is nothing known).
  const LONGEST = 24;
  function cjkChunk(chunk, meanings) {
    const n = chunk.length, best = new Array(n + 1).fill(Infinity), next = new Array(n + 1);
    best[n] = 0;
    for (let i = n - 1; i >= 0; i--) {
      for (let j = Math.min(n, i + LONGEST); j > i; j--) {
        const key = nameKey(chunk.slice(i, j)), cost = key === "" ? 0 : meanings.has(key) ? 1 : Infinity;
        if (best[j] + cost < best[i]) { best[i] = best[j] + cost; next[i] = [j, key]; }
      }
    }
    if (best[0] === Infinity) return null;
    const out = [];
    for (let i = 0; i < n;) {
      const [j, key] = next[i];
      if (key) out.push({ start: i, end: j, meanings: meanings.get(key) });
      i = j;
    }
    return out;
  }

  // A query with a parse applied: types replaced, areas/stations/features added, numbers and flags set,
  // text = what wasn't understood.
  function applyParse(q, parsed) {
    const out = { ...q };
    for (const [k, v] of Object.entries(parsed.patch)) {
      if (k === "types") out.types = [...v];
      else if (Array.isArray(out[k])) out[k] = [...new Set([...out[k], ...v])];
      else out[k] = v;
    }
    out.text = parsed.rest;
    return out;
  }

  return { emptyQuery, upgrade, normalize, stationKey, photo, FLAGS, TSUBO, load, combine, release, every, find, item, get,
    partOf, positions, matches, search, count, counts, facets, spatial, stationMatch, suggest, parse, applyParse, nameKey };
});
