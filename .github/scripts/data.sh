#!/usr/bin/env bash
# data/<pref>/ as a release asset (suumo/snapshot.py; README "How it runs"). git holds the pointer, data/snapshot.json
# (tag, asset, size, sha256, groups); assets are named once and never renamed or replaced, so the pointer in a commit
# always names the same bytes.
#   data.sh get <data-dir>            download the pointer's asset and unpack it into data/<pref>/ (checked: size,
#                                     sha256, groups). With FALLBACK_REF set (test runs) and the pointer or its
#                                     asset gone, the pointer of that ref (main) is taken instead.
#   data.sh put <tag> <file>          upload a new asset (unique name; the release made if missing), check it
#                                     arrived whole (size, and GitHub's sha256 digest)
#   data.sh prune <tag> <data-dir>    delete data-*.tar.zst assets of <tag> but the pointer's and the newest KEEP
# Needs GH_TOKEN and GH_REPO (contents: write for put and prune), and uv for get.
set -euo pipefail
KEEP=8   # assets kept besides the pointer's (~2 days of runs; the shard states are the deeper backup)
cmd=$1
tmp=${RUNNER_TEMP:-/tmp}

asset_id() {  # <tag> <name>: the id of a whole (uploaded) asset of that name, or nothing; an API failure fails
  gh api "repos/$GH_REPO/releases/tags/$1" --jq ".assets[] | select(.name == \"$2\" and .state == \"uploaded\") | .id"
}
main_pointer() {  # the pointer of FALLBACK_REF (main) into <ptr>
  echo "::warning::taking $FALLBACK_REF's data/snapshot.json"
  gh api "repos/$GH_REPO/contents/data/snapshot.json?ref=$FALLBACK_REF" -H 'Accept: application/vnd.github.raw' > "$1"
}

case $cmd in
  get)
    dir=$2
    ptr="$dir/snapshot.json"
    if [ ! -f "$ptr" ] && [ -n "${FALLBACK_REF:-}" ]; then main_pointer "$ptr"; fi
    [ -f "$ptr" ] || { echo "::error::no $ptr"; exit 1; }
    tag=$(jq -r .tag "$ptr") asset=$(jq -r .asset "$ptr")
    id=$(asset_id "$tag" "$asset")
    if [ -z "$id" ] && [ -n "${FALLBACK_REF:-}" ]; then
      echo "$asset is gone from $tag"
      main_pointer "$ptr"
      tag=$(jq -r .tag "$ptr") asset=$(jq -r .asset "$ptr")
      id=$(asset_id "$tag" "$asset")
    fi
    [ -n "$id" ] || { echo "::error::$tag has no asset $asset"; exit 1; }
    for i in 1 2 3; do
      gh api -H 'Accept: application/octet-stream' "repos/$GH_REPO/releases/assets/$id" > "$tmp/$asset" && break
      [ "$i" = 3 ] && { echo "::error::$asset did not download"; exit 1; }
      sleep $((i * 10))
    done
    uv run python -m suumo --data "$dir" snapshot unpack "$tmp/$asset"
    rm -f "$tmp/$asset"
    ;;
  put)
    tag=$2 file=$3
    name=$(basename "$file")
    gh release view "$tag" > /dev/null 2>&1 || gh release create "$tag" --prerelease --title "$tag" \
      --notes 'data/<pref>/ snapshots (data-*.tar.zst), named by data/snapshot.json. See README "How it runs".'
    for i in 1 2 3; do
      # a retry after a half upload: the broken asset of that name is deleted first (names are this run's own)
      old=$(gh api "repos/$GH_REPO/releases/tags/$tag" --jq ".assets[] | select(.name == \"$name\") | .id")
      [ -z "$old" ] || gh api -X DELETE "repos/$GH_REPO/releases/assets/$old"
      gh release upload "$tag" "$file" && break
      [ "$i" = 3 ] && { echo "::error::$name did not upload"; exit 1; }
      sleep $((i * 10))
    done
    read -r size digest < <(gh api "repos/$GH_REPO/releases/tags/$tag" \
      --jq ".assets[] | select(.name == \"$name\" and .state == \"uploaded\") | \"\\(.size) \\(.digest // \"\")\"") || true
    [ "${size:-}" = "$(wc -c < "$file" | tr -d ' ')" ] || { echo "::error::$name is not whole on $tag"; exit 1; }
    sha=$( (sha256sum "$file" 2> /dev/null || shasum -a 256 "$file") | cut -d' ' -f1)
    # (GitHub's digest: absent on older API answers, then the size alone)
    [ -z "${digest:-}" ] || [ "$digest" = "sha256:$sha" ] || { echo "::error::$name: digest $digest, not sha256:$sha"; exit 1; }
    echo "uploaded $name to $tag: $size bytes"
    ;;
  prune)
    tag=$2 dir=$3
    pointed=$(jq -r --arg t "$tag" 'select(.tag == $t) | .asset' "$dir/snapshot.json")
    gh api "repos/$GH_REPO/releases/tags/$tag" > "$tmp/data-release.json"
    for id in $(python3 "$(dirname "$0")/ci.py" prune "$tmp/data-release.json" --keep "$KEEP" --pointed "$pointed"); do
      gh api -X DELETE "repos/$GH_REPO/releases/assets/$id"
    done
    ;;
  *) echo "usage: data.sh get <dir> | put <tag> <file> | prune <tag> <dir>"; exit 2 ;;
esac
