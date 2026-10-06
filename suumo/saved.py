"""The shared saved list (saved.json, git-tracked): listings anyone can save, kept after they leave SUUMO.

Saving goes through GitHub: the site's heart opens an issue titled `save <type>:<id>` (or `unsave ...`), and
the Saved workflow (.github/workflows/saved.yml) applies it with `python -m suumo saved "<title>"`, commits
saved.json and rebuilds the site. Runs read the list to decide what to keep (pipeline.Pipeline.saved).
"""
import json
import re
from pathlib import Path

from .archive import write_atomic
from .parse import TYPES

ACTION = re.compile(rf"^\s*(save|unsave)\s+({'|'.join(TYPES)}):(\d{{1,12}})\s*$")


def load(path):
    """The saved keys ("type:id"); an empty set when there is no list yet."""
    try:
        return set(json.loads(Path(path).read_text(encoding="utf-8")))
    except (OSError, ValueError):
        return set()


def apply(path, title):
    """Apply an issue title ("save used_condo:123"); returns the message for the issue, or None if it isn't one."""
    m = ACTION.match(title or "")
    if not m:
        return None
    action, key = m.group(1), f"{m.group(2)}:{m.group(3)}"
    keys = load(path)
    before = key in keys
    if action == "save":
        keys.add(key)
    else:
        keys.discard(key)
    write_atomic(path, (json.dumps(sorted(keys), indent=0) + "\n").encode())
    done = (action == "save") != before
    return f"{'saved' if action == 'save' else 'unsaved'} {key}" + ("" if done else " (no change)")
