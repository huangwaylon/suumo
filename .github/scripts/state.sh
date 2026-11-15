#!/usr/bin/env bash
# A crawl worker's state on a GitHub release (kalori-scrape.yml's pattern): the asset state-<n>.db.zst, or a newer
# state-<n>.db.zst.<run>-<attempt> a save left before its rename.
#   state.sh get <tag> <n> <db>    download the newest whole copy, check it, decompress to <db>
#   state.sh put <tag> <n> <db>    checkpoint, check, compress, upload under a new name, then swap it in
# Never a moment without a whole copy: the new one is uploaded and checked before the old one is deleted.
# Needs GH_TOKEN and GH_REPO (contents: write for put).
set -euo pipefail
cmd=$1 tag=$2 n=$3 db=$4
base="state-$n.db.zst"
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
    old=$(echo "$a" | jq -r --arg b "$base" '.[] | select(.name == $b) | .id')
    [ -z "$old" ] || gh api -X DELETE "repos/$GH_REPO/releases/assets/$old"
    gh api -X PATCH "repos/$GH_REPO/releases/assets/$new" -f name="$base" --silent
    for id in $(echo "$a" | jq -r --arg u "$u" --arg b "$base" '.[] | select(.name != $u and .name != $b) | .id'); do
      gh api -X DELETE "repos/$GH_REPO/releases/assets/$id"
    done
    echo "saved $base: $size bytes"
    ;;
  *) echo "usage: state.sh get|put <tag> <n> <db>"; exit 2 ;;
esac
