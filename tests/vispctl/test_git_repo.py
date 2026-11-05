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


# ── round-2 GR-R2-03: pull must be config-immune and reason-visible ─────────


def test_pull_is_ff_only_and_config_immune(monkeypatch):
    import subprocess

    from vispctl.git_repo import GitRepository

    seen = []

    def fake_run(cmd, **kwargs):
        seen.append(list(cmd))
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    repo = GitRepository("/nonexistent", "http://example.invalid/x.git")
    repo.pull()
    argv = seen[-1]
    assert argv[:1] == ["git"]
    assert ["-c", "pull.rebase=false", "pull", "--ff-only"] == argv[1:5], argv


def test_deploy_update_surfaces_git_stderr(monkeypatch, capsys, tmp_path):
    import subprocess

    import vispctl.deploy as deploy_mod

    class FakeRepo:
        url = "u"

        def __init__(self, path, url=None):
            pass

        def exists(self):
            return True

        def fetch(self, quiet=False):
            pass

        def is_dirty(self):
            return False

        def is_detached(self):
            return False

        def ensure_on_branch(self):
            return "main"

        def pull(self):
            raise subprocess.CalledProcessError(
                128, ["git", "pull"], stderr=b"fatal: Not possible to fast-forward, aborting."
            )

    class Cfg:
        def get_components(self):
            return [("demo", {"url": "u", "version": "latest"})]

        def is_locked(self, name):
            return False

    dm = deploy_mod.DeployManager.__new__(deploy_mod.DeployManager)
    dm.config = Cfg()
    dm.external_dir = tmp_path
    dm.runner = None
    monkeypatch.setattr(deploy_mod, "GitRepository", FakeRepo)

    dm.update_components()
    out = capsys.readouterr().out
    assert "Not possible to fast-forward" in out
