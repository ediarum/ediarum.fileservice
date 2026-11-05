"""Exercises the FastAPI app end to end against the real ediarum.webdav-
shaped contract: POST /files (created/modified/moved/deleted), per-project
Bearer-key auth, project cross-checking, the status route, and the admin
routes for the backup check (platform users, checked through the auth
contract's ``verify``, or admin keys).
"""

import subprocess
import time
from typing import Annotated

import httpx2 as httpx
import pytest
from fastapi import Depends
from fastapi.testclient import TestClient
from sqlmodel import Session

from ediarum_fileservice import app as app_module
from ediarum_fileservice import check
from ediarum_sdk.auth import create_key

PROJECTS_URL = "http://platform.test/projects"
VERIFY_URL = "http://platform.test/api/auth/verify"
SESSION_COOKIE = "ediarum_session"
SPARSE = "data/Register"


def run(*args: str, cwd) -> subprocess.CompletedProcess:
    result = subprocess.run(args, cwd=cwd, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    return result


@pytest.fixture
def upstream(tmp_path):
    repo = tmp_path / "upstream"
    repo.mkdir()
    run("git", "init", "-q", "-b", "main", cwd=repo)
    run("git", "config", "user.email", "test@example.org", cwd=repo)
    run("git", "config", "user.name", "Test", cwd=repo)
    (repo / SPARSE).mkdir(parents=True)
    (repo / SPARSE / "person.xml").write_text("<person/>")
    run("git", "add", "-A", cwd=repo)
    run("git", "commit", "-q", "-m", "init", cwd=repo)
    return repo


# session cookie value -> (X-User, X-User-Roles, X-User-Projects), as the platform's dev mock users
USERS = {
    "admin": ("admin@example.org", "admin", ""),
    "ada": ("ada@example.org", "", "mega"),
}


class Provider:
    "Answers GET {PROJECTS_URL} with the project list, and GET {VERIFY_URL} per the auth contract."

    def __init__(self, rows):
        self.rows = rows

    def __call__(self, request: httpx.Request) -> httpx.Response:
        if request.method == "GET" and str(request.url) == PROJECTS_URL:
            return httpx.Response(200, json=self.rows)
        if request.method == "GET" and str(request.url) == VERIFY_URL:
            cookie = request.headers.get("cookie", "")
            session = cookie.removeprefix(f"{SESSION_COOKIE}=") if cookie.startswith(f"{SESSION_COOKIE}=") else None
            if session not in USERS:
                return httpx.Response(401, json={"message": "Unauthenticated."})
            user, roles, projects = USERS[session]
            return httpx.Response(204, headers={"X-User": user, "X-User-Roles": roles, "X-User-Projects": projects})
        return httpx.Response(404)


@pytest.fixture
def provider(upstream):
    return Provider([{
        "slug": "mega",
        "label": "MEGA",
        "repo_url": str(upstream),
        "branch": "main",
        "sparse_path": SPARSE,
        "index_source_of_truth": "xml",
    }])


@pytest.fixture
def mount_root(tmp_path):
    return tmp_path / "mount"


async def no_background_task(*args):
    return None


@pytest.fixture
def client(monkeypatch, tmp_path, mount_root, provider):
    monkeypatch.setenv("XML_MOUNT_ROOT", str(mount_root))
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'fileservice.db'}")
    monkeypatch.setenv("PLATFORM_PROJECTS_URL", PROJECTS_URL)
    monkeypatch.setenv("AUTH_VERIFY_URL", VERIFY_URL)
    monkeypatch.setattr(check, "run_scheduled", no_background_task)  # no first fill racing the route tests
    monkeypatch.delenv("AUTH_DEV_USER", raising=False)
    real_client = httpx.Client
    monkeypatch.setattr(
        app_module.httpx, "Client", lambda *a, **kw: real_client(transport=httpx.MockTransport(provider))
    )
    with TestClient(app_module.app) as c:
        yield c


@pytest.fixture
def mega_key(client):
    "A real per-project key, created the same way ediarum.webdav's would be — via the app's own engine."
    with Session(app_module.app.state.engine) as session:
        return create_key(session, "webdav-mega", projects="mega")


def post_files(client, key, **fields):
    headers = {"Authorization": f"Bearer {key}"} if key else {}
    files = None
    if "file" in fields:
        files = {"file": ("content", fields.pop("file"))}
    return client.post("/files", data=fields, files=files, headers=headers)


def test_health_needs_no_key(client):
    r = client.get("/health")
    assert r.status_code == 200


def test_created_writes_content_and_responds(client, mega_key, mount_root):
    r = post_files(
        client, mega_key, event="created", project="mega", commit="deadbeef" * 5,
        path=f"{SPARSE}/new-person.xml", file=b"<person>new</person>",
    )
    assert r.status_code == 200
    body = r.json()
    assert body == {
        "project": "mega", "event": "created", "path": f"{SPARSE}/new-person.xml",
        "commit_sha": "deadbeef" * 5, "verified": False,
    }
    assert (mount_root / "mega" / SPARSE / "new-person.xml").read_text() == "<person>new</person>"


def test_modified_overwrites_content(client, mega_key, mount_root):
    post_files(client, mega_key, event="created", project="mega", commit="a" * 40, path=f"{SPARSE}/p.xml", file=b"<p>1</p>")
    post_files(client, mega_key, event="modified", project="mega", commit="b" * 40, path=f"{SPARSE}/p.xml", file=b"<p>2</p>")
    assert (mount_root / "mega" / SPARSE / "p.xml").read_text() == "<p>2</p>"


def test_deleted_removes_content(client, mega_key, mount_root):
    post_files(client, mega_key, event="created", project="mega", commit="a" * 40, path=f"{SPARSE}/p.xml", file=b"<p/>")
    r = post_files(client, mega_key, event="deleted", project="mega", commit="b" * 40, path=f"{SPARSE}/p.xml")
    assert r.status_code == 200
    assert not (mount_root / "mega" / SPARSE / "p.xml").exists()


def test_moved_renames(client, mega_key, mount_root):
    post_files(client, mega_key, event="created", project="mega", commit="a" * 40, path=f"{SPARSE}/old.xml", file=b"<p/>")
    r = post_files(
        client, mega_key, event="moved", project="mega", commit="b" * 40,
        path=f"{SPARSE}/new.xml", previous_path=f"{SPARSE}/old.xml",
    )
    assert r.status_code == 200
    base = mount_root / "mega" / SPARSE
    assert not (base / "old.xml").exists()
    assert (base / "new.xml").exists()


def test_moved_without_previous_path_is_422(client, mega_key):
    r = post_files(client, mega_key, event="moved", project="mega", commit="a" * 40, path=f"{SPARSE}/new.xml")
    assert r.status_code == 422


def test_created_without_file_is_422(client, mega_key):
    r = post_files(client, mega_key, event="created", project="mega", commit="a" * 40, path=f"{SPARSE}/p.xml")
    assert r.status_code == 422


def test_unknown_event_is_422(client, mega_key):
    r = post_files(client, mega_key, event="teleported", project="mega", commit="a" * 40, path="x.xml")
    assert r.status_code == 422


def test_path_traversal_is_rejected(client, mega_key):
    r = post_files(
        client, mega_key, event="created", project="mega", commit="a" * 40,
        path="../../../etc/passwd", file=b"evil",
    )
    assert r.status_code == 400


def test_missing_key_is_401(client):
    r = post_files(client, None, event="created", project="mega", commit="a" * 40, path="p.xml", file=b"<p/>")
    assert r.status_code == 401


def test_key_scoped_to_different_project_is_403(client, mount_root):
    with Session(app_module.app.state.engine) as session:
        other_key = create_key(session, "webdav-other", projects="other")
    r = post_files(client, other_key, event="created", project="mega", commit="a" * 40, path="p.xml", file=b"<p/>")
    assert r.status_code == 403


def test_unknown_project_is_404(client, mega_key):
    with Session(app_module.app.state.engine) as session:
        key = create_key(session, "webdav-ghost", projects="ghost")
    r = post_files(client, key, event="created", project="ghost", commit="a" * 40, path="p.xml", file=b"<p/>")
    assert r.status_code == 404


def test_app_led_project_is_written(client, provider, mount_root):
    # app-led concerns register's registers only; the texts are files
    provider.rows.append({**provider.rows[0], "slug": "applied", "index_source_of_truth": "app"})
    with Session(app_module.app.state.engine) as session:
        key = create_key(session, "webdav-applied", projects="applied")
    r = post_files(client, key, event="created", project="applied", commit="a" * 40, path="p.xml", file=b"<p/>")
    assert r.status_code == 200
    assert (mount_root / "applied" / "p.xml").read_bytes() == b"<p/>"


def get_status(client, slug, session=None, key=None):
    headers = {}
    if session:
        headers["Cookie"] = f"{SESSION_COOKIE}={session}"
    if key:
        headers["Authorization"] = f"Bearer {key}"
    return client.get(f"/projects/{slug}/status", headers=headers)


def test_status_before_any_sync_is_empty(client):
    r = get_status(client, "mega", session="admin")
    assert r.status_code == 200
    body = r.json()
    assert body["claimed_commit"] is None
    assert body["verified"] is False


def test_status_reflects_claim_not_yet_verified(client, mega_key):
    post_files(client, mega_key, event="created", project="mega", commit="c" * 40, path=f"{SPARSE}/p.xml", file=b"<p/>")
    body = get_status(client, "mega", session="admin").json()
    assert body["claimed_commit"] == "c" * 40
    assert body["verified_commit"] is None
    assert body["verified"] is False


def test_status_without_session_is_401(client):
    assert get_status(client, "mega").status_code == 401


def test_status_for_non_admin_member_is_403(client):
    assert get_status(client, "mega", session="ada").status_code == 403


def test_status_with_project_key_is_200(client, mega_key):
    "ediarum.id's watcher polls the status with a key, no platform session."
    assert get_status(client, "mega", key=mega_key).status_code == 200


def test_status_for_project_the_key_cant_see_is_403(client, mega_key):
    assert get_status(client, "other", key=mega_key).status_code == 403


def test_status_with_unknown_key_is_401(client):
    assert get_status(client, "mega", key="not-a-key").status_code == 401


def test_app_starts_with_the_first_fill(monkeypatch, tmp_path, mount_root, provider, caplog):
    "At startup every project without a checkout is cloned; removed settings are only warned about."
    monkeypatch.setenv("RECONCILE_INTERVAL_SECONDS", "300")
    monkeypatch.setenv("XML_MOUNT_ROOT", str(mount_root))
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'fileservice.db'}")
    monkeypatch.setenv("PLATFORM_PROJECTS_URL", PROJECTS_URL)
    monkeypatch.setenv("AUTH_VERIFY_URL", VERIFY_URL)
    real_client = httpx.Client
    monkeypatch.setattr(
        app_module.httpx, "Client", lambda *a, **kw: real_client(transport=httpx.MockTransport(provider))
    )
    with TestClient(app_module.app):
        for _ in range(100):
            if (mount_root / "mega" / SPARSE / "person.xml").exists():
                break
            time.sleep(0.05)
    assert (mount_root / "mega" / SPARSE / "person.xml").read_text() == "<person/>"
    assert "RECONCILE_INTERVAL_SECONDS" in caplog.text


# --- the backup check's admin routes -----------------------------------------

def admin_post(client, url, session="admin", key=None):
    headers = {"Cookie": f"{SESSION_COOKIE}={session}"} if session else {}
    if key:
        headers = {"Authorization": f"Bearer {key}"}
    return client.post(url, headers=headers)


def test_check_now_fills_checks_and_shows_in_status(client, upstream):
    r = admin_post(client, "/projects/mega/check")
    assert r.status_code == 200
    assert r.json()["check"]["result"] == "in_sync"
    assert get_status(client, "mega", session="admin").json()["check"]["result"] == "in_sync"


def test_check_now_reports_a_divergence(client, mega_key, mount_root):
    admin_post(client, "/projects/mega/check")
    (mount_root / "mega" / SPARSE / "person.xml").write_text("<person>manual</person>")
    body = admin_post(client, "/projects/mega/check").json()
    assert body["check"]["result"] == "diverged"
    assert body["check"]["differences"] == [{"status": "M", "path": f"{SPARSE}/person.xml"}]


def test_check_now_git_error_is_a_failed_result_not_an_http_error(client, provider, tmp_path):
    provider.rows[0]["repo_url"] = str(tmp_path / "missing")
    r = admin_post(client, "/projects/mega/check")
    assert r.status_code == 200
    assert r.json()["check"]["result"] == "failed"


def test_check_now_accepts_an_admin_key_but_not_a_project_key(client, mega_key):
    with Session(app_module.app.state.engine) as session:
        admin_key = create_key(session, "debug", projects="*", is_admin=True)  # create-key --admin
        all_projects_key = create_key(session, "watcher", projects="*")
    assert admin_post(client, "/projects/mega/check", session=None, key=admin_key).status_code == 200
    assert admin_post(client, "/projects/mega/check", session=None, key=all_projects_key).status_code == 403
    assert admin_post(client, "/projects/mega/check", session=None, key=mega_key).status_code == 403
    assert admin_post(client, "/projects/mega/check", session="ada").status_code == 403
    assert admin_post(client, "/projects/mega/check", session=None).status_code == 401


def test_check_now_unknown_404_app_led_checked(client, provider):
    provider.rows.append({**provider.rows[0], "slug": "applied", "index_source_of_truth": "app"})
    assert admin_post(client, "/projects/ghost/check").status_code == 404
    assert admin_post(client, "/projects/applied/check").status_code == 200


def test_overview_lists_every_registry_project(client, provider):
    provider.rows.append({**provider.rows[0], "slug": "applied", "index_source_of_truth": "app"})
    admin_post(client, "/projects/mega/check")
    r = client.get("/projects/status", headers={"Cookie": f"{SESSION_COOKIE}=admin"})
    assert r.status_code == 200
    rows = {row["project"]: row for row in r.json()}
    assert rows.keys() == {"applied", "mega"}
    assert rows["mega"]["check"]["result"] == "in_sync"
    assert rows["applied"]["check"] is None


def test_overview_is_admins_only(client):
    assert client.get("/projects/status", headers={"Cookie": f"{SESSION_COOKIE}=ada"}).status_code == 403


def test_reload_resets_the_mount_to_git(client, mount_root):
    admin_post(client, "/projects/mega/check")
    (mount_root / "mega" / SPARSE / "person.xml").write_text("<person>manual</person>")
    (mount_root / "mega" / SPARSE / "extra.xml").write_text("<extra/>")
    r = admin_post(client, "/projects/mega/reload")
    assert r.status_code == 200
    assert r.json()["check"]["result"] == "in_sync"
    assert (mount_root / "mega" / SPARSE / "person.xml").read_text() == "<person/>"
    assert not (mount_root / "mega" / SPARSE / "extra.xml").exists()


def test_reload_refused_while_git_is_behind_unless_forced(client, mega_key, mount_root):
    admin_post(client, "/projects/mega/check")
    time.sleep(1.1)  # the post must be newer than git's commit (commit times have whole seconds)
    post_files(client, mega_key, event="modified", project="mega", commit="d" * 40, path=f"{SPARSE}/person.xml",
               file=b"<person>saved, not pushed yet</person>")
    assert admin_post(client, "/projects/mega/check").json()["check"]["result"] == "git_behind"
    assert admin_post(client, "/projects/mega/reload").status_code == 409
    assert (mount_root / "mega" / SPARSE / "person.xml").read_text() == "<person>saved, not pushed yet</person>"
    assert admin_post(client, "/projects/mega/reload?force=true").status_code == 200
    assert (mount_root / "mega" / SPARSE / "person.xml").read_text() == "<person/>"


def test_reload_is_admins_only(client, mega_key):
    assert admin_post(client, "/projects/mega/reload", session=None, key=mega_key).status_code == 403


def test_admin_routes_answer_503_when_the_registry_is_down(client, provider, monkeypatch):
    def down():
        raise ConnectionError("registry down")

    monkeypatch.setattr(app_module.app.state.projects, "all", down)
    admin = {"Cookie": f"{SESSION_COOKIE}=admin"}
    assert client.get("/projects/status", headers=admin).status_code == 503
    assert admin_post(client, "/projects/mega/check").status_code == 503
    assert admin_post(client, "/projects/mega/reload").status_code == 503
