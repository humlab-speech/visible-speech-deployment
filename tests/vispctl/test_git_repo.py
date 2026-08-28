"""Tests for vispctl.git_repo — detached-HEAD detection and recovery."""

import subprocess

from vispctl.git_repo import GitRepository


def _git(args, cwd):
    return subprocess.run(["git"] + args, cwd=cwd, check=True, capture_output=True, text=True)


def _make_repo(path):
    """Create a git repo with two commits on branch 'main'; return (sha1, sha2)."""
    path.mkdir(parents=True)
    _git(["init", "-q", "-b", "main"], path)
    _git(["config", "user.email", "test@example.com"], path)
    _git(["config", "user.name", "Test"], path)
    (path / "file.txt").write_text("v1\n")
    _git(["add", "."], path)
    _git(["commit", "-q", "-m", "first"], path)
    sha1 = _git(["rev-parse", "HEAD"], path).stdout.strip()
    (path / "file.txt").write_text("v2\n")
    _git(["add", "."], path)
    _git(["commit", "-q", "-m", "second"], path)
    sha2 = _git(["rev-parse", "HEAD"], path).stdout.strip()
    return sha1, sha2


def test_is_detached_false_on_branch(tmp_path):
    repo_dir = tmp_path / "r"
    _make_repo(repo_dir)
    repo = GitRepository(str(repo_dir))
    assert repo.is_detached() is False


def test_is_detached_true_when_detached(tmp_path):
    repo_dir = tmp_path / "r"
    sha1, _ = _make_repo(repo_dir)
    _git(["checkout", "-q", sha1], repo_dir)
    repo = GitRepository(str(repo_dir))
    assert repo.is_detached() is True


def test_ensure_on_branch_returns_current_when_on_branch(tmp_path):
    repo_dir = tmp_path / "r"
    _make_repo(repo_dir)
    repo = GitRepository(str(repo_dir))
    branch = repo.ensure_on_branch()
    assert branch == "main"
    assert repo.is_detached() is False


def test_ensure_on_branch_recovers_detached_via_origin_head(tmp_path):
    """A detached repo with a local origin recovers onto the origin default branch."""
    origins = tmp_path / "origins"
    origins.mkdir()
    bare = origins / "origin.git"
    _git(["init", "-q", "--bare", "-b", "main", str(bare)], origins)

    repo_dir = tmp_path / "r"
    sha1, sha2 = _make_repo(repo_dir)
    _git(["remote", "add", "origin", str(bare)], repo_dir)
    _git(["push", "-q", "origin", "main"], repo_dir)

    # Detach at the first commit (simulates `deploy rollback`).
    _git(["checkout", "-q", sha1], repo_dir)
    repo = GitRepository(str(repo_dir))
    assert repo.is_detached() is True

    branch = repo.ensure_on_branch()

    assert branch == "main"
    assert repo.is_detached() is False
    assert repo.get_current_branch() == "main"
    # main still points at the second commit; recovery did not move it.
    assert _git(["rev-parse", "HEAD"], repo_dir).stdout.strip() == sha2


def test_ensure_on_branch_recovers_detached_via_main_fallback(tmp_path):
    """Without origin/HEAD, recovery falls back to a remote 'main' branch."""
    origins = tmp_path / "origins"
    origins.mkdir()
    bare = origins / "origin.git"
    _git(["init", "-q", "--bare", "-b", "main", str(bare)], origins)

    repo_dir = tmp_path / "r"
    sha1, _ = _make_repo(repo_dir)
    _git(["remote", "add", "origin", str(bare)], repo_dir)
    _git(["push", "-q", "origin", "main"], repo_dir)
    # Remove the local origin/HEAD ref so get_default_branch() finds nothing.
    _git(["remote", "set-head", "--delete", "origin"], repo_dir)

    _git(["checkout", "-q", sha1], repo_dir)
    repo = GitRepository(str(repo_dir))
    assert repo.is_detached() is True

    branch = repo.ensure_on_branch()

    assert branch == "main"
    assert repo.is_detached() is False


def test_ensure_on_branch_detached_no_remote_returns_none(tmp_path):
    """A detached repo with no remote cannot be recovered; stays detached."""
    repo_dir = tmp_path / "r"
    sha1, _ = _make_repo(repo_dir)
    _git(["checkout", "-q", sha1], repo_dir)
    repo = GitRepository(str(repo_dir))
    assert repo.is_detached() is True

    assert repo.ensure_on_branch() is None
    assert repo.is_detached() is True  # unchanged
