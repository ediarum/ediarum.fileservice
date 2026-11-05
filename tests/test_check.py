"""Exercises the backup check (docs/concepts/webdav-led-sync.md) against real
local git repos: compare the mount with git, report, never correct — git
writes the mount only on the first fill and an admin reload.
"""

import asyncio
import dataclasses
import subprocess
from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from ediarum_fileservice import check, db, git_sync
from ediarum_fileservice.check import (
    check_all, check_project, first_fill, first_fill_all, next_check_at, reload_project, run_scheduled,
)
from ediarum_fileservice.config import ConfigError, Settings, parse_check_times
from ediarum_fileservice.locks import ProjectLocks
from ediarum_sdk.projects import ProjectInfo

SPARSE = "data/Register"
BRANCH = "main"


def run(*args: str, cwd) -> subprocess.CompletedProcess:
    result = subprocess.run(args, cwd=cwd, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    return result


def commit(repo, rel: str, content: str, message: str = "update") -> None:
    path = repo / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)
    run("git", "add", "-A", cwd=repo)
    run("git", "commit", "-q", "-m", message, cwd=repo)


def remote_commit(repo) -> str:
    return run("git", "rev-parse", "HEAD", cwd=repo).stdout.strip()


@pytest.fixture
def upstream(tmp_path):
    repo = tmp_path / "upstream"
    repo.mkdir()
    run("git", "init", "-q", "-b", BRANCH, cwd=repo)
    run("git", "config", "user.email", "test@example.org", cwd=repo)
    run("git", "config", "user.name", "Test", cwd=repo)
    (repo / "unrelated").mkdir()
    (repo / "unrelated" / "archive.bin").write_text("not the register")
    commit(repo, f"{SPARSE}/person.xml", "<person/>", "init")
    return repo


@pytest.fixture
def info(upstream):
    return ProjectInfo(
        slug="mega", label="MEGA", repo_url=str(upstream), branch=BRANCH, sparse_path=SPARSE, index_source_of_truth="xml",
    )


@pytest.fixture
def engine():
    e = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    SQLModel.metadata.create_all(e)
    return e


@pytest.fixture
def session_factory(engine):
    return lambda: Session(engine)


@pytest.fixture
def settings(tmp_path):
    return Settings(mount_root=tmp_path / "mount", database_url="sqlite://")


@pytest.fixture
def locks():
    return ProjectLocks(), ProjectLocks()  # git_locks, locks


@pytest.fixture
def filled(settings, info, session_factory, locks):
    "A project after its first fill."
    assert first_fill(settings, info, session_factory, *locks)
    return settings.mount_root / "mega"


class StubProjects:
    def __init__(self, *infos, fail_first: int = 0):
        self.infos = {i.slug: i for i in infos}
        self.fail_first = fail_first
        self.calls = 0

    def all(self):
        self.calls += 1
        if self.calls <= self.fail_first:
            raise ConnectionError("registry down")
        return self.infos


def claim(session_factory, commit_sha: str, at: datetime) -> None:
    "What POST /files records."
    with session_factory() as session:
        state = session.get(db.SyncState, "mega") or db.SyncState(project="mega")
        state.claimed_commit, state.claimed_at = commit_sha, at
        session.add(state)
        session.commit()


def status(session_factory) -> db.SyncState:
    with session_factory() as session:
        return session.get(db.SyncState, "mega")


def snapshot(checkout):
    "HEAD, the real index and every file's bytes — what a check must leave alone."
    files = {p.relative_to(checkout).as_posix(): p.read_bytes()
             for p in checkout.rglob("*") if p.is_file() and ".git" not in p.parts}
    head = run("git", "rev-parse", "HEAD", cwd=checkout).stdout
    index = (checkout / ".git" / "index").read_bytes()
    return head, index, files


# --- first fill -------------------------------------------------------------

def test_first_fill_clones_and_records_verified(settings, info, session_factory, locks, upstream, filled):
    assert (filled / SPARSE / "person.xml").read_text() == "<person/>"
    assert not (filled / "unrelated").exists()  # sparse
    assert status(session_factory).verified_commit == remote_commit(upstream)


def test_first_fill_leaves_an_existing_checkout_alone(settings, info, session_factory, locks, upstream, filled):
    (filled / SPARSE / "person.xml").write_text("<person>webdav</person>")
    commit(upstream, f"{SPARSE}/person.xml", "<person>git</person>")
    assert not first_fill(settings, info, session_factory, *locks)
    assert (filled / SPARSE / "person.xml").read_text() == "<person>webdav</person>"


def test_first_fill_keeps_files_posted_before(settings, info, session_factory, locks):
    "POST /files may write before the first fill (git host down): webdav's files win over git's."
    git_sync.write_file(settings.mount_root, "mega", f"{SPARSE}/person.xml", b"<person>posted</person>")
    git_sync.write_file(settings.mount_root, "mega", f"{SPARSE}/extra.xml", b"<extra/>")
    assert first_fill(settings, info, session_factory, *locks)
    base = settings.mount_root / "mega" / SPARSE
    assert (base / "person.xml").read_text() == "<person>posted</person>"
    assert (base / "extra.xml").read_text() == "<extra/>"
    assert not any(p.name.startswith(".mega-posted-") for p in settings.mount_root.iterdir())


def test_failed_first_fill_keeps_posted_files_and_leaves_no_git(settings, session_factory, locks, tmp_path):
    broken = ProjectInfo(slug="mega", label="MEGA", repo_url=str(tmp_path / "missing"), branch=BRANCH,
                         sparse_path=SPARSE, index_source_of_truth="xml")
    git_sync.write_file(settings.mount_root, "mega", f"{SPARSE}/person.xml", b"<person>posted</person>")
    with pytest.raises(git_sync.GitError):
        first_fill(settings, broken, session_factory, *locks)
    assert (settings.mount_root / "mega" / SPARSE / "person.xml").read_text() == "<person>posted</person>"
    assert not (settings.mount_root / "mega" / ".git").exists()


def test_first_fill_all_goes_on_after_a_failing_project(settings, info, session_factory, locks, tmp_path):
    broken = ProjectInfo(slug="broken", label="Broken", repo_url=str(tmp_path / "missing"), branch=BRANCH,
                         index_source_of_truth="xml")
    app_led = dataclasses.replace(info, slug="other", index_source_of_truth="app")  # its texts are files too
    first_fill_all(settings, StubProjects(broken, info, app_led), session_factory, *locks)
    assert (settings.mount_root / "mega" / ".git").exists()
    assert (settings.mount_root / "other" / ".git").exists()


# --- the check --------------------------------------------------------------

def test_in_sync(settings, info, session_factory, locks, filled, upstream):
    row = check_project(settings, info, session_factory, *locks)
    assert (row.result, row.difference_count, row.remote_commit) == ("in_sync", 0, remote_commit(upstream))


def test_in_sync_after_webdav_post_and_push_confirms_the_claim(settings, info, session_factory, locks, filled,
                                                               upstream):
    git_sync.write_file(settings.mount_root, "mega", f"{SPARSE}/new.xml", b"<new/>")
    commit(upstream, f"{SPARSE}/new.xml", "<new/>")  # webdav's push arrived
    claim(session_factory, remote_commit(upstream), datetime.now(timezone.utc))
    assert check_project(settings, info, session_factory, *locks).result == "in_sync"
    assert status(session_factory).verified


def test_git_behind_when_the_push_has_not_arrived(settings, info, session_factory, locks, filled):
    git_sync.write_file(settings.mount_root, "mega", f"{SPARSE}/person.xml", b"<person>saved</person>")
    claim(session_factory, "f" * 40, datetime.now(timezone.utc) + timedelta(seconds=1))
    row = check_project(settings, info, session_factory, *locks)
    assert row.result == "git_behind"
    assert row.differences == [{"status": "M", "path": f"{SPARSE}/person.xml"}]
    assert not status(session_factory).verified


@pytest.mark.parametrize("change, expected", [
    ("modify", {"status": "M", "path": f"{SPARSE}/person.xml"}),
    ("add", {"status": "A", "path": f"{SPARSE}/extra.xml"}),
    ("delete", {"status": "D", "path": f"{SPARSE}/person.xml"}),
])
def test_diverged_reports_each_kind_of_difference(settings, info, session_factory, locks, filled, change, expected):
    "No claim newer than git's commit: a difference is a divergence, listed file by file."
    base = filled / SPARSE
    if change == "modify":
        (base / "person.xml").write_text("<person>manual</person>")
    elif change == "add":
        (base / "extra.xml").write_text("<extra/>")
    else:
        (base / "person.xml").unlink()
    row = check_project(settings, info, session_factory, *locks)
    assert (row.result, row.differences, row.difference_count) == ("diverged", [expected], 1)


def test_diverged_when_git_has_a_save_the_mount_missed(settings, info, session_factory, locks, filled, upstream):
    "webdav pushed, but its POST /files never arrived (failed queue)."
    claim(session_factory, remote_commit(upstream), datetime.now(timezone.utc) - timedelta(hours=1))
    commit(upstream, f"{SPARSE}/person.xml", "<person>pushed, never posted</person>")
    row = check_project(settings, info, session_factory, *locks)
    assert row.result == "diverged"
    assert row.differences == [{"status": "M", "path": f"{SPARSE}/person.xml"}]


def test_diverged_when_the_claimed_commit_is_on_git_but_files_differ(settings, info, session_factory, locks,
                                                                     filled, upstream):
    claim(session_factory, remote_commit(upstream), datetime.now(timezone.utc) + timedelta(seconds=1))
    (filled / SPARSE / "person.xml").write_text("<person>manual</person>")
    assert check_project(settings, info, session_factory, *locks).result == "diverged"


def test_check_never_changes_the_checkout(settings, info, session_factory, locks, filled, upstream):
    (filled / SPARSE / "person.xml").write_text("<person>local</person>")
    (filled / SPARSE / "extra.xml").write_text("<extra/>")
    commit(upstream, f"{SPARSE}/person.xml", "<person>git</person>")
    before = snapshot(filled)
    check_project(settings, info, session_factory, *locks)
    assert snapshot(filled) == before


def test_check_compares_only_the_sparse_path(settings, info, session_factory, locks, filled, upstream):
    commit(upstream, "unrelated/archive.bin", "changed outside the register")
    assert check_project(settings, info, session_factory, *locks).result == "in_sync"


def test_check_without_sparse_path_compares_everything(settings, session_factory, locks, upstream):
    whole = ProjectInfo(slug="mega", label="MEGA", repo_url=str(upstream), branch=BRANCH, index_source_of_truth="xml")
    first_fill(settings, whole, session_factory, *locks)
    (settings.mount_root / "mega" / "unrelated" / "archive.bin").write_text("local")
    row = check_project(settings, whole, session_factory, *locks)
    assert row.differences == [{"status": "M", "path": "unrelated/archive.bin"}]


def test_check_fills_a_project_without_checkout_first(settings, info, session_factory, locks):
    assert check_project(settings, info, session_factory, *locks).result == "in_sync"
    assert (settings.mount_root / "mega" / SPARSE / "person.xml").exists()


def test_failed_when_git_is_unreachable(settings, session_factory, locks, tmp_path):
    broken = ProjectInfo(slug="broken", label="Broken", repo_url=str(tmp_path / "missing"), branch=BRANCH,
                         index_source_of_truth="xml")
    row = check_project(settings, broken, session_factory, *locks)
    assert row.result == "failed"
    assert row.error


def test_result_since_persists_while_the_result_stays(settings, info, session_factory, locks, filled):
    (filled / SPARSE / "person.xml").write_text("<person>manual</person>")
    first = check_project(settings, info, session_factory, *locks)
    second = check_project(settings, info, session_factory, *locks)
    assert second.result_since == first.result_since
    assert second.checked_at > first.checked_at
    (filled / SPARSE / "person.xml").write_text("<person/>")
    assert check_project(settings, info, session_factory, *locks).result_since > first.result_since


def test_differences_are_capped(settings, info, session_factory, locks, filled):
    for i in range(db.MAX_DIFFERENCES + 5):
        (filled / SPARSE / f"extra-{i:03}.xml").write_text("<extra/>")
    row = check_project(settings, info, session_factory, *locks)
    assert (len(row.differences), row.difference_count) == (db.MAX_DIFFERENCES, db.MAX_DIFFERENCES + 5)


def test_check_all_includes_app_led_and_goes_on_after_a_failure(settings, info, session_factory, locks, tmp_path):
    broken = ProjectInfo(slug="broken", label="Broken", repo_url=str(tmp_path / "missing"), branch=BRANCH,
                         index_source_of_truth="xml")
    app_led = dataclasses.replace(info, slug="other", index_source_of_truth="app")
    rows = check_all(settings, StubProjects(broken, info, app_led), session_factory, *locks)
    assert [(r.project, r.result) for r in rows] == [("broken", "failed"), ("mega", "in_sync"), ("other", "in_sync")]


# --- reload -----------------------------------------------------------------

def test_reload_resets_to_git_and_checks(settings, info, session_factory, locks, filled, upstream):
    (filled / SPARSE / "person.xml").write_text("<person>manual</person>")
    (filled / SPARSE / "extra.xml").write_text("<extra/>")
    commit(upstream, f"{SPARSE}/person.xml", "<person>git</person>")
    row = reload_project(settings, info, session_factory, *locks)
    assert row.result == "in_sync"
    assert (filled / SPARSE / "person.xml").read_text() == "<person>git</person>"
    assert not (filled / SPARSE / "extra.xml").exists()
    assert status(session_factory).verified_commit == remote_commit(upstream)


# --- schedule ---------------------------------------------------------------

BERLIN = ZoneInfo("Europe/Berlin")


def at(*args) -> datetime:
    return datetime(*args, tzinfo=BERLIN)


@pytest.mark.parametrize("now, expected", [
    (at(2026, 10, 16, 1, 0), at(2026, 10, 16, 3, 0)),
    (at(2026, 10, 16, 3, 0), at(2026, 10, 16, 15, 0)),  # exactly at a check time: the next one
    (at(2026, 10, 16, 16, 0), at(2026, 10, 17, 3, 0)),  # across midnight
])
def test_next_check_at(tmp_path, now, expected):
    settings = Settings(mount_root=tmp_path, database_url="sqlite://",
                        check_times=(time(3, 0), time(15, 0)), check_timezone=BERLIN)
    assert next_check_at(now.astimezone(timezone.utc), settings) == expected


def test_no_check_times_no_next_check(tmp_path):
    assert next_check_at(datetime.now(timezone.utc), Settings(mount_root=tmp_path, database_url="")) is None


def test_parse_check_times():
    assert parse_check_times(" 15:00, 03:00,15:00,") == (time(3, 0), time(15, 0))
    assert parse_check_times("") == ()
    with pytest.raises(ConfigError):
        parse_check_times("3pm")


def test_run_scheduled_fills_then_checks_and_survives_a_failed_round(settings, info, session_factory, locks,
                                                                     monkeypatch):
    soon = iter([0.05, 0.05, None])  # two rounds, then no further check time

    def fake_next(now, _settings):
        delay = next(soon)
        return None if delay is None else now + timedelta(seconds=delay)

    monkeypatch.setattr(check, "next_check_at", fake_next)
    projects = StubProjects(info, fail_first=2)  # the first fill and the first round find the registry down
    asyncio.run(asyncio.wait_for(run_scheduled(settings, projects, session_factory, *locks), timeout=10))
    assert projects.calls == 3
    with session_factory() as session:
        assert session.get(db.GitCheck, "mega").result == "in_sync"  # round 2 filled and checked
