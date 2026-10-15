"""Commit (and optionally push) the exported data/ and the geocode cache geo/ after a run. A push rebuilds the
site (GitHub Actions)."""
import subprocess
import time
from pathlib import Path

PATHS = ("data", "geo")
STALE_LOCK = 600  # seconds: an index.lock this old was left by a killed git, not held by a running one


def _git(root, *args):
    return subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True)


def _git_path(root, name):
    """A path inside the repository's git directory (.git/<name>, or the worktree's)."""
    return Path(root) / _git(root, "rev-parse", "--git-path", name).stdout.strip()


def _unstick(root, log):
    """Remove an index.lock left by a git process that was killed (every later commit would fail on it)."""
    lock = _git_path(root, "index.lock")
    if lock.is_file() and time.time() - lock.stat().st_mtime > STALE_LOCK:
        lock.unlink()
        log(f"git: removed a stale {lock} (older than {STALE_LOCK // 60} min)")


def pull(root, log=print):
    """Bring in what changed on GitHub (saved.json) before a run; a failure is logged, the run goes on. A rebase the
    failed pull leaves half-done is aborted (else every later commit lands in the middle of it)."""
    _unstick(root, log)
    r = _git(root, "pull", "-q", "--rebase", "--autostash")
    if r.returncode != 0:
        log(f"git: pull failed: {r.stderr.strip()}")
        if any(_git_path(root, p).exists() for p in ("rebase-merge", "rebase-apply")):
            a = _git(root, "rebase", "--abort")
            log("git: !!! the pull left a rebase in progress; "
                + ("aborted it (local commits kept, not rebased)" if a.returncode == 0
                   else f"`git rebase --abort` failed, fix by hand: {a.stderr.strip()}"))


def commit_data(root: Path, run_id: str, push=False, log=print):
    """Commit changes under data/ and geo/ only; with push, push the branch (also retries an earlier failed push).
    Returns True if a commit was made. Failures are logged, never raised: the run's data is already saved."""
    _unstick(root, log)
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
