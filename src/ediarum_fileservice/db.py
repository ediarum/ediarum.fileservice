"""Database engine and the sync-state table.

The API-key store (``ediarum_sdk.auth.ApiKey``) lives in this same
database — importing it here is what registers it on ``SQLModel.metadata``
so :func:`init_db` creates it too.
"""

from datetime import datetime, timezone
from sqlalchemy import JSON, Column
from sqlmodel import Field, Session, SQLModel, create_engine

from ediarum_sdk.auth import ApiKey  # noqa: F401 -- registers sdk_api_key

def utcnow() -> datetime:
    "Now, timezone-aware (UTC)."
    return datetime.now(timezone.utc)


def _aware(dt: datetime) -> datetime:
    "SQLite round-trips a datetime as naive (UTC)."
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)


CHECK_RESULTS = ("in_sync", "git_behind", "diverged", "failed")
MAX_DIFFERENCES = 200  # stored per check; difference_count has the full number


class SyncState(SQLModel, table=True):
    """One project's sync state — backs ``GET /projects/{slug}/status``.

    Two independent facts, not one: ``claimed_*`` is what the last
    ``POST /files`` said (written immediately, before anything has checked
    it against git); ``verified_*`` is the state last confirmed against the
    git host — by the first fill, an admin reload, or a check that found the
    mount and git in sync (:mod:`ediarum_fileservice.check`). The index and
    id watchers compare both against their last look to skip unchanged
    projects; every post changes ``claimed_commit``, every write from git
    ``verified_commit``.
    """

    __tablename__ = "sync_state"

    project: str = Field(primary_key=True)
    claimed_commit: str | None = Field(default=None)
    claimed_at: datetime | None = Field(default=None)
    verified_commit: str | None = Field(default=None)
    verified_at: datetime | None = Field(default=None)

    @property
    def verified(self) -> bool:
        """Has the mount been confirmed in git since the last claim? True after
        a first fill, reload or in-sync check that came after the last post."""
        if self.verified_at is None:
            return False
        return self.claimed_at is None or _aware(self.verified_at) >= _aware(self.claimed_at)


def make_engine(url: str):
    "Engine for ``url``; SQLite usable from several threads."
    connect_args = {"check_same_thread": False} if url.startswith("sqlite") else {}
    return create_engine(url, connect_args=connect_args)


def init_db(engine) -> None:
    "Create missing tables."
    SQLModel.metadata.create_all(engine)


def record_claim(session: Session, project: str, commit_sha: str) -> None:
    "A POST /files just wrote content claiming `commit_sha` — record it, optimistically, unverified."
    state = session.get(SyncState, project) or SyncState(project=project)
    state.claimed_commit = commit_sha
    state.claimed_at = utcnow()
    session.add(state)
    session.commit()


def record_verified(session: Session, project: str, commit_sha: str) -> None:
    "The mount's state is confirmed in git at `commit_sha` (first fill, reload, or an in-sync check)."
    state = session.get(SyncState, project) or SyncState(project=project)
    state.verified_commit = commit_sha
    state.verified_at = utcnow()
    # a verified pull also settles any pending claim at or before this commit
    if state.claimed_commit is None:
        state.claimed_commit = commit_sha
        state.claimed_at = state.verified_at
    session.add(state)
    session.commit()


class GitCheck(SQLModel, table=True):
    """The last backup check of one project: the mount compared with the git
    host (``docs/concepts/webdav-led-sync.md``). One row per project, replaced at
    each check. A table of its own, not columns on ``sync_state``:
    :func:`init_db` creates a new table in an existing database, but never
    adds columns."""

    __tablename__ = "git_check"

    project: str = Field(primary_key=True)
    checked_at: datetime
    result: str  # one of CHECK_RESULTS
    result_since: datetime  # when the current result first appeared (a persisting git_behind)
    remote_commit: str | None = Field(default=None)
    differences: list[dict] = Field(default_factory=list, sa_column=Column(JSON))  # [{status, path}], capped
    difference_count: int = 0
    error: str | None = Field(default=None)


def record_check(
    session: Session, project: str, result: str, *, remote_commit: str | None = None,
    differences: list[dict] | None = None, error: str | None = None,
) -> GitCheck:
    "Store a check's outcome, keeping ``result_since`` while the result stays the same."
    now = utcnow()
    differences = differences or []
    row = session.get(GitCheck, project)
    since = row.result_since if row is not None and row.result == result else now
    row = row or GitCheck(project=project, checked_at=now, result=result, result_since=since)
    row.checked_at = now
    row.result = result
    row.result_since = since
    row.remote_commit = remote_commit
    row.differences = differences[:MAX_DIFFERENCES]
    row.difference_count = len(differences)
    row.error = error
    session.add(row)
    session.commit()
    session.refresh(row)
    return row
