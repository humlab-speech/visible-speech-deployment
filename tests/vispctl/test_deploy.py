"""Tests for vispctl.deploy — deploy rollback checkout behavior."""

import json
import subprocess
from pathlib import Path

from vispctl.deploy import DeployManager


def _git(args, cwd):
    return subprocess.run(["git"] + args, cwd=cwd, check=True, capture_output=True, text=True)


def _make_repo(path: Path) -> tuple[str, str]:
    """Create a git repo with two commits; return (sha1, sha2)."""
    path.mkdir(parents=True)
    _git(["init", "-q"], path)
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


def _write_versions(project: Path, component: str, version: str, locked: str) -> None:
    versions = {
        "_comment": "test",
        "components": {component: {"url": None, "version": version, "locked_version": locked}},
    }
    (project / "versions.json").write_text(json.dumps(versions, indent=2))


def _head(repo_dir: Path) -> str:
    return _git(["rev-parse", "HEAD"], repo_dir).stdout.strip()


def test_rollback_checks_out_locked_version(tmp_path):
    """rollback checks the repo out to the locked SHA and updates versions.json."""
    project = tmp_path / "proj"
    project.mkdir()
    repo_dir = project / "external" / "webclient"
    sha1, sha2 = _make_repo(repo_dir)

    # Locked to sha1, but the working tree has moved on to sha2.
    _write_versions(project, "webclient", version=sha2, locked=sha1)

    dm = DeployManager(str(project))
    ok = dm.rollback_components(["webclient"])

    assert ok is True
    assert _head(repo_dir) == sha1  # repo is back at the locked SHA
    assert dm.config.get_version("webclient") == sha1  # versions.json updated


def test_rollback_noop_when_already_at_locked(tmp_path):
    """rollback succeeds (no-op) when the repo is already at the locked SHA."""
    project = tmp_path / "proj"
    project.mkdir()
    repo_dir = project / "external" / "webclient"
    _, sha2 = _make_repo(repo_dir)  # HEAD is at sha2
    _write_versions(project, "webclient", version=sha2, locked=sha2)  # locked == HEAD

    dm = DeployManager(str(project))
    ok = dm.rollback_components(["webclient"])

    assert ok is True
    assert _head(repo_dir) == sha2  # unchanged (no-op)


def test_rollback_dirty_repo_skips_checkout(tmp_path):
    """rollback skips the checkout (and versions.json) when the repo is dirty."""
    project = tmp_path / "proj"
    project.mkdir()
    repo_dir = project / "external" / "webclient"
    sha1, sha2 = _make_repo(repo_dir)
    _write_versions(project, "webclient", version=sha2, locked=sha1)

    # Make the working tree dirty.
    (repo_dir / "file.txt").write_text("v2\nlocal edit\n")

    dm = DeployManager(str(project))
    ok = dm.rollback_components(["webclient"])

    assert ok is False  # nothing rolled back
    assert _head(repo_dir) == sha2  # repo untouched
    assert dm.config.get_version("webclient") == sha2  # versions.json untouched


def test_rollback_missing_locked_version_skips(tmp_path):
    """rollback skips a component that has no locked version."""
    project = tmp_path / "proj"
    project.mkdir()
    repo_dir = project / "external" / "webclient"
    _, sha2 = _make_repo(repo_dir)
    _write_versions(project, "webclient", version="latest", locked=None)

    dm = DeployManager(str(project))
    ok = dm.rollback_components(["webclient"])

    assert ok is False
    assert _head(repo_dir) == sha2  # repo untouched
