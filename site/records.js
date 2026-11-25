// A listing's full record, from its bucket (data/l/<id % buckets>.json.zst, zstd laid out by field: Filter.decode;
// fetched once per page load, revalidated with the server so a deploy never pairs a fresh index with a stale
// bucket). Rejects with Error("gone") when the bucket has no such listing (it ended), else Error("offline").

import { recent } from "./util.js";
import { st } from "./state.js";

// A listing's full record, from its bucket (data/l/<id % buckets>.json.zst, zstd laid out by field: Filter.decode; fetched
// once per page load, revalidated with the server so a deploy never pairs a fresh index with a stale bucket).
// Rejects with Error("gone") when the bucket has no such listing (it ended), else Error("offline") (try again).
// (the last few buckets and records are kept, least recently used out first)
const buckets = new Map(), records = new Map(), MAX_BUCKETS = 8, MAX_RECORDS = 100;
function bucket(url) {
  return recent(buckets, url, MAX_BUCKETS, () => fetch(url, { cache: "no-cache" })
    .then((res) => (res.ok ? res.arrayBuffer().then(Filter.decode) : res.status === 404 ? {} : Promise.reject(new Error("offline"))))
    .catch((e) => { buckets.delete(url); throw new Error(e.message === "unsupported" ? "unsupported" : "offline"); }));
}
export function record(key) {
  recent(records, key, MAX_RECORDS, () => null);  // (its place in line; made below when new)
  if (!records.get(key)) {
    const at = key.indexOf(":");
    records.set(key, bucket(Filter.bucketOf(key, st.manifest?.buckets, st.manifest?.bucketExt)).then((all) => {
      const r = all[key];
      if (!r) throw new Error("gone");
      return { ...r, key, type: key.slice(0, at), id: key.slice(at + 1), url: Filter.suumoUrl(key, r.url),
        others: (r.others || []).map((o) => ({ ...o, url: Filter.suumoUrl(key, o.url) })) };
    }).catch((e) => { records.delete(key); throw e; }));
  }
  return records.get(key);
}
