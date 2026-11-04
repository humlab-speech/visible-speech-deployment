"""Tests for vispctl.release and the release.json side of vispctl.versions / deploy."""

import json
import subprocess
from pathlib import Path

import pytest

from vispctl.deploy import DeployManager
from vispctl.exceptions import ConfigError
from vispctl.release import (
    InstallState,
    Manifest,
    check_manifest,
    parse_version,
    quadlet_images,
    sync_manifest,
    vendored_pins,
)
from vispctl.versions import DEFAULT_VERSIONS_CONFIG, ComponentConfig

REPO = Path(__file__).resolve().parents[2]


# ── the repository's own release.json ─────────────────────────────────────────────


def test_repo_release_json_is_consistent():
    """release.json must match quadlets/ and the Dockerfiles, and pin every component.

    Fix a failure with './visp.py release sync' (images, vendored) or
    './visp.py release pin <component>'.
    """
    manifest = Manifest.load(REPO)
    assert check_manifest(manifest, REPO) == []


def test_repo_upgrade_modules_load():
    from vispctl.upgrade import discover_upgrades, load_release_upgrade

    manifest = Manifest.load(REPO)
    for rel in discover_upgrades(REPO):
        assert parse_version(rel.version) <= parse_version(manifest.version)
    template = load_release_upgrade(REPO / "upgrades" / "_template.py")
    assert template.steps == []  # the examples must stay inert


# ── versions ──────────────────────────────────────────────────────────────────────


def test_parse_version_orders_numerically():
    assert parse_version("1.10.0") > parse_version("1.9.3")
    for bad in ("1.0", "v1.0.0", "1.0.0-rc1", "01.0.0"):
        with pytest.raises(ConfigError):
            parse_version(bad)


# ── image and vendored pins ───────────────────────────────────────────────────────


def _quadlet(project: Path, mode: str, name: str, image: str) -> None:
    d = project / "quadlets" / mode
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{name}.container").write_text(f"[Container]\nImage={image}\n")


def test_quadlet_images_skips_own_images_and_flags_mode_mismatch(tmp_path):
    _quadlet(tmp_path, "dev", "apache", "localhost/visp-apache:latest")
    _quadlet(tmp_path, "prod", "apache", "localhost/visp-apache:latest")
    _quadlet(tmp_path, "dev", "mongo", "docker.io/library/mongo:6.0.27")
    _quadlet(tmp_path, "prod", "mongo", "docker.io/library/mongo:6.0.27")
    _quadlet(tmp_path, "dev", "mongo-express", "docker.io/library/mongo-express:1")
    _quadlet(tmp_path, "dev", "matomo", "docker.io/library/matomo:5.14")
    _quadlet(tmp_path, "prod", "matomo", "docker.io/library/matomo:5.8.0")

    images, problems = quadlet_images(tmp_path)

    assert images == {"mongo": "docker.io/library/mongo:6.0.27", "mongo-express": "docker.io/library/mongo-express:1"}
    assert len(problems) == 1 and problems[0].startswith("matomo:")


def test_vendored_pins_reads_tratt_dockerfile(tmp_path):
    d = tmp_path / "docker" / "tratt"
    d.mkdir(parents=True)
    sha = "a" * 40
    (d / "Dockerfile").write_text(
        f"RUN git clone --branch x --depth 1 https://github.com/example/TRATT.git . && \\\n    git checkout {sha}\n"
    )
    pins, problems = vendored_pins(tmp_path)
    assert problems == []
    assert pins["tratt"] == {
        "repo": "https://github.com/example/TRATT.git",
        "ref": sha,
        "pinned_in": "docker/tratt/Dockerfile",
    }


def test_sync_then_check_detects_drift(tmp_path):
    _quadlet(tmp_path, "prod", "mongo", "docker.io/library/mongo:6.0.27")
    d = tmp_path / "docker" / "tratt"
    d.mkdir(parents=True)
    (d / "Dockerfile").write_text(f"RUN git clone https://github.com/example/T.git . && git checkout {'b' * 40}\n")
    components = {name: {"ref": "c" * 40} for name in DEFAULT_VERSIONS_CONFIG}
    (tmp_path / "release.json").write_text(json.dumps({"visp": "1.0.0", "components": components}))

    manifest = Manifest.load(tmp_path)
    changes = sync_manifest(manifest, tmp_path)
    manifest.save()
    assert any(c.startswith("images.mongo") for c in changes)
    assert check_manifest(Manifest.load(tmp_path), tmp_path) == []

    _quadlet(tmp_path, "prod", "mongo", "docker.io/library/mongo:7.0.20")
    errors = check_manifest(Manifest.load(tmp_path), tmp_path)
    assert len(errors) == 1 and "images.mongo" in errors[0]


def test_check_requires_full_shas_for_every_component(tmp_path):
    (tmp_path / "release.json").write_text(json.dumps({"visp": "1.0.0", "components": {"webclient": {"ref": "abc"}}}))
    (tmp_path / "docker" / "tratt").mkdir(parents=True)
    (tmp_path / "docker" / "tratt" / "Dockerfile").write_text(
        f"RUN git clone https://github.com/x/T.git . && git checkout {'d' * 40}\n"
    )
    manifest = Manifest.load(tmp_path)
    sync_manifest(manifest, tmp_path)
    errors = check_manifest(manifest, tmp_path)
    assert any("components.webclient" in e and "40-character" in e for e in errors)
    assert any("components.session-manager: not pinned" in e for e in errors)


# ── ComponentConfig over release.json ──────────────────────────────────────────────


def test_component_config_save_keeps_other_sections(tmp_path):
    path = tmp_path / "release.json"
    path.write_text(json.dumps({"visp": "1.2.3", "images": {"mongo": "m:1"}, "components": {}}))
    cfg = ComponentConfig(str(path))
    assert cfg.is_locked("webclient") is False
    cfg.lock("webclient", "e" * 40)
    cfg.save()

    data = json.loads(path.read_text())
    assert data["visp"] == "1.2.3" and data["images"] == {"mongo": "m:1"}
    assert data["components"]["webclient"] == {"ref": "e" * 40}
    assert data["components"]["session-manager"] == {"ref": None}

    cfg = ComponentConfig(str(path))
    assert cfg.get_version("webclient") == "e" * 40
    assert cfg.get_component("webclient")["url"] == DEFAULT_VERSIONS_CONFIG["webclient"]["url"]
    cfg.unlock("webclient")
    cfg.save()
    assert json.loads(path.read_text())["components"]["webclient"] == {"ref": None}


# ── deploy update --release ───────────────────────────────────────────────────────


def _git(args, cwd):
    return subprocess.run(["git"] + args, cwd=cwd, check=True, capture_output=True, text=True)


def _origin_and_clone(base: Path, name: str) -> tuple[str, str, Path]:
    seed = base / "seed" / name
    seed.mkdir(parents=True)
    _git(["init", "-q", "-b", "main"], seed)
    _git(["config", "user.email", "t@example.com"], seed)
    _git(["config", "user.name", "T"], seed)
    shas = []
    for v in ("1", "2"):
        (seed / "f").write_text(v)
        _git(["add", "."], seed)
        _git(["commit", "-q", "-m", v], seed)
        shas.append(_git(["rev-parse", "HEAD"], seed).stdout.strip())
    return shas[0], shas[1], seed


def test_update_release_checks_out_pins_and_clones_missing(tmp_path):
    project = tmp_path / "proj"
    (project / "external").mkdir(parents=True)
    names = list(DEFAULT_VERSIONS_CONFIG)
    origins, pins = {}, {}
    for name in names:
        sha1, _sha2, seed = _origin_and_clone(tmp_path, name)
        origins[name], pins[name] = seed, sha1
    (project / "release.json").write_text(
        json.dumps({"visp": "1.0.0", "components": {n: {"ref": pins[n]} for n in names}})
    )
    _git(["clone", "-q", str(origins["webclient"]), str(project / "external" / "webclient")], tmp_path)

    dm = DeployManager(str(project))
    for name in names:
        dm.config.get_component(name)["url"] = str(origins[name])
        dm.config.get_component(name)["submodules"] = False

    assert dm.update_components(release=True) is True
    for name in names:
        assert _git(["rev-parse", "HEAD"], project / "external" / name).stdout.strip() == pins[name]


def test_update_release_refuses_dirty_repo(tmp_path):
    project = tmp_path / "proj"
    (project / "external").mkdir(parents=True)
    sha1, _sha2, seed = _origin_and_clone(tmp_path, "webclient")
    repo = project / "external" / "webclient"
    _git(["clone", "-q", str(seed), str(repo)], tmp_path)
    (repo / "f").write_text("local edit")
    (project / "release.json").write_text(json.dumps({"visp": "1.0.0", "components": {"webclient": {"ref": sha1}}}))

    dm = DeployManager(
        str(project),
    )
    dm.config.config = {"webclient": {**dm.config.get_component("webclient"), "url": str(seed)}}

    assert dm.update_components(release=True) is False
    assert (repo / "f").read_text() == "local edit"


# ── installed state ──────────────────────────────────────────────────────────────


def test_install_state_record_keeps_history(tmp_path):
    state = InstallState.load(tmp_path)
    assert state.version is None and not state.exists
    state.record("1.0.0", "install")
    state.data["in_progress"] = {"from": "1.0.0", "to": "1.1.0", "done": []}
    state.record("1.1.0", "upgrade", "f" * 40)

    state = InstallState.load(tmp_path)
    assert state.version == "1.1.0"
    assert state.in_progress is None
    assert [(h["from"], h["to"], h["how"]) for h in state.data["history"]] == [
        (None, "1.0.0", "install"),
        ("1.0.0", "1.1.0", "upgrade"),
    ]
