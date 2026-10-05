"""Commit (and optionally push) the exported data/ folder after a run."""
import subprocess
from pathlib import Path


def _git(root, *args):
    return subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True)


def commit_data(root: Path, run_id: str, push=False, log=print):
    """Commit changes under data/ only; with push, push the branch (also retries an earlier failed push).
    Returns True if a commit was made. Failures are logged, never raised: the run's data is already saved."""
    committed = False
    if _git(root, "add", "--all", "data").returncode != 0:
        log("git: add failed")
    elif _git(root, "diff", "--cached", "--quiet", "--", "data").returncode == 0:
        log("git: data unchanged, nothing to commit")
    else:
        r = _git(root, "commit", "-q", "-m", f"data: run {run_id}", "--", "data")
        committed = r.returncode == 0
        log(f"git: committed data for run {run_id}" if committed else f"git: commit failed: {r.stderr.strip()}")
    if push:
        r = _git(root, "push", "-q")
        log("git: pushed" if r.returncode == 0 else f"git: push failed (retried next run): {r.stderr.strip()}")
    return committed
