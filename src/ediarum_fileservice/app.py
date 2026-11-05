"""The FastAPI app: ``POST /files`` (webdav's hot path — see
``docs/concepts/design.md``), and a small status surface for platform admins.

``index`` and ``id`` receive the same ``POST /files`` from webdav
directly and ingest it themselves; this app only keeps the shared mount
current and never notifies them.

At startup the app starts :func:`ediarum_fileservice.check.run_scheduled`
as a background task: the first fill of projects without a checkout, then
the backup check at each of ``CHECK_TIMES``. Admins see the results in
``GET /projects/status``, run a check now (``POST /projects/{slug}/check``)
or reload a project from git (``POST /projects/{slug}/reload``). Every
project is handled alike: the platform project registry's ``xml``/``app``
switch concerns ``index``'s registers only, the texts are files either way.
"""

import asyncio
import logging
from contextlib import asynccontextmanager, suppress
from datetime import datetime
from typing import Annotated

import httpx2 as httpx
from fastapi import Depends, FastAPI, File, Form, HTTPException, Query, Request, UploadFile, status
from pydantic import BaseModel
from sqlmodel import Session

from ediarum_sdk.auth import (
    VERIFY_TIMEOUT, AuthConfig, Caller, admin_only, extract_key, key_caller, make_api_key_dependency,
    platform_user, require_project_access,
)
from ediarum_sdk.projects import ProjectInfo, ProjectsClient

from . import check, db, git_sync
from .config import Settings, removed_settings_in_env
from .locks import ProjectLocks

log = logging.getLogger("uvicorn.error")

EVENTS_WITH_CONTENT = {"created", "modified"}
EVENTS = EVENTS_WITH_CONTENT | {"moved", "deleted"}


@asynccontextmanager
async def lifespan(app: FastAPI):
    "Start-up: settings, database, registry and auth clients, locks, the check scheduler."
    settings = Settings.from_env()
    engine = db.make_engine(settings.database_url)
    db.init_db(engine)

    app.state.settings = settings
    app.state.engine = engine
    app.state.projects_client = httpx.Client()
    app.state.projects = ProjectsClient(app.state.projects_client)
    app.state.locks = ProjectLocks()  # the mount, per project: POST /files, the check's comparison, reload
    app.state.git_locks = ProjectLocks()  # git operations per project (a fetch can be slow)
    app.state.auth_config = AuthConfig.from_env()
    app.state.verify_client = httpx.Client(timeout=VERIFY_TIMEOUT)
    if removed := removed_settings_in_env():
        log.warning("ignored (no longer used, see CHECK_TIMES): %s", ", ".join(removed))
    scheduler = asyncio.create_task(check.run_scheduled(
        settings, app.state.projects, lambda: Session(engine), app.state.git_locks, app.state.locks,
    ))
    try:
        yield
    finally:
        # a check already running in its worker thread finishes first
        scheduler.cancel()
        with suppress(asyncio.CancelledError):
            await scheduler
        app.state.projects_client.close()
        app.state.verify_client.close()


app = FastAPI(lifespan=lifespan)


def get_session(request: Request):
    "This app's session dependency — handed to ediarum_sdk.auth.make_api_key_dependency, which doesn't own a DB itself."
    with Session(request.app.state.engine) as session:
        yield session


ApiKeyDep = Annotated[Caller, Depends(make_api_key_dependency(get_session))]


def key_or_admin(request: Request, session: Annotated[Session, Depends(get_session)]) -> Caller:
    """An API key if one is presented (e.g. ``ediarum.id``'s watcher polling
    the status), else a platform user with the role ``admin``."""
    if presented := extract_key(request):
        return key_caller(session, presented)
    return admin_only(platform_user(request))


KeyOrAdminDep = Annotated[Caller, Depends(key_or_admin)]


def admin(caller: KeyOrAdminDep) -> Caller:
    "A platform admin, or an admin API key (``create-key --admin``, e.g. for curl while debugging)."
    if not caller.is_admin:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "admins only")
    return caller


AdminDep = Annotated[Caller, Depends(admin)]


class FilesChangedResponse(BaseModel):
    "Response of ``POST /files``."

    project: str
    event: str
    path: str
    commit_sha: str
    verified: bool  # always False immediately after a fast-write -- see db.SyncState.verified


class CheckResponse(BaseModel):
    "The last backup check (db.GitCheck): the mount compared with the git host."
    checked_at: datetime
    result: str  # in_sync, git_behind, diverged, failed
    result_since: datetime
    remote_commit: str | None
    differences: list[dict]  # [{status, path}], at most db.MAX_DIFFERENCES
    difference_count: int
    error: str | None


class StatusResponse(BaseModel):
    "A project's claimed and verified commit and its last backup check."

    project: str
    claimed_commit: str | None
    claimed_at: datetime | None
    verified_commit: str | None
    verified_at: datetime | None
    verified: bool
    check: CheckResponse | None = None  # None before the first check


def _status(session: Session, slug: str) -> StatusResponse:
    "``StatusResponse`` from the project's ``SyncState`` and ``GitCheck`` rows."
    state = session.get(db.SyncState, slug)
    last = session.get(db.GitCheck, slug)
    return StatusResponse(
        project=slug,
        claimed_commit=state.claimed_commit if state else None,
        claimed_at=state.claimed_at if state else None,
        verified_commit=state.verified_commit if state else None,
        verified_at=state.verified_at if state else None,
        verified=state.verified if state else False,
        check=CheckResponse.model_validate(last, from_attributes=True) if last else None,
    )


def _registry(request: Request) -> dict[str, ProjectInfo]:
    "The platform's project registry; 503 if it can't be reached (and nothing is cached)."
    try:
        return request.app.state.projects.all()
    except Exception as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, f"platform registry unavailable: {exc}")


def _project(request: Request, slug: str) -> ProjectInfo:
    "The registry's project `slug`: 404 if unknown."
    info = _registry(request).get(slug)
    if info is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"unknown project {slug!r}")
    return info


@app.get("/health")
def health() -> dict:
    "Liveness check."
    return {"status": "ok"}


@app.post("/files")
async def files_changed(
    request: Request,
    caller: ApiKeyDep,
    event: Annotated[str, Form()],
    project: Annotated[str, Form()],
    commit: Annotated[str, Form()],
    path: Annotated[str, Form()],
    previous_path: Annotated[str | None, Form()] = None,
    file: Annotated[UploadFile | None, File()] = None,
) -> FilesChangedResponse:
    """Apply one file change `ediarum.webdav` claims, directly to the mount
    — no git on this request's critical path (see module docstring).
    `commit` is recorded as *claimed*, not yet verified; the next backup
    check (:mod:`ediarum_fileservice.check`) compares the mount with git
    and reports any difference.

    `project` is cross-checked against what `caller`'s key actually
    authorizes — never trusted on its own, since nothing else in the
    payload vouches for it.
    """
    if event not in EVENTS:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, f"unknown event {event!r}")
    require_project_access(caller, project)

    settings: Settings = request.app.state.settings
    projects: ProjectsClient = request.app.state.projects
    info = projects.get(project)
    if info is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"unknown project {project!r}")

    with request.app.state.locks.acquire(project):
        try:
            if event in EVENTS_WITH_CONTENT:
                if file is None:
                    raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, f"{event!r} requires file content")
                git_sync.write_file(settings.mount_root, info.slug, path, await file.read())
            elif event == "deleted":
                git_sync.delete_file(settings.mount_root, info.slug, path)
            else:  # moved
                if not previous_path:
                    raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "'moved' requires previous_path")
                git_sync.move_file(settings.mount_root, info.slug, previous_path, path)
                if file is not None:
                    git_sync.write_file(settings.mount_root, info.slug, path, await file.read())
        except git_sync.PathError as exc:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc))

        with Session(request.app.state.engine) as session:
            db.record_claim(session, project, commit)

    return FilesChangedResponse(project=project, event=event, path=path, commit_sha=commit, verified=False)


@app.get("/projects/{slug}/status")
def project_status(slug: str, request: Request, caller: KeyOrAdminDep) -> StatusResponse:
    """A project's claimed and verified commit and its last backup check.

    Platform admins (checked through the platform's ``verify``,
    ``AUTH_VERIFY_URL``), or an API key for the project — e.g.
    ``ediarum.id``'s watcher, which polls this to skip unchanged projects.
    """
    require_project_access(caller, slug)
    with Session(request.app.state.engine) as session:
        return _status(session, slug)


@app.get("/projects/status")
def projects_status(request: Request, caller: AdminDep) -> list[StatusResponse]:
    """Every project in the platform registry with its sync state and last
    check — the admins' overview."""
    with Session(request.app.state.engine) as session:
        return [_status(session, slug) for slug in sorted(_registry(request))]


@app.post("/projects/{slug}/check")
def check_now(slug: str, request: Request, caller: AdminDep) -> StatusResponse:
    """Check one project against the git host now, outside ``CHECK_TIMES`` —
    for debugging, or to confirm a fix (a resend from webdav's queue, a
    reload). Answers when the check is done; a git error is the ``failed``
    result, not an HTTP error."""
    info = _project(request, slug)
    state = request.app.state
    check.check_project(state.settings, info, lambda: Session(state.engine), state.git_locks, state.locks)
    with Session(state.engine) as session:
        return _status(session, slug)


@app.post("/projects/{slug}/reload")
def reload_project(
    slug: str, request: Request, caller: AdminDep,
    force: Annotated[bool, Query(description="reload even if the last check says git_behind")] = False,
) -> StatusResponse:
    """Reset one project's mount to git's version (``reset --hard`` +
    ``clean -fd``), then check it. The fallback for a ``diverged`` project
    that resending from webdav's queue doesn't fix. Refused (409) while the
    last check says ``git_behind`` — the reset would discard saves git
    doesn't have yet — unless ``force``."""
    info = _project(request, slug)
    state = request.app.state
    with Session(state.engine) as session:
        last = session.get(db.GitCheck, slug)
    if last is not None and last.result == "git_behind" and not force:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"{slug}: git is behind the mount (since {last.result_since}); a reload would discard those saves "
            "— wait for webdav's push, or pass force=true",
        )
    try:
        check.reload_project(state.settings, info, lambda: Session(state.engine), state.git_locks, state.locks)
    except git_sync.GitError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"{slug}: reload from git failed: {exc}")
    except TimeoutError as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(exc))
    with Session(state.engine) as session:
        return _status(session, slug)
