"""Tests for ``./visp.py release create`` (vispctl.release_create)."""

import json
import subprocess
from pathlib import Path

import pytest

from vispctl.exceptions import ConfigError
from vispctl.release_create import create_release, release_tags
from vispctl.versions import DEFAULT_VERSIONS_CONFIG

MONGO = "docker.io/library/mongo:6.0.27"


def _git(cwd, *args):
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


def _init(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    _git(path, "init", "-q", "-b", "main")
    _git(path, "config", "user.email", "t@example.com")
    _git(path, "config", "user.name", "T")
    _git(path, "config", "commit.gpgsign", "false")
    _git(path, "config", "tag.gpgsign", "false")


def _commit(path: Path, name: str, text: str, msg: str) -> str:
    (path / name).write_text(text)
    _git(path, "add", ".")
    _git(path, "commit", "-q", "-m", msg)
    return _git(path, "rev-parse", "HEAD")


@pytest.fixture
def project(tmp_path):
    """A deployment repo with a 1.0.0 release.json and every component cloned from a local origin."""
    proj = tmp_path / "proj"
    _init(proj)
    for mode in ("dev", "prod"):
        (proj / "quadlets" / mode).mkdir(parents=True)
        (proj / "quadlets" / mode / "mongo.container").write_text(f"[Container]\nImage={MONGO}\n")
    (proj / "docker" / "tratt").mkdir(parents=True)
    (proj / "docker" / "tratt" / "Dockerfile").write_text(
        f"RUN git clone https://github.com/example/TRATT.git . && git checkout {'a' * 40}\n"
    )
    (proj / ".gitignore").write_text("external/\n")

    shas = {}
    for name in DEFAULT_VERSIONS_CONFIG:
        seed = tmp_path / "seeds" / name
        _init(seed)
        _commit(seed, "f", "1", "first")
        bare = tmp_path / "origins" / f"{name}.git"
        subprocess.run(["git", "clone", "-q", "--bare", str(seed), str(bare)], check=True)
        work = proj / "external" / name
        subprocess.run(["git", "clone", "-q", str(bare), str(work)], check=True)
        _git(work, "config", "user.email", "t@example.com")
        _git(work, "config", "user.name", "T")
        shas[name] = _git(work, "rev-parse", "HEAD")

    manifest = {
        "visp": "1.0.0",
        "components": {n: {"ref": s} for n, s in shas.items()},
        "images": {"mongo": MONGO},
        "vendored": {
            "tratt": {
                "repo": "https://github.com/example/TRATT.git",
                "ref": "a" * 40,
                "pinned_in": "docker/tratt/Dockerfile",
            }
        },
    }
    (proj / "release.json").write_text(json.dumps(manifest, indent=2) + "\n")
    _git(proj, "add", ".")
    _git(proj, "commit", "-q", "-m", "init")
    _git(proj, "tag", "-a", "v1.0.0", "-m", "VISP 1.0.0")
    return proj


def _manifest(proj):
    return json.loads((proj / "release.json").read_text())


def _push_new_commit(proj, name, branch="main"):
    work = proj / "external" / name
    if branch != "main":
        _git(work, "checkout", "-q", "-b", branch)
    sha = _commit(work, "f", "2", "second")
    _git(work, "push", "-q", "origin", branch)
    return sha


def test_creates_commit_and_tag_from_checkouts(project, capsys):
    sha = _push_new_commit(project, "session-manager", branch="fix/thing")
    for mode in ("dev", "prod"):
        (project / "quadlets" / mode / "mongo.container").write_text(
            "[Container]\nImage=docker.io/library/mongo:7.0.1\n"
        )
    _git(project, "commit", "-qam", "bump mongo")

    assert create_release(project, "1.1.0", assume_yes=True, fetch=False) == 0

    m = _manifest(project)
    assert m["visp"] == "1.1.0"
    assert m["components"]["session-manager"]["ref"] == sha
    assert m["images"]["mongo"] == "docker.io/library/mongo:7.0.1"
    assert release_tags(project) == ["1.0.0", "1.1.0"]
    assert _git(project, "rev-parse", "v1.1.0^{commit}") == _git(project, "rev-parse", "HEAD")
    assert _git(project, "log", "-1", "--format=%s") == "chore(release): VISP 1.1.0"
    assert _git(project, "status", "--porcelain") == ""
    out = capsys.readouterr().out
    assert "[fix/thing]" in out and "git push origin main v1.1.0" in out


def test_default_version_is_next_patch(project):
    assert create_release(project, assume_yes=True, fetch=False) == 0
    assert release_tags(project)[-1] == "1.0.1"


def test_untagged_manifest_version_is_suggested_first(project):
    _git(project, "tag", "-d", "v1.0.0")
    assert create_release(project, assume_yes=True, fetch=False) == 0
    assert release_tags(project) == ["1.0.0"]


def test_refuses_dirty_component_and_changes_nothing(project, capsys):
    (project / "external" / "webclient" / "f").write_text("local edit")
    before = (project / "release.json").read_text()
    assert create_release(project, "1.1.0", assume_yes=True, fetch=False) == 1
    assert "webclient: has uncommitted changes" in capsys.readouterr().out
    assert (project / "release.json").read_text() == before
    assert release_tags(project) == ["1.0.0"]


def test_unpushed_commit_needs_allow_unpushed(project, capsys):
    _commit(project / "external" / "artic", "f", "local", "not pushed")
    assert create_release(project, "1.1.0", assume_yes=True, fetch=False) == 1
    assert "artic" in capsys.readouterr().out
    assert create_release(project, "1.1.0", assume_yes=True, fetch=False, allow_unpushed=True) == 0


def test_remote_pins_origin_tip_not_local_checkout(project):
    work = project / "external" / "wsrng-server"
    tip = _push_new_commit(project, "wsrng-server")
    _git(work, "checkout", "-q", "HEAD~1")  # local checkout lags behind origin
    assert create_release(project, "1.1.0", remote=True, assume_yes=True, fetch=True) == 0
    assert _manifest(project)["components"]["wsrng-server"]["ref"] == tip


def test_refuses_stray_changes_in_deployment_repo(project, capsys):
    (project / "quadlets" / "prod" / "mongo.container").write_text("[Container]\nImage=x:1\n")
    assert create_release(project, "1.1.0", assume_yes=True, fetch=False) == 1
    assert "quadlets/prod/mongo.container" in capsys.readouterr().out


def test_upgrade_module_is_committed_with_the_release(project):
    (project / "upgrades").mkdir()
    (project / "upgrades" / "1.1.0.py").write_text('"""VISP 1.1.0 — test."""\nSTEPS = []\n')
    assert create_release(project, "1.1.0", assume_yes=True, fetch=False) == 0
    assert "upgrades/1.1.0.py" in _git(project, "show", "--name-only", "--format=", "HEAD")


@pytest.mark.parametrize("version", ["1.0.0", "0.9.0"])
def test_refuses_version_not_newer_than_last_tag(project, version):
    with pytest.raises(ConfigError):
        create_release(project, version, assume_yes=True, fetch=False)


def test_declining_changes_nothing(project):
    before = (project / "release.json").read_text()
    assert create_release(project, "1.1.0", fetch=False, input_fn=lambda _: "n") == 1
    assert (project / "release.json").read_text() == before
    assert release_tags(project) == ["1.0.0"]
