"""Git mechanics: the project checkouts on the shared mount, their backup
check against the git host, and direct file writes (the ``POST /files``
fast path — see :mod:`ediarum_fileservice.app`).

webdav is the source of truth; the git host holds webdav's pushes as a
backup (``docs/concepts/webdav-led-sync.md``). So git writes the mount only in
two places: :func:`ensure_clone`, the first fill of a project that has no
checkout yet, and :func:`pull`, an admin's explicit reload. The scheduled
check (:mod:`ediarum_fileservice.check`) only reads: :func:`fetch_remote`
moves ``origin/<branch>``, :func:`compare_with_remote` diffs the working
tree against it — ``HEAD``, the index and the files stay as they are.

The clone side is ported from ediarum.index's former
``backend/sync_xml_sources.py``, generalized to any mount root and
parameterized by :class:`ediarum_sdk.projects.ProjectInfo`. The
sparse/partial/shallow/non-cone checkout strategy and its reasoning are
unchanged — see :func:`ensure_clone`.

This service never pushes to git: webdav owns every write to the dataset
(and its own push to the git host). ``app``-led projects keep their
registers in ``index``'s database, but their texts are files here like any
other project's.
"""

import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path


class GitError(Exception):
    "A git subprocess failed; message is its stderr."


class PathError(Exception):
    "A caller-supplied path would escape the project's checkout."


def _run(*args: str, check: bool = True, env: dict | None = None) -> subprocess.CompletedProcess:
    "Run a command; ``GitError`` with its stderr if it fails and ``check``."
    result = subprocess.run(args, capture_output=True, text=True, env=env)
    if check and result.returncode != 0:
        raise GitError(f"{' '.join(args)}: {result.stderr.strip()}")
    return result


def checkout_path(root: Path, slug: str) -> Path:
    "Where ``slug``'s git checkout lives under the shared mount — see module docstring."
    return root / slug


def head_commit(target: Path) -> str:
    "The checkout's ``HEAD`` commit."
    return _run("git", "-C", str(target), "rev-parse", "HEAD").stdout.strip()


def has_checkout(root: Path, slug: str) -> bool:
    "Whether ``slug`` has a git checkout on the mount."
    return (checkout_path(root, slug) / ".git").exists()


def ensure_clone(root: Path, slug: str, url: str, branch: str, sparse_path: str) -> bool:
    """Set up `slug`'s checkout if it doesn't exist locally yet (the first
    fill); otherwise a no-op. Returns whether it cloned.

    Sparse, partial, shallow, non-cone: only ``sparse_path`` is ever
    fetched (the whole repository when it is ``""``) — a project's upstream
    repo may hold unrelated document types the registers never read, and in
    at least one real case a large unrelated archive at the repo root,
    which **cone** mode would materialize anyway regardless of the sparse
    pattern (by design). A ``**`` glob in non-cone mode gets exactly
    ``sparse_path`` and nothing else.

    ``init`` + ``fetch`` + ``checkout -f`` rather than ``clone``, because the
    directory may already hold files that ``POST /files`` wrote before the
    first fill (``git clone`` refuses a non-empty target) — e.g. while the
    git host was unreachable. Those are webdav's, so they win: they are set
    aside, git's version is checked out, and they are put back on top (the
    next check reports them if git doesn't have them yet). A failed setup
    removes ``.git`` again, so the next attempt starts over.
    """
    target = checkout_path(root, slug)
    if (target / ".git").exists():
        return False
    target.mkdir(parents=True, exist_ok=True)
    posted = [p for p in target.rglob("*") if p.is_file()]
    git = ("git", "-C", str(target))
    with tempfile.TemporaryDirectory(dir=root, prefix=f".{slug}-posted-") as aside:
        for p in posted:
            dest = Path(aside) / p.relative_to(target)
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(p, dest)
        try:
            _run("git", "init", "-q", str(target))
            _run(*git, "remote", "add", "origin", url)
            _run(*git, "config", "remote.origin.promisor", "true")
            _run(*git, "config", "remote.origin.partialclonefilter", "blob:none")
            _run(*git, "fetch", "-q", "--filter=blob:none", "--depth=1", "origin", branch)
            if sparse_path:
                _run(*git, "sparse-checkout", "init", "--no-cone")
                _run(*git, "sparse-checkout", "set", f"{sparse_path}/**")
            _run(*git, "checkout", "-q", "-f", "-B", branch, f"origin/{branch}")
        except GitError:
            shutil.rmtree(target / ".git", ignore_errors=True)
            raise
        finally:
            for p in posted:
                dest = target / p.relative_to(target)
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(Path(aside) / p.relative_to(target), dest)
    return True


@dataclass(frozen=True)
class RemoteHead:
    "The tip of ``origin/<branch>`` after a fetch."

    commit: str
    committed_at: datetime


def fetch_remote(root: Path, slug: str, branch: str) -> RemoteHead:
    """Fetch `slug`'s ``origin/<branch>`` (shallow) and return its tip —
    without touching ``HEAD``, the index or the working tree. Needs a
    checkout (:func:`ensure_clone`)."""
    git = ("git", "-C", str(checkout_path(root, slug)))
    _run(*git, "fetch", "-q", "--depth=1", "origin", branch)
    commit, committed_at = _run(*git, "log", "-1", "--format=%H %cI", f"origin/{branch}").stdout.split()
    return RemoteHead(commit, datetime.fromisoformat(committed_at))


@dataclass(frozen=True)
class Difference:
    "One file that differs between the mount and git."

    status: str  # git's name-status letter: M (modified), A (only on the mount), D (only in git), T (type)
    path: str


def compare_with_remote(root: Path, slug: str, branch: str, sparse_path: str) -> list[Difference]:
    """How `slug`'s working tree differs from ``origin/<branch>`` (as last
    fetched), limited to ``sparse_path`` if set: modified, added (not in
    git) and deleted (only in git) files.

    Uses a temporary index (``GIT_INDEX_FILE``), so the checkout's own index
    and ``HEAD`` stay as they are: ``read-tree`` the remote tree, ``add -A``
    the working tree on top, ``diff --cached`` against the remote tree.
    Compares blob ids only (``--no-renames``), so the partial clone needs no
    blob downloads. Ignored files are not compared.
    """
    target = checkout_path(root, slug)
    git = ("git", "-C", str(target))
    ref = f"origin/{branch}"
    pathspec = sparse_path or "."
    with tempfile.TemporaryDirectory() as tmp:
        env = {**os.environ, "GIT_INDEX_FILE": str(Path(tmp) / "index")}
        _run(*git, "read-tree", ref, env=env)
        _run(*git, "add", "-A", "--", pathspec, env=env)
        out = _run(*git, "diff", "--cached", "--no-renames", "--name-status", ref, "--", pathspec, env=env).stdout
    return [Difference(*line.split("\t", 1)) for line in out.splitlines() if line]


def pull(root: Path, slug: str, url: str, branch: str, sparse_path: str) -> str:
    """Reload ``slug``'s checkout from git: clone if needed, then ``fetch``
    + ``reset --hard`` + ``clean -fd``. Returns the resulting ``HEAD``.
    Discards whatever on the mount differs from git — an admin's explicit
    reload only, never automatic (see the module docstring)."""
    target = checkout_path(root, slug)
    ensure_clone(root, slug, url, branch, sparse_path)
    _run("git", "-C", str(target), "fetch", "--depth=1", "origin", branch)
    _run("git", "-C", str(target), "reset", "--hard", f"origin/{branch}")
    _run("git", "-C", str(target), "clean", "-fdq")
    return head_commit(target)



# --- direct file writes: the POST /files fast path -------------------------

def safe_target(root: Path, slug: str, path: str) -> Path:
    """Resolve ``path`` (as sent in a ``POST /files`` payload) to a location
    inside ``slug``'s checkout, raising :class:`PathError` if it would land
    anywhere else — the only validation standing between an external
    caller's raw ``path`` string and the filesystem, now that content
    arrives without git having structured or bounded it first.

    ``path`` is taken as relative to the project's checkout root (which
    `ediarum.webdav` calls ``data_folder_location``) — **an assumption, not
    yet confirmed against a real payload**.
    """
    target = checkout_path(root, slug)
    resolved = (target / path).resolve()
    try:
        resolved.relative_to(target.resolve())
    except ValueError:
        raise PathError(f"{path!r} escapes {slug}'s checkout")
    return resolved


def write_file(root: Path, slug: str, path: str, content: bytes) -> Path:
    "Write `content` to `path` inside `slug`'s checkout, creating parent directories as needed."
    target = safe_target(root, slug, path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(content)
    return target


def delete_file(root: Path, slug: str, path: str) -> bool:
    "Remove `path` from `slug`'s checkout. Returns False if it wasn't there (not an error — already gone)."
    target = safe_target(root, slug, path)
    if not target.exists():
        return False
    target.unlink()
    return True


def move_file(root: Path, slug: str, previous_path: str, path: str) -> Path:
    """Rename `previous_path` to `path` inside `slug`'s checkout.

    If `previous_path` no longer exists locally, falls back to just
    ensuring the destination directory exists — the content itself arrives
    through the ordinary `write_file` path on the same event when the
    sender includes it; otherwise the next check reports the difference.
    """
    source = safe_target(root, slug, previous_path)
    dest = safe_target(root, slug, path)
    dest.parent.mkdir(parents=True, exist_ok=True)
    if source.exists():
        source.rename(dest)
    return dest
