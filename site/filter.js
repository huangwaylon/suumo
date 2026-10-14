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

  // Tags the site now offers as conditions (the index no longer carries them, site.HIDDEN_TAGS).
  const SOUTH_TAGS = ["南向き", "全室南向き", "東南向き", "南西向き", "全室東南向き", "全室南西向き"];
  const READY_TAGS = ["即引渡可", "即引渡し可"], TOP_TAG = "最上階・上階なし";
  const TSUBO = 3.30578, LAND_TAGS = { 土地50坪以上: Math.round(50 * TSUBO), 土地100坪以上: Math.round(100 * TSUBO) };
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
  // The same, as the safety net of a query still asking for such a tag (a page older than the index): the tag's
  // condition stands in for it where the item's index doesn't carry the tag any more.
  const TAG_RULES = {
    ...Object.fromEntries(SOUTH_TAGS.map((t) => [t, (it) => flagOf(it, "south")])),
    ...Object.fromEntries(READY_TAGS.map((t) => [t, (it) => flagOf(it, "ready")])),
    [TOP_TAG]: (it) => flagOf(it, "top"),
    建築条件なし: (it) => !(it.flags & bit(it.part, "conditional")),
    ...Object.fromEntries(Object.entries(LAND_TAGS).map(([t, m2]) => [t, (it) => it.land != null && it.land >= m2])),
  };
  const tagSet = (part) => (part.tagSet ??= new Set(part.index.features));
  const hasTag = (it, f) => it.features.has(f) || (!!TAG_RULES[f] && !tagSet(it.part).has(f) && TAG_RULES[f](it));
  // A flag of an item, read from its own prefecture's index; an index older than the flag answers from the tags it
  // still has (the other way round from TAG_RULES).
  const FLAG_TAGS = { south: SOUTH_TAGS, ready: READY_TAGS, top: [TOP_TAG] };
  function flagOf(it, name) {
    const b = it.part.flags[name];
    if (b) return !!(it.flags & b);
    return (FLAG_TAGS[name] || []).some((t) => it.features.has(t));
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
    features: (it, v) => v.every((f) => hasTag(it, f)),
    freehold: (it, v, q, db) => !v || !(it.flags & db.flags.leasehold),
    noCondition: (it, v, q, db) => !v || !(it.flags & db.flags.conditional),
    newOnly: (it, v, q, db) => !v || !!(it.flags & db.flags.new),
    dropsOnly: (it, v, q, db) => !v || !!(it.flags & db.flags.dropped),
    // new or cheaper since an hour (YYYYMMDDHH): "since your last visit"
    since: (it, v) => v == null || it.newAt > v || it.dropAt > v,
    south: (it, v) => !v || flagOf(it, "south"),
    top: (it, v) => !v || flagOf(it, "top"),            // condos only
    upper: (it, v) => !v || flagOf(it, "upper"),        // condos only
    nofee: (it, v) => !v || flagOf(it, "nofee"),
    ready: (it, v) => !v || flagOf(it, "ready"),
    tower: (it, v) => !v || flagOf(it, "tower"),
    parking: (it, v) => !v || flagOf(it, "parking"),    // houses and land only
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
    const bitsOf = (p) => FLAGS.map((f) => [f, bit(p, FLAG_BITS[f][0]), FLAG_BITS[f][1]]);  // per prefecture's index
    let flagBits = null;
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
      if (it.part !== part) { part = it.part; tally = tallyOf(part); flagBits = bitsOf(part); }
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
    for (const it of part.items) {
      areaN[it.areaId]++;
      for (const s of it.stationIds) stationN[s]++;
      if (it.townId != null) { townN[it.townId]++; town[it.townId] ??= it.town; }
      if (it.nameId != null) nameN[it.nameId]++;
      for (const k of it.tagIds) tagN[k]++;
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
      const v = unit === "坪" ? Math.round(num(n) * 3.30578) : num(n);
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

  return { emptyQuery, upgrade, normalize, stationKey, load, combine, matches, search, count, counts, facets, stationMatch,
    suggest, parse, applyParse, nameKey };
});
