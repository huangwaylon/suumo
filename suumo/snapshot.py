"""data/'s prefecture folders as one release asset, with git holding the pointer (README "How it runs").

git keeps data/events/, data/fx.json, data/canary/ and data/snapshot.json; everything under data/<pref>/ (the
listings, page side files, areas.json) travels as data-<stamp>.tar.zst on the release suumo-data (suumo-data-test off
main). data/snapshot.json names the asset (tag, asset, size, sha256) and lists its groups: one per <pref>/<child>
(a type, removed, pages, areas.json) with its file count, record count (listing .jsonl lines; pages: none) and a hash.
A `data: run` commit's diff of snapshot.json shows which groups the run changed (the determinism check that
`git diff -- data` was); the group counts feed the gate (`check`).

pack is deterministic (sorted members, fixed metadata, zstd at a pinned version and level, one thread): the same
data/ gives the same bytes. unpack trusts nothing: size and sha256 against the pointer, regular files and folders
under a prefecture only, then the manifest of what it wrote against the pointer's groups.
"""
import hashlib
import json
import shutil
import tarfile
from pathlib import Path

import zstandard

from .export import prefectures
from .scope import PREFS
from .util import write_atomic

POINTER = "snapshot.json"   # in data/: the asset and its manifest (committed)
LEVEL = 10                  # zstd level (zstandard pinned in pyproject.toml): ~300 MB, seconds to unpack
DROP = 0.5                  # the gate: a prefecture losing more than this share of its records since the last
MIN_RECORDS = 200           # snapshot, when it had at least this many, is refused (a half-empty export; the
                            # per-area suspect guards catch real-looking drops long before); `--force` overrides


class SnapshotError(Exception):
    """A snapshot that doesn't match its pointer, or a data/ the gate refuses."""


def _members(data_dir):
    """(relative path, absolute path) of every file under data/<pref>/, sorted by relative path."""
    data_dir = Path(data_dir)
    out = []
    for pref in prefectures(data_dir):
        for p in (data_dir / pref).rglob("*"):
            if p.is_file():
                out.append((p.relative_to(data_dir).as_posix(), p))
    return sorted(out)


def manifest(data_dir):
    """{"<pref>/<child>": {"files", "records"?, "sha256"}}: the hash covers each file's path and bytes, in order."""
    groups = {}
    for rel, path in _members(data_dir):
        parts = rel.split("/")
        key = "/".join(parts[:2])
        g = groups.setdefault(key, {"files": 0, "h": hashlib.sha256()})
        body = path.read_bytes()
        g["files"] += 1
        g["h"].update(rel.encode() + b"\0" + str(len(body)).encode() + b"\0" + body)
        if rel.endswith(".jsonl") and parts[1] != "pages":
            g["records"] = g.get("records", 0) + sum(1 for line in body.splitlines() if line.strip())
    return {k: {**{f: v for f, v in g.items() if f != "h"}, "sha256": g["h"].hexdigest()}
            for k, g in sorted(groups.items())}


def records_by_pref(groups):
    out = {}
    for key, g in groups.items():
        pref = key.split("/")[0]
        out[pref] = out.get(pref, 0) + g.get("records", 0)
    return out


def check(old, new, prefs):
    """The gate before an upload: what's wrong with the new groups (against the old pointer's; old may be empty)."""
    problems = []
    before, after = records_by_pref(old), records_by_pref(new)
    for pref in sorted(prefs):
        if not after.get(pref):
            problems.append(f"{pref}: no records")
        elif before.get(pref, 0) >= MIN_RECORDS and after[pref] < (1 - DROP) * before[pref]:
            problems.append(f"{pref}: {before[pref]} -> {after[pref]} records")
    return problems


def pack(data_dir, out):
    """data/<pref>/** -> a deterministic tar.zst at out (streamed: data/ is gigabytes): (size, sha256)."""
    out = Path(out)
    tmp = out.with_name(out.name + ".tmp")
    with open(tmp, "wb") as f, zstandard.ZstdCompressor(level=LEVEL, threads=0).stream_writer(f) as z, \
            tarfile.open(fileobj=z, mode="w|", format=tarfile.PAX_FORMAT) as tar:
        dirs = set()
        for rel, path in _members(data_dir):
            parts = rel.split("/")
            for i in range(1, len(parts)):
                d = "/".join(parts[:i])
                if d not in dirs:
                    dirs.add(d)
                    tar.addfile(_info(d, tarfile.DIRTYPE, 0o755))
            info = _info(rel, tarfile.REGTYPE, 0o644)
            info.size = path.stat().st_size
            with open(path, "rb") as body:
                tar.addfile(info, body)
    tmp.replace(out)
    return out.stat().st_size, sha256_file(out)


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _info(name, kind, mode):
    info = tarfile.TarInfo(name)
    info.type, info.mode, info.mtime, info.uid, info.gid, info.uname, info.gname = kind, mode, 0, 0, 0, "", ""
    return info


def _safe(name):
    parts = name.split("/")
    return (not name.startswith("/") and "\\" not in name and all(p not in ("", ".", "..") for p in parts)
            and parts[0] in PREFS)


def unpack(archive, data_dir, pointer):
    """Check archive against the pointer (size, sha256), extract it into a scratch folder, check what was written
    against the pointer's groups, then put its prefectures in place of data/<pref>/. Raises SnapshotError (data/ is
    left as it was); data/events and the top-level files are never touched."""
    archive = Path(archive)
    if archive.stat().st_size != pointer["size"] or sha256_file(archive) != pointer["sha256"]:
        raise SnapshotError(f"{archive}: size or sha256 differ from {POINTER} ({pointer['asset']})")
    data_dir = Path(data_dir)
    data_dir.mkdir(parents=True, exist_ok=True)
    scratch = data_dir / ".unpack"
    shutil.rmtree(scratch, ignore_errors=True)
    scratch.mkdir()
    try:
        try:
            with open(archive, "rb") as f, zstandard.ZstdDecompressor().stream_reader(f) as z, \
                    tarfile.open(fileobj=z, mode="r|") as tar:
                for m in tar:
                    _extract(tar, m, scratch, archive)
        except (tarfile.TarError, zstandard.ZstdError, EOFError) as e:
            raise SnapshotError(f"{archive}: unreadable: {e}") from None
        got = manifest(scratch)
        if got != pointer["groups"]:
            diff = sorted(k for k in set(got) | set(pointer["groups"]) if got.get(k) != pointer["groups"].get(k))
            raise SnapshotError(f"{archive}: unpacked data differs from {POINTER} in {diff[:10]}")
        for pref in prefectures(data_dir):
            shutil.rmtree(data_dir / pref)
        for pref in prefectures(scratch):
            (scratch / pref).rename(data_dir / pref)
    finally:
        shutil.rmtree(scratch, ignore_errors=True)
    return got


def _extract(tar, m, dest, archive):
    if not _safe(m.name) or not (m.isfile() or m.isdir()) or (m.isfile() and "/" not in m.name):
        raise SnapshotError(f"{archive}: unexpected member {m.name!r}")
    try:
        tarfile.data_filter(m, str(dest))   # (the stdlib's checks too: no links, nothing outside)
    except tarfile.FilterError as e:
        raise SnapshotError(f"{archive}: unexpected member {m.name!r}: {e}") from None
    target = dest / m.name
    if m.isdir():
        target.mkdir(parents=True, exist_ok=True)
    else:
        target.parent.mkdir(parents=True, exist_ok=True)
        with open(target, "wb") as out:
            shutil.copyfileobj(tar.extractfile(m), out, 1 << 20)


def load_pointer(data_dir):
    path = Path(data_dir) / POINTER
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


def write_pointer(data_dir, pointer):
    body = json.dumps(pointer, ensure_ascii=False, indent=1, sort_keys=True) + "\n"
    write_atomic(Path(data_dir) / POINTER, body.encode())
