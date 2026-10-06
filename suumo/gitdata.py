"""Commit (and optionally push) the exported data/ and the geocode cache geo/ after a run. A push rebuilds the
site (GitHub Actions)."""
import subprocess
from pathlib import Path

PATHS = ("data", "geo")


def _git(root, *args):
    return subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True)


def pull(root, log=print):
    """Bring in what changed on GitHub (saved.json) before a run; a failure is logged, the run goes on."""
    r = _git(root, "pull", "-q", "--rebase", "--autostash")
    if r.returncode != 0:
        log(f"git: pull failed: {r.stderr.strip()}")


def commit_data(root: Path, run_id: str, push=False, log=print):
    """Commit changes under data/ and geo/ only; with push, push the branch (also retries an earlier failed push).
    Returns True if a commit was made. Failures are logged, never raised: the run's data is already saved."""
    committed = False
    paths = [p for p in PATHS if (Path(root) / p).exists()]
    if _git(root, "add", "--all", *paths).returncode != 0:
        log("git: add failed")
    elif _git(root, "diff", "--cached", "--quiet", "--", *paths).returncode == 0:
        log("git: data unchanged, nothing to commit")
    else:
        r = _git(root, "commit", "-q", "-m", f"data: run {run_id}", "--", *paths)
        committed = r.returncode == 0
        log(f"git: committed data for run {run_id}" if committed else f"git: commit failed: {r.stderr.strip()}")
    if push:
        pull(root, log)  # the Saved workflow may have pushed meanwhile
        r = _git(root, "push", "-q")
        log("git: pushed" if r.returncode == 0 else f"git: push failed (retried next run): {r.stderr.strip()}")
    return committed
