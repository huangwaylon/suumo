"""Raw HTML archive (local, not in git) so any field can be re-parsed later without re-crawling.

  archive/<pref>/list/<YYYYMMDD>/<type>/<slug>_p<N>.html.gz   daily search-result snapshots (kept LIST_DAYS)
  archive/<pref>/detail/<type>/<id>.html.gz                   latest copy of each listing page
"""
import gzip
import shutil
from datetime import timedelta
from pathlib import Path

LIST_DAYS = 14


def write_atomic(path, data: bytes):
    """Write via a temporary file and rename, so readers never see a half-written file."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_bytes(data)
    tmp.replace(path)


class Archive:
    def __init__(self, root):
        self.root = Path(root)

    def save_list(self, pref, day, type_key, name, html):
        write_atomic(self.root / pref / "list" / day / type_key / f"{name}.html.gz", gzip.compress(html.encode()))

    def list_days(self, pref):
        d = self.root / pref / "list"
        return sorted(p.name for p in d.iterdir()) if d.exists() else []

    def list_pages(self, pref, day, type_key):
        d = self.root / pref / "list" / day / type_key
        return sorted(d.glob("sc_*.html.gz")) if d.exists() else []

    def detail_path(self, pref, type_key, lid):
        return self.root / pref / "detail" / type_key / f"{lid}.html.gz"

    def save_detail(self, pref, type_key, lid, html):
        write_atomic(self.detail_path(pref, type_key, lid), gzip.compress(html.encode()))

    def delete_detail(self, pref, type_key, lid):
        self.detail_path(pref, type_key, lid).unlink(missing_ok=True)

    @staticmethod
    def read(path):
        return gzip.decompress(Path(path).read_bytes()).decode("utf-8")

    def prune_lists(self, pref, today):
        cutoff = (today - timedelta(days=LIST_DAYS)).strftime("%Y%m%d")
        for day in self.list_days(pref):
            if day < cutoff:
                shutil.rmtree(self.root / pref / "list" / day)

    def drop_pref(self, pref):
        shutil.rmtree(self.root / pref, ignore_errors=True)

