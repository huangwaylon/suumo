#!/usr/bin/env bash
# A crawl worker's state on a GitHub release (kalori-scrape.yml's pattern): the asset state-<n>.db.zst, or a newer
# state-<n>.db.zst.<run>-<attempt> a save left before its rename. The upload before it is kept as
# state-<n>.prev.db.zst, a name `get` never matches (assets(); tests/test_ci.py pins this): a bad upload can be undone
# by hand (it guards against a bad save, not against losing the release).
#   state.sh get <tag> <n> <db>    download the newest whole copy, check it, decompress to <db>
#   state.sh put <tag> <n> <db>    checkpoint, check, compress, upload under a new name, then swap it in
# Never a moment without a whole copy: the new one is uploaded and checked before the old one is renamed. A failure
# between the renames (the old .prev deleted, base not yet renamed, or base renamed and new not yet) leaves no .prev or
# no base: safe, `get` takes the newest whole copy (base, or the upload under its temporary name), the next put
# tidies up.
# put also labels the asset 'queued=N crawled=pref@ISO,...' (listing pages queued; each prefecture's last crawl):
# crawl.yml's plan (ci.py plan) runs only the shards with work.
# Needs GH_TOKEN and GH_REPO (contents: write for put).
set -euo pipefail
cmd=$1 tag=$2 n=$3 db=$4
[[ $n =~ ^[0-9]+$ ]] || { echo "::error::state.sh: shard '$n' is not a number"; exit 2; }
MAX_ATTEMPTS=3   # pipeline.MAX_DETAIL_ATTEMPTS: queue rows still to try (tests/test_ci.py checks)
base="state-$n.db.zst"
prev="state-$n.prev.db.zst"
assets() {
  gh api "repos/$GH_REPO/releases/tags/$tag" --jq "[.assets[] | select(.name == \"$base\" or (.name | startswith(\"$base.\")))]"
}
case $cmd in
  get)
    id=$(assets | jq -r '[.[] | select(.state == "uploaded")] | sort_by(.created_at) | last | .id // empty')
    [ -n "$id" ] || { echo "::error::$tag has no $base"; exit 1; }
    gh api -H 'Accept: application/octet-stream' "repos/$GH_REPO/releases/assets/$id" > "$db.zst"
    zstd -q -d -f "$db.zst" -o "$db"
    rm "$db.zst"
    [ "$(sqlite3 "$db" 'PRAGMA quick_check')" = ok ] || { echo "::error::$base fails quick_check"; exit 1; }
    echo "got $base: $(stat -c %s "$db") bytes, $(sqlite3 "$db" 'SELECT COUNT(*) FROM listings') listings"
    ;;
  put)
    # (the run has exited: fold its WAL into the file, which alone is uploaded)
    sqlite3 "$db" 'PRAGMA wal_checkpoint(TRUNCATE)' > /dev/null
    [ "$(sqlite3 "$db" 'PRAGMA quick_check')" = ok ] || { echo "::error::$db fails quick_check: not saved"; exit 1; }
    queued=$(sqlite3 "$db" "SELECT COUNT(*) FROM queue WHERE attempts < $MAX_ATTEMPTS")
    crawled=$(sqlite3 "$db" "SELECT COALESCE(group_concat(pref || '@' || crawled_at, ','), '') FROM prefs")
    label="queued=$queued crawled=$crawled"
    u="$base.${GITHUB_RUN_ID:-local}-${GITHUB_RUN_ATTEMPT:-1}"
    zstd -q -T0 -9 -f "$db" -o "$RUNNER_TEMP/$u"
    for i in 1 2 3; do
      gh release upload "$tag" "$RUNNER_TEMP/$u" --clobber && break
      [ "$i" = 3 ] && { echo "::error::$u did not upload"; exit 1; }
      sleep $((i * 10))
    done
    a=$(assets)
    new=$(echo "$a" | jq -r --arg u "$u" '.[] | select(.name == $u and .state == "uploaded") | .id')
    size=$(echo "$a" | jq -r --arg u "$u" '.[] | select(.name == $u) | .size')
    [ -n "$new" ] && [ "$size" = "$(stat -c %s "$RUNNER_TEMP/$u")" ] || { echo "::error::$u is not whole on $tag"; exit 1; }
    # the old .prev deleted, base -> .prev, new -> base (a rename onto an existing name is refused: 422)
    oldprev=$(gh api "repos/$GH_REPO/releases/tags/$tag" --jq ".assets[] | select(.name == \"$prev\") | .id")
    [ -z "$oldprev" ] || gh api -X DELETE "repos/$GH_REPO/releases/assets/$oldprev"
    old=$(echo "$a" | jq -r --arg b "$base" '.[] | select(.name == $b) | .id')
    [ -z "$old" ] || gh api -X PATCH "repos/$GH_REPO/releases/assets/$old" -f name="$prev" --silent
    gh api -X PATCH "repos/$GH_REPO/releases/assets/$new" -f name="$base" -f label="$label" --silent
    for id in $(echo "$a" | jq -r --arg u "$u" --arg b "$base" '.[] | select(.name != $u and .name != $b) | .id'); do
      gh api -X DELETE "repos/$GH_REPO/releases/assets/$id"
    done
    echo "saved $base: $size bytes, $label${old:+ (the one before kept as $prev)}"
    ;;
  *) echo "usage: state.sh get|put <tag> <n> <db>"; exit 2 ;;
esac
