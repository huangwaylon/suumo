"""Small helpers shared by the file writers."""
from pathlib import Path


def write_atomic(path, data: bytes):
    """Write via a temporary file and rename, so readers never see a half-written file."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_bytes(data)
    tmp.replace(path)
