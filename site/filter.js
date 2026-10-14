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
  // A flag bit of the index; 0 when the index predates it (a cached old index after a deploy): nothing has it.
  const bit = (db, name) => db.flags[name] || 0;

  // NFKC (full-width -> half-width), lower case, hiragana -> katakana, no accents: "ぱーく", "ﾊﾟｰｸ" and "パーク"
  // all match, and so do "jiyugaoka" and "Jiyūgaoka".
  // (Accents are only stripped from strings that can have them: Latin, Greek, Cyrillic; kana's dakuten stay.)
  const normalize = (s) => {
    const k = (s || "").normalize("NFKC").toLowerCase().replace(/[ぁ-ゖ]/g, (c) => String.fromCharCode(c.charCodeAt(0) + 0x60));
    return /[\u00c0-\u1fff]/.test(k) ? k.normalize("NFD").replace(/[\u0300-\u036f]/g, "").normalize("NFC") : k;
  };
  // A station name as typed: "Futako-Tamagawa", "futakotamagawa" and "ふたこたまがわ" are the same.
  const stationKey = (s) => normalize(s).replace(/[\s\-・]/g, "");

  // A listing's tags: a bit string, 6 bits per character, bit k = index.features[k] (see site._mask).
  const B64 = Object.fromEntries([..."ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"].map((ch, v) => [ch, v]));
  function tagIds(mask) {
    const out = [];
    for (let k = 0; k < mask.length; k++) {
      const v = B64[mask[k]];
      for (let b = 0; b < 6; b++) if (v & (1 << b)) out.push(k * 6 + b);
    }
    return out;
  }

  // A listing's photo: a number is the usual path, shortened by the builder (see site._image).
  const photo = (v, id) => (typeof v === "number" ? `030/N010000/${id.slice(-3)}/${String(v).padStart(4, "0")}.jpg` : v);

  function load(index) {
    const c = index.columns, n = c.id.length, items = new Array(n), stops = new Map();
    let id = 0;
    for (let i = 0; i < n; i++) {
      id += c.id[i];
      const type = index.types[c.type[i]], area = index.areas[c.area[i]], town = index.towns[c.town[i]];
      const st = c.stations[i], stations = [];
      for (let j = 0; j < st.length; j++) {  // [name, walk minutes, bus minutes, car km] (nulls when not)
        const m = c.access[i][j];
        stations.push([index.stations[st[j]], m >= 0 && m < 1000 ? m : null, m < 0 ? -m : null, m >= 1000 ? (m - 1000) / 10 : null]);
      }
      const sid = String(id), it = {
        id: sid, idn: id, type, key: type + ":" + sid, area: area[0], areaName: area[1],
        price: c.price[i], priceHi: c.priceMax[i] ?? c.price[i], plan: c.plan[i], size: c.size[i],
        land: c.land[i], age: c.age[i] == null ? null : Math.floor(c.age[i] / 12), ageMonths: c.age[i],
        stations, stop: c.stop?.[i] ?? null, flags: c.flags[i],
        others: c.others[i], newAt: c.newAt[i], newDate: c.newAt[i] ? Math.floor(c.newAt[i] / 100) : null,
        dropAt: c.drop[i]?.[0] ?? null, prevPrice: c.drop[i]?.[1] ?? null, firstSeen: c.firstSeen[i], unit: c.unit[i],
        town: town ? town[0].slice(area[2].length) : null, lat: town ? town[1] : null, lng: town ? town[2] : null,
        name: index.names[c.name[i]] ?? "", layout: index.layouts[c.layout[i]] ?? "", image: photo(c.image[i], sid),
        fees: c.fees?.[i] != null ? c.fees[i] * 100 : null,  // condos' monthly fees, yen (the index stores ¥100)
        // positions in this prefecture's tables (index.areas, .stations, .features), for counting
        areaId: c.area[i], stationIds: st, tagIds: tagIds(c.features[i]), part: null,
        nameId: c.name[i], townId: c.town[i], layoutId: c.layout[i], stopId: null,
      };
      it.features = new Set(it.tagIds.map((k) => index.features[k]));
      if (it.stop) {  // bus stops get a table of their own (for text search)
        if (!stops.has(it.stop[0])) stops.set(it.stop[0], stops.size);
        it.stopId = stops.get(it.stop[0]);
      }
      items[i] = it;
    }
    index.columns = null;  // the items hold everything now; free the raw columns
    const gone = index.flags.gone || 0;  // ended listings kept because saved: in the saved list, never in search
    // station -> [kana, English] (null for bus stops: kanji only)
    const stationNames = new Map(index.stations.map((s, i) => [s, index.stationNames?.[i] || null]));
    const db = { index, items: items.filter((it) => !(it.flags & gone)), flags: index.flags, stationNames,
      byKey: new Map(items.map((it) => [it.key, it])) };
    db.parts = [db];  // the prefectures searched: one here, several after combine
    db.stops = [...stops.keys()];
    for (const it of items) it.part = db;
    return db;
  }

  // Several prefectures' indexes (each loaded with `load`) searched as one.
  function combine(dbs) {
    const all = (f) => dbs.flatMap(f);
    const index = { types: [...new Set(all((d) => d.index.types))], areas: all((d) => d.index.areas),
      stations: [...new Set(all((d) => d.index.stations))], towns: all((d) => d.index.towns), flags: dbs[0].flags };
    return { index, items: all((d) => d.items), flags: index.flags, stationNames: new Map(all((d) => [...d.stationNames])),
      byKey: new Map(all((d) => [...d.byKey])), parts: all((d) => d.parts) };
  }

  // Text search: a word matches an item when it is part of its name, town, area (or its English name, it.alias:
  // set by the page), layout, a station (with 駅, kana or English) or its bus stop. Each prefecture's tables are
  // normalized once, the first time a text search runs; a search marks the table entries containing each word, and
  // items are tested by their positions in those tables.
  function textTables(part) {
    if (part.textTables) return part.textTables;
    const ix = part.index, alias = new Array(ix.areas.length).fill(null), town = new Array(ix.towns.length).fill(null);
    for (const it of part.items) {
      if (it.alias && alias[it.areaId] == null) alias[it.areaId] = it.alias;
      if (it.town && it.townId != null && town[it.townId] == null) town[it.townId] = it.town;
    }
    const norm = (v) => (v ? normalize(v) : null);
    return (part.textTables = {
      names: ix.names.map(norm), towns: town.map(norm), layouts: ix.layouts.map(norm),
      areas: ix.areas.map((a, k) => [norm(a[1]), norm(alias[k])].filter(Boolean)),
      stations: ix.stations.map((st, k) => [st + "駅", ...(ix.stationNames?.[k] || [])].filter(Boolean).map(normalize)),
      stops: part.stops.map(norm),
    });
  }
  const marks = (list, w) => Uint8Array.from(list, (v) => (v && v.includes(w) ? 1 : 0));
  const marksAny = (list, w) => Uint8Array.from(list, (vs) => (vs.some((v) => v.includes(w)) ? 1 : 0));
  let textCache = [null, new Map()];  // the last text searched, and per prefecture the marks of each of its words
  function wordMarks(part, v) {
    if (textCache[0] !== v) textCache = [v, new Map()];
    let m = textCache[1].get(part);
    if (!m) {
      const t = textTables(part);
      textCache[1].set(part, m = words(v).map((w) => ({ names: marks(t.names, w), towns: marks(t.towns, w),
        layouts: marks(t.layouts, w), areas: marksAny(t.areas, w), stations: marksAny(t.stations, w), stops: marks(t.stops, w) })));
    }
    return m;
  }
  function textMatches(it, v) {
    for (const m of wordMarks(it.part, v)) {
      if ((it.nameId != null && m.names[it.nameId]) || (it.townId != null && m.towns[it.townId]) || m.areas[it.areaId]
        || (it.layoutId != null && m.layouts[it.layoutId]) || (it.stopId != null && m.stops[it.stopId])) continue;
      let hit = false;
      for (const s of it.stationIds) if (m.stations[s]) { hit = true; break; }
      if (!hit) return false;
    }
    return true;
  }

  function walkTo(it, chosen) {
    let best = null;
    for (const [s, w] of it.stations) {
      if (w != null && (!chosen.length || chosen.includes(s)) && (best == null || w < best)) best = w;
    }
    return best;
  }

  const STATION_WORDS = new Set(["station", "sta", "sta.", "駅"]);  // "Shibuya Station" finds what "Shibuya" does
  let typed = [null, []];  // the last text searched and its words: split once per search, not once per item
  const words = (v) => (typed[0] === v ? typed : (typed = [v, normalize(v).split(/\s+/).filter((w) => w && !STATION_WORDS.has(w))]))[1];

  // One check per condition: (item, value, query) -> passes. Empty values pass.
  const CHECKS = {
    types: (it, v) => !v.length || v.includes(it.type),
    areas: (it, v) => !v.length || v.includes(it.area),
    stations: (it, v) => !v.length || it.stations.some(([s]) => v.includes(s)),
    walk: (it, v, q) => v == null || (walkTo(it, q.stations) ?? Infinity) <= v,
    priceMax: (it, v) => v == null || (it.price != null && it.price <= v),
    priceMin: (it, v) => v == null || (it.priceHi != null && it.priceHi >= v),
    landMin: (it, v) => v == null || (it.land != null && it.land >= v),
    features: (it, v) => v.every((f) => it.features.has(f)),
    freehold: (it, v, q, db) => !v || !(it.flags & db.flags.leasehold),
    noCondition: (it, v, q, db) => !v || !(it.flags & db.flags.conditional),
    newOnly: (it, v, q, db) => !v || !!(it.flags & db.flags.new),
    dropsOnly: (it, v, q, db) => !v || !!(it.flags & db.flags.dropped),
    // new or cheaper since an hour (YYYYMMDDHH): "since your last visit"
    since: (it, v) => v == null || it.newAt > v || it.dropAt > v,
    south: (it, v, q, db) => !v || !!(it.flags & bit(db, "south")),
    top: (it, v, q, db) => !v || !!(it.flags & bit(db, "top")),            // condos only
    upper: (it, v, q, db) => !v || !!(it.flags & bit(db, "upper")),        // condos only
    nofee: (it, v, q, db) => !v || !!(it.flags & bit(db, "nofee")),
    ready: (it, v, q, db) => !v || !!(it.flags & bit(db, "ready")),
    tower: (it, v, q, db) => !v || !!(it.flags & bit(db, "tower")),
    parking: (it, v, q, db) => !v || !!(it.flags & bit(db, "parking")),    // houses and land only
    // monthly fees (yen) of condos; houses and land have none and pass
    feesMax: (it, v) => v == null || !isCondo(it.type) || (it.fees != null && it.fees <= v),
    text: (it, v) => !v || textMatches(it, v),
  };
  // Conditions on the building: they don't apply to land when 土地 is chosen; otherwise land fails them.
  const BUILDING = {
    plan: (it, v) => v == null || (it.plan != null && it.plan >= v),
    sizeMin: (it, v) => v == null || (it.size != null && it.size >= v),
    ageMax: (it, v) => v == null || (it.age != null && it.age <= v),
    post1981: (it, v, q, db) => !v || !!(it.flags & db.flags.post1981),
  };
  const GROUP = { walk: "stations" };  // walk depends on the chosen stations: count them together
  // The FLAGS conditions as bits of the index: [flag name, passes when set (true) or when not set (false)]
  const FLAG_BITS = { freehold: ["leasehold", false], noCondition: ["conditional", false], newOnly: ["new", true],
    dropsOnly: ["dropped", true], post1981: ["post1981", true], south: ["south", true], top: ["top", true],
    upper: ["upper", true], nofee: ["nofee", true], ready: ["ready", true], tower: ["tower", true],
    parking: ["parking", true] };
  const FLAGS = ["freehold", "noCondition", "newOnly", "dropsOnly", "post1981", "south", "top", "upper", "nofee",
    "ready", "tower", "parking"];
  const ALL = [...Object.entries(CHECKS).map(([d, check]) => [d, check, false]),
    ...Object.entries(BUILDING).map(([d, check]) => [d, check, true])];

  // The query's conditions that are set, as [name, check, on the building]: the others pass every item.
  const conditions = (q) => ALL.filter(([d]) => {
    const v = q[d];
    return v != null && v !== false && v !== "" && !(Array.isArray(v) && !v.length);
  });

  // The conditions an item fails (at most 2 are collected: that's all faceting needs).
  function failing(db, it, q, conds = conditions(q)) {
    const out = [];
    const exempt = it.type === "land" && q.types.includes("land");
    for (const [d, check, building] of conds) {
      if ((building && exempt) || check(it, q[d], q, db)) continue;
      // land isn't a building: choosing 土地 is what would let it through
      const dim = building && it.type === "land" ? "types" : GROUP[d] || d;
      if (!out.includes(dim) && out.push(dim) > 1) return out;
    }
    return out;
  }

  // Whether an item passes the given conditions (stops at the first failure).
  function passes(db, it, q, conds) {
    const exempt = it.type === "land" && q.types.includes("land");
    for (const [d, check, building] of conds) if (!(building && exempt) && !check(it, q[d], q, db)) return false;
    return true;
  }
  const matches = (db, it, q, conds = conditions(q)) => passes(db, it, q, conds);

  const newest = (d) => (d ? -d : Infinity);  // newest first, missing last
  const onlyLand = (q) => q.types.length > 0 && q.types.every((t) => t === "land");
  const onlyHouses = (q) => q.types.length > 0 && q.types.every(isHouse);
  const orLast = (v) => (v == null ? Infinity : v);
  function sortKey(it, q) {
    switch (q.sort) {
      case "price_asc": return [orLast(it.price), it.idn];
      case "price_desc": return [-(it.priceHi || 0), it.idn];
      // land after the buildings, unless only land is chosen
      case "size_desc": return [it.type === "land" && !onlyLand(q) ? 1 : 0, -(it.size || it.land || 0), it.idn];
      case "walk_asc": return [orLast(walkTo(it, q.stations)), it.idn];
      case "age_asc": return [orLast(it.ageMonths), it.idn];
      // a house's price per ㎡ of building isn't comparable with a condo's or a plot's: houses last unless only
      // houses are chosen
      case "unit_asc": return [orLast(isHouse(it.type) && !onlyHouses(q) ? null : it.unit), it.idn];
      case "fees_asc": return [orLast(isCondo(it.type) ? it.fees : null), it.idn];
      case "drop_desc": return [it.prevPrice && it.price ? it.price / it.prevPrice : Infinity, it.idn];  // biggest cut first
      default: return [newest(it.newDate), newest(it.firstSeen), -it.idn];
    }
  }

  // The matching items (of all, or of the given ones), sorted.
  function search(db, q, items = db.items) {
    const conds = conditions(q), hits = [];
    for (const it of items) if (passes(db, it, q, conds)) hits.push(it);
    // sort keys in columns of numbers (a stable sort of positions, so ties keep the items' order)
    const n = hits.length, width = n ? sortKey(hits[0], q).length : 0;
    const keys = Array.from({ length: width }, () => new Float64Array(n));
    for (let i = 0; i < n; i++) {
      const k = sortKey(hits[i], q);
      for (let l = 0; l < width; l++) keys[l][i] = k[l];
    }
    const order = Array.from({ length: n }, (_, i) => i).sort((a, b) => {
      for (let l = 0; l < width; l++) if (keys[l][a] !== keys[l][b]) return keys[l][a] < keys[l][b] ? -1 : 1;
      return 0;
    });
    return order.map((i) => hits[i]);
  }

  function count(db, q) {
    const conds = conditions(q);
    let n = 0;
    for (const it of db.items) if (matches(db, it, q, conds)) n++;
    return n;
  }

  // Threshold conditions as (value of an item, comparison): "max" choices pass items whose value is at most the
  // choice, "min" ones items whose value is at least it; null: the item passes no choice, true: every choice.
  const THRESHOLDS = {
    priceMax: ["max", (it) => it.price], priceMin: ["min", (it) => it.priceHi], plan: ["min", (it) => it.plan],
    sizeMin: ["min", (it) => it.size], landMin: ["min", (it) => it.land], ageMax: ["max", (it) => it.age],
    walk: ["max", (it, q) => walkTo(it, q.stations)],
    feesMax: ["max", (it) => (isCondo(it.type) ? it.fees : true)],
  };
  const sortedChoices = new WeakMap();  // choice list -> its distinct values ascending
  function ascending(values) {
    let s = sortedChoices.get(values);
    if (!s) sortedChoices.set(values, s = [...new Set(values)].sort((a, b) => a - b));
    return s;
  }
  // Position of the first value >= x (max) or the last value <= x (min) in ascending values (-1 / length: none).
  function bucket(values, x, kind) {
    let lo = 0, hi = values.length;
    if (kind === "max") {  // first values[i] >= x
      while (lo < hi) { const m = (lo + hi) >> 1; if (values[m] >= x) hi = m; else lo = m + 1; }
      return lo;
    }
    while (lo < hi) { const m = (lo + hi) >> 1; if (values[m] <= x) lo = m + 1; else hi = m; }
    return lo - 1;  // last values[i] <= x
  }

  // Counts per position of a table (Int32Array) plus the order positions were first counted in, so the result keeps
  // the order a plain object filled item by item would have.
  class Tally {
    constructor(n) { this.n = new Int32Array(n); this.order = []; }
    add(k) { if (this.n[k]++ === 0) this.order.push(k); }
    into(out, name) { for (const k of this.order) out[name(k)] = (out[name(k)] || 0) + this.n[k]; }
  }

  // Counts for every choice of every condition, in one pass: an item that fails no condition counts for
  // all choices it satisfies; one that fails exactly one condition counts for that condition's choices.
  // choices: {condition: [values]} for thresholds (priceMax, plan, walk...); sets are counted by value.
  function facets(db, q, choices) {
    const out = { total: 0, types: {}, areas: {}, stations: {}, features: {} };
    const dims = [];  // {d, group, kind, value, values, hist}
    for (const d in choices) {
      const t = THRESHOLDS[d], values = ascending(choices[d]);
      dims.push({ d, group: GROUP[d] || d, kind: t ? t[0] : null, value: t ? t[1] : null, values,
        hist: new Int32Array(values.length) });
    }
    // flags as bit tests: [name, bit, passes when the bit is set (or when it isn't: freehold, noCondition)]
    const flagBits = FLAGS.map((f) => [f, ...(FLAG_BITS[f] ? [bit(db, FLAG_BITS[f][0]), FLAG_BITS[f][1]] : [0, true])]);
    const flagN = new Int32Array(FLAGS.length);
    const tallies = new Map();  // prefecture -> its area, station and tag tallies
    const tallyOf = (part) => {
      let t = tallies.get(part);
      if (!t) tallies.set(part, t = { areas: new Tally(part.index.areas.length), stations: new Tally(part.index.stations.length),
        tags: new Tally(part.index.features.length) });
      return t;
    };
    const conds = conditions(q), walk = q.walk, types = out.types, none = [];
    let part = null, tally = null;
    for (const it of db.items) {
      const f = conds.length ? failing(db, it, q, conds) : none;
      if (f.length > 1) continue;
      if (it.part !== part) { part = it.part; tally = tallyOf(part); }
      const only = f.length ? f[0] : null, free = only === null;
      if (free) {
        out.total++;
        // a tag counts when the item has all chosen tags but it, i.e. (it is in it.features) all of them
        const ids = it.tagIds;
        for (let k = 0; k < ids.length; k++) tally.tags.add(ids[k]);
      }
      if (free || only === "types") types[it.type] = (types[it.type] || 0) + 1;
      if (free || only === "areas") tally.areas.add(it.areaId);
      if (free || only === "stations") {
        const sts = it.stations;
        for (let j = 0; j < sts.length; j++) {
          const w = sts[j][1];
          if (walk == null || (w != null && w <= walk)) tally.stations.add(it.stationIds[j]);
        }
      }
      for (let k = 0; k < dims.length; k++) {
        const dim = dims[k];
        if (!free && only !== dim.group) continue;
        const values = dim.values, hist = dim.hist;
        if (!dim.kind) {  // a condition without a known shape: check every choice
          const check = CHECKS[dim.d] || BUILDING[dim.d];
          for (let i = 0; i < values.length; i++) if (check(it, values[i], q, db)) hist[i]++;
          continue;
        }
        const x = dim.value(it, q);
        if (x == null) continue;
        if (x === true) hist[dim.kind === "max" ? 0 : values.length - 1]++;
        else {
          const b = bucket(values, x, dim.kind);
          if (b >= 0 && b < values.length) hist[b]++;
        }
      }
      const flags = it.flags;
      for (let k = 0; k < flagBits.length; k++) {
        const fb = flagBits[k];
        if ((free || only === fb[0]) && ((flags & fb[1]) !== 0) === fb[2]) flagN[k]++;
      }
    }
    for (let k = 0; k < FLAGS.length; k++) out[FLAGS[k]] = flagN[k];
    for (const [p, t] of tallies) {
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

  // The number of items matching q, and q changed by each patch ({name: {condition: value}}), in one pass:
  // counts(db, q, {fresh: {newOnly: true}, cheaper: {dropsOnly: true}}) -> {total, fresh, cheaper}.
  function counts(db, q, patches = {}) {
    const names = Object.keys(patches), touched = new Set();
    for (const n of names) for (const d in patches[n]) touched.add(d);
    if (touched.has("stations")) touched.add("walk");             // walk is measured to the chosen stations
    if (touched.has("types")) for (const d in BUILDING) touched.add(d);  // land is exempt when 土地 is chosen
    const conds = conditions(q), shared = conds.filter(([d]) => !touched.has(d)), own = conds.filter(([d]) => touched.has(d));
    const variants = names.map((n) => {
      const qq = { ...q, ...patches[n] };
      return [n, qq, conditions(qq).filter(([d]) => touched.has(d))];
    });
    const out = { total: 0 };
    for (const n of names) out[n] = 0;
    for (const it of db.items) {
      if (!passes(db, it, q, shared)) continue;
      if (passes(db, it, q, own)) out.total++;
      for (const [n, qq, cs] of variants) if (passes(db, it, qq, cs)) out[n]++;
    }
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

  return { emptyQuery, normalize, stationKey, load, combine, matches, search, count, counts, facets, stationMatch };
});
