"""The backup check: compare each ``xml``-led project's mount with the git
host at fixed times, report differences, never correct them
(``docs/concepts/webdav-led-sync.md``).

webdav is the source of truth — it writes the mount through ``POST /files``
(in order, with retries and a failed queue) and pushes to the git host as a
backup. So a difference between the two is reported, as one of:

- ``in_sync``: git has what the mount has.
- ``git_behind``: the mount has a newer save than git's last commit —
  webdav's push hasn't arrived yet (it is retried). Logged as a warning
  only if it persists to the next check.
- ``diverged``: anything else — usually a post that never reached
  fileservice (webdav's failed queue: resend it there), else a manual
  change on the mount. An admin reload (:func:`reload_project`) is the
  fallback.
- ``failed``: the git host couldn't be reached or read; the error is kept.

Git writes the mount only on the first fill of a project without a
checkout (:func:`first_fill`) and on an admin reload.

Locks: ``git_locks`` serialize git operations per project (a fetch can be
slow); ``locks`` are the ones ``POST /files`` holds, taken only for the
comparison and the reload's reset, so a slow git host doesn't block saves.
"""

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Callable

from sqlmodel import Session

from ediarum_sdk.projects import ProjectInfo, ProjectsClient

from . import db, git_sync
from .config import Settings
from .locks import ProjectLocks

log = logging.getLogger("uvicorn.error")


def _as_aware_utc(dt: datetime) -> datetime:
    "SQLite round-trips a datetime as naive -- normalize before any arithmetic, never compare naive/aware."
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)


def _fill(settings: Settings, info: ProjectInfo, session_factory: Callable[[], Session]) -> bool:
    "First fill without taking locks (callers hold both)."
    if not git_sync.ensure_clone(settings.mount_root, info.slug, info.repo_url, info.branch, info.sparse_path):
        return False
    commit_sha = git_sync.head_commit(git_sync.checkout_path(settings.mount_root, info.slug))
    with session_factory() as session:
        db.record_verified(session, info.slug, commit_sha)
    log.info("first fill of %s from git at %s", info.slug, commit_sha[:12])
    return True


def first_fill(settings: Settings, info: ProjectInfo, session_factory: Callable[[], Session],
               git_locks: ProjectLocks, locks: ProjectLocks) -> bool:
    """Clone `info`'s project if it has no checkout yet; files posted before
    stay (see :func:`git_sync.ensure_clone`). Returns whether it cloned."""
    if git_sync.has_checkout(settings.mount_root, info.slug):
        return False
    with git_locks.acquire(info.slug), locks.acquire(info.slug):
        return _fill(settings, info, session_factory)


def first_fill_all(settings: Settings, projects: ProjectsClient, session_factory: Callable[[], Session],
                   git_locks: ProjectLocks, locks: ProjectLocks) -> None:
    "First fill of every project without a checkout; a failing one is logged, the others go on."
    for info in projects.all().values():
        try:
            first_fill(settings, info, session_factory, git_locks, locks)
        except Exception as exc:
            log.warning("first fill of %s failed: %s", info.slug, exc)


def _classify(differences: list[git_sync.Difference], remote: git_sync.RemoteHead,
              state: db.SyncState | None) -> str:
    "``in_sync``, ``git_behind`` (webdav's last claim is newer than git's tip) or ``diverged``."
    if not differences:
        return "in_sync"
    if state is not None and state.claimed_commit is not None:
        if state.claimed_commit == remote.commit:
            return "diverged"  # git has webdav's last commit, yet the files differ
        if state.claimed_at is not None and _as_aware_utc(state.claimed_at) > remote.committed_at:
            return "git_behind"
    return "diverged"


def check_project(settings: Settings, info: ProjectInfo, session_factory: Callable[[], Session],
                  git_locks: ProjectLocks, locks: ProjectLocks) -> db.GitCheck:
    """Check one project against the git host (first fill if it has no
    checkout yet) and record the result. Never raises for git trouble: that
    is the ``failed`` result."""
    remote = None
    try:
        with git_locks.acquire(info.slug):
            if not git_sync.has_checkout(settings.mount_root, info.slug):
                with locks.acquire(info.slug):
                    _fill(settings, info, session_factory)
            remote = git_sync.fetch_remote(settings.mount_root, info.slug, info.branch)
            with locks.acquire(info.slug):
                differences = git_sync.compare_with_remote(
                    settings.mount_root, info.slug, info.branch, info.sparse_path
                )
                with session_factory() as session:
                    state = session.get(db.SyncState, info.slug)
                    if state is not None:
                        session.expunge(state)
    except Exception as exc:
        with session_factory() as session:
            row = db.record_check(session, info.slug, "failed", remote_commit=remote and remote.commit,
                                  error=str(exc))
        log.warning("check %s failed: %s", info.slug, exc)
        return row

    result = _classify(differences, remote, state)
    with session_factory() as session:
        if result == "in_sync":
            db.record_verified(session, info.slug, remote.commit)  # the mount's content is this commit's
        row = db.record_check(  # last: it refreshes the row it returns
            session, info.slug, result, remote_commit=remote.commit,
            differences=[{"status": d.status, "path": d.path} for d in differences],
        )

    persisting = _as_aware_utc(row.result_since) < _as_aware_utc(row.checked_at)
    if result == "diverged" or (result == "git_behind" and persisting):
        log.warning("check %s: %s, %d file(s) differ from git (since %s)", info.slug, result,
                    row.difference_count, _as_aware_utc(row.result_since).isoformat(timespec="minutes"))
    else:
        log.info("check %s: %s", info.slug, result)
    return row


def check_all(settings: Settings, projects: ProjectsClient, session_factory: Callable[[], Session],
              git_locks: ProjectLocks, locks: ProjectLocks) -> list[db.GitCheck]:
    "Check every project the platform knows about; raises only if the registry is unavailable."
    return [
        check_project(settings, info, session_factory, git_locks, locks)
        for info in projects.all().values()
    ]


def reload_project(settings: Settings, info: ProjectInfo, session_factory: Callable[[], Session],
                   git_locks: ProjectLocks, locks: ProjectLocks) -> db.GitCheck:
    """An admin's reload: reset the project's mount to git (``fetch`` +
    ``reset --hard`` + ``clean -fd``), then check it. Discards whatever on
    the mount differs from git — callers refuse it on ``git_behind`` unless
    forced. Raises :class:`git_sync.GitError` if git can't be read."""
    with git_locks.acquire(info.slug), locks.acquire(info.slug):
        commit_sha = git_sync.pull(settings.mount_root, info.slug, info.repo_url, info.branch, info.sparse_path)
        with session_factory() as session:
            db.record_verified(session, info.slug, commit_sha)
    log.warning("reload of %s from git at %s (admin)", info.slug, commit_sha[:12])
    return check_project(settings, info, session_factory, git_locks, locks)


def next_check_at(now: datetime, settings: Settings) -> datetime | None:
    "The first of ``settings.check_times`` (in ``check_timezone``) after `now`; ``None`` without check times."
    if not settings.check_times:
        return None
    local = now.astimezone(settings.check_timezone)
    for days in (0, 1):
        day = (local + timedelta(days=days)).date()
        for t in settings.check_times:
            candidate = datetime.combine(day, t, tzinfo=settings.check_timezone)
            if candidate > local:
                return candidate
    raise AssertionError("unreachable: tomorrow always has a check time")


async def run_scheduled(settings: Settings, projects: ProjectsClient, session_factory: Callable[[], Session],
                        git_locks: ProjectLocks, locks: ProjectLocks) -> None:
    """The app's background task: the first fill of projects without a
    checkout, then a check of every project at each of ``CHECK_TIMES``,
    until cancelled. Git runs in a worker thread, so ``POST /files`` keeps
    being served. A restart doesn't trigger a check (no burst of fetches on
    every deploy). A round that fails as a whole (the registry is unreachable
    and nothing is cached) is logged; the next time tries again."""
    try:
        await asyncio.to_thread(first_fill_all, settings, projects, session_factory, git_locks, locks)
    except Exception as exc:
        log.warning("first fill failed: %s", exc)
    while (when := next_check_at(datetime.now(timezone.utc), settings)) is not None:
        await asyncio.sleep(max(0.0, (when - datetime.now(timezone.utc)).total_seconds()))
        try:
            rows = await asyncio.to_thread(check_all, settings, projects, session_factory, git_locks, locks)
            counts: dict[str, int] = {}
            for row in rows:
                counts[row.result] = counts.get(row.result, 0) + 1
            log.info("check: %s", ", ".join(f"{n} {r}" for r, n in sorted(counts.items())) or "no projects")
        except Exception as exc:
            log.warning("check round failed: %s", exc)
