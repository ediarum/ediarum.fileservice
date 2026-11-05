"""Exercises git_sync's clone, fetch and pull against real local git repos (no network,
no mocking of git itself) — the sparse/shallow mechanics are exactly what
needs verifying, so a fake would defeat the point.
"""

import subprocess

import pytest

from ediarum_fileservice.git_sync import (
    GitError,
    PathError,
    checkout_path,
    delete_file,
    fetch_remote,
    head_commit,
    move_file,
    pull,
    safe_target,
    write_file,
)

SPARSE = "data/Register"
BRANCH = "main"


def run(*args: str, cwd) -> subprocess.CompletedProcess:
    result = subprocess.run(args, cwd=cwd, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    return result


def make_upstream(tmp_path, name="upstream") -> "Path":
    repo = tmp_path / name
    repo.mkdir()
    run("git", "init", "-q", "-b", BRANCH, cwd=repo)
    run("git", "config", "user.email", "test@example.org", cwd=repo)
    run("git", "config", "user.name", "Test", cwd=repo)
    (repo / SPARSE).mkdir(parents=True)
    (repo / SPARSE / "person.xml").write_text("<person/>")
    (repo / "unrelated").mkdir()
    (repo / "unrelated" / "archive.bin").write_text("not the register")
    run("git", "add", "-A", cwd=repo)
    run("git", "commit", "-q", "-m", "init", cwd=repo)
    return repo


def commit(repo, rel: str, content: str, message: str = "update") -> None:
    path = repo / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)
    run("git", "add", "-A", cwd=repo)
    run("git", "commit", "-q", "-m", message, cwd=repo)


# --- pull ---------------------------------------------------------------

def test_pull_clones_only_the_sparse_path(tmp_path):
    upstream = make_upstream(tmp_path)
    root = tmp_path / "mount"
    sha = pull(root, "mega", str(upstream), BRANCH, SPARSE)

    target = checkout_path(root, "mega")
    assert (target / SPARSE / "person.xml").read_text() == "<person/>"
    assert not (target / "unrelated").exists()
    assert sha == head_commit(target)


def test_pull_without_sparse_path_clones_everything(tmp_path):
    "The platform registry has no sparse_path: the whole repository."
    upstream = make_upstream(tmp_path)
    root = tmp_path / "mount"
    pull(root, "mega", str(upstream), BRANCH, "")

    target = checkout_path(root, "mega")
    assert (target / SPARSE / "person.xml").exists()
    assert (target / "unrelated" / "archive.bin").exists()


def test_pull_again_refreshes_to_new_upstream_commit(tmp_path):
    upstream = make_upstream(tmp_path)
    root = tmp_path / "mount"
    first_sha = pull(root, "mega", str(upstream), BRANCH, SPARSE)

    commit(upstream, f"{SPARSE}/person.xml", "<person>changed</person>")
    second_sha = pull(root, "mega", str(upstream), BRANCH, SPARSE)

    target = checkout_path(root, "mega")
    assert second_sha != first_sha
    assert (target / SPARSE / "person.xml").read_text() == "<person>changed</person>"


def test_pull_removes_files_git_does_not_have(tmp_path):
    upstream = make_upstream(tmp_path)
    root = tmp_path / "mount"
    pull(root, "mega", str(upstream), BRANCH, SPARSE)

    target = checkout_path(root, "mega")
    (target / SPARSE / "stray.xml").write_text("<stray/>")
    pull(root, "mega", str(upstream), BRANCH, SPARSE)
    assert not (target / SPARSE / "stray.xml").exists()


def test_pull_is_a_hard_reset_local_edits_are_discarded(tmp_path):
    upstream = make_upstream(tmp_path)
    root = tmp_path / "mount"
    pull(root, "mega", str(upstream), BRANCH, SPARSE)

    target = checkout_path(root, "mega")
    (target / SPARSE / "person.xml").write_text("<person>local stray edit</person>")

    pull(root, "mega", str(upstream), BRANCH, SPARSE)
    assert (target / SPARSE / "person.xml").read_text() == "<person/>"


def test_pull_unknown_url_raises_git_error(tmp_path):
    root = tmp_path / "mount"
    with pytest.raises(GitError):
        pull(root, "mega", str(tmp_path / "does-not-exist"), BRANCH, SPARSE)


# --- fetch_remote (moves origin/<branch>, never the working tree) ---------

def test_fetch_remote_returns_the_tip_without_touching_the_working_tree(tmp_path):
    upstream = make_upstream(tmp_path)
    root = tmp_path / "mount"
    first_sha = pull(root, "mega", str(upstream), BRANCH, SPARSE)
    target = checkout_path(root, "mega")
    (target / SPARSE / "person.xml").write_text("<person>local, unconfirmed</person>")
    commit(upstream, f"{SPARSE}/person.xml", "<person>changed</person>")

    remote = fetch_remote(root, "mega", BRANCH)
    assert remote.commit != first_sha
    assert remote.committed_at.tzinfo is not None
    assert head_commit(target) == first_sha
    assert (target / SPARSE / "person.xml").read_text() == "<person>local, unconfirmed</person>"


def test_fetch_remote_without_checkout_raises_git_error(tmp_path):
    (tmp_path / "mount" / "mega").mkdir(parents=True)
    with pytest.raises(GitError):
        fetch_remote(tmp_path / "mount", "mega", BRANCH)


# --- write_file / delete_file / move_file ----------------------------------

def test_write_file_creates_parents_and_writes_content(tmp_path):
    upstream = make_upstream(tmp_path)
    root = tmp_path / "mount"
    pull(root, "mega", str(upstream), BRANCH, SPARSE)

    target = write_file(root, "mega", f"{SPARSE}/sub/new.xml", b"<p/>")
    assert target.read_bytes() == b"<p/>"


def test_write_file_overwrites_existing(tmp_path):
    upstream = make_upstream(tmp_path)
    root = tmp_path / "mount"
    pull(root, "mega", str(upstream), BRANCH, SPARSE)

    write_file(root, "mega", f"{SPARSE}/person.xml", b"<person>fast-written</person>")
    assert (checkout_path(root, "mega") / SPARSE / "person.xml").read_bytes() == b"<person>fast-written</person>"


def test_delete_file_removes_it(tmp_path):
    upstream = make_upstream(tmp_path)
    root = tmp_path / "mount"
    pull(root, "mega", str(upstream), BRANCH, SPARSE)

    assert delete_file(root, "mega", f"{SPARSE}/person.xml") is True
    assert not (checkout_path(root, "mega") / SPARSE / "person.xml").exists()


def test_delete_file_missing_is_not_an_error(tmp_path):
    upstream = make_upstream(tmp_path)
    root = tmp_path / "mount"
    pull(root, "mega", str(upstream), BRANCH, SPARSE)

    assert delete_file(root, "mega", f"{SPARSE}/does-not-exist.xml") is False


def test_move_file_renames(tmp_path):
    upstream = make_upstream(tmp_path)
    root = tmp_path / "mount"
    pull(root, "mega", str(upstream), BRANCH, SPARSE)

    move_file(root, "mega", f"{SPARSE}/person.xml", f"{SPARSE}/renamed.xml")
    target = checkout_path(root, "mega")
    assert not (target / SPARSE / "person.xml").exists()
    assert (target / SPARSE / "renamed.xml").read_text() == "<person/>"


def test_move_file_missing_source_just_ensures_destination_dir(tmp_path):
    upstream = make_upstream(tmp_path)
    root = tmp_path / "mount"
    pull(root, "mega", str(upstream), BRANCH, SPARSE)

    dest = move_file(root, "mega", f"{SPARSE}/already-gone.xml", f"{SPARSE}/sub/new.xml")
    assert dest.parent.is_dir()
    assert not dest.exists()


def test_safe_target_rejects_traversal(tmp_path):
    root = tmp_path / "mount"
    (root / "mega").mkdir(parents=True)
    with pytest.raises(PathError):
        safe_target(root, "mega", "../../../etc/passwd")


def test_safe_target_rejects_absolute_path(tmp_path):
    root = tmp_path / "mount"
    (root / "mega").mkdir(parents=True)
    with pytest.raises(PathError):
        safe_target(root, "mega", "/etc/passwd")


def test_safe_target_accepts_normal_relative_path(tmp_path):
    root = tmp_path / "mount"
    (root / "mega").mkdir(parents=True)
    target = safe_target(root, "mega", "data/Register/person.xml")
    assert target == (root / "mega" / "data" / "Register" / "person.xml").resolve()


def test_write_file_rejects_traversal(tmp_path):
    upstream = make_upstream(tmp_path)
    root = tmp_path / "mount"
    pull(root, "mega", str(upstream), BRANCH, SPARSE)

    with pytest.raises(PathError):
        write_file(root, "mega", "../../outside.xml", b"evil")
