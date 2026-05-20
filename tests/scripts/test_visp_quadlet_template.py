"""Tests for render_quadlet_template, get_current_mode, set_current_mode,
and get_quadlets_dir in visp.py."""

import importlib.util
import os
import sys
from pathlib import Path


def load_visp_module():
    proj = str(Path.cwd())
    if proj not in sys.path:
        sys.path.insert(0, proj)
    spec = importlib.util.spec_from_file_location("vp", str(Path.cwd() / "visp.py"))
    vp = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(vp)
    return vp


# ── render_quadlet_template ────────────────────────────────────────────────────


def test_render_replaces_project_dir(monkeypatch):
    vp = load_visp_module()
    monkeypatch.setattr(vp, "load_env_vars", lambda _: {})
    result = vp.render_quadlet_template("Volume=@@PROJECT_DIR@@/mounts/data:/data:Z")
    assert str(vp.PROJECT_DIR) in result
    assert "@@PROJECT_DIR@@" not in result


def test_render_replaces_uid(monkeypatch):
    vp = load_visp_module()
    monkeypatch.setattr(vp, "load_env_vars", lambda _: {})
    result = vp.render_quadlet_template("User=@@UID@@")
    assert str(os.getuid()) in result
    assert "@@UID@@" not in result


def test_render_replaces_env_vars(monkeypatch):
    vp = load_visp_module()
    monkeypatch.setattr(vp, "load_env_vars", lambda _: {"BASE_DOMAIN": "visp.local"})
    result = vp.render_quadlet_template("ServerName=@@BASE_DOMAIN@@")
    assert "visp.local" in result
    assert "@@BASE_DOMAIN@@" not in result


def test_render_leaves_unknown_tokens_intact(monkeypatch):
    vp = load_visp_module()
    monkeypatch.setattr(vp, "load_env_vars", lambda _: {})
    result = vp.render_quadlet_template("Foo=@@UNKNOWN@@")
    assert "@@UNKNOWN@@" in result


def test_render_multiple_substitutions(monkeypatch):
    vp = load_visp_module()
    monkeypatch.setattr(vp, "load_env_vars", lambda _: {"A": "alpha", "B": "beta"})
    result = vp.render_quadlet_template("x=@@A@@ y=@@B@@")
    assert "alpha" in result
    assert "beta" in result
    assert "@@A@@" not in result
    assert "@@B@@" not in result


# ── get_current_mode / set_current_mode ───────────────────────────────────────


def test_default_mode_is_dev_when_file_absent(tmp_path, monkeypatch):
    vp = load_visp_module()
    monkeypatch.setattr(vp, "MODE_FILE", tmp_path / ".visp-mode")
    assert vp.get_current_mode() == "dev"


def test_set_and_get_mode(tmp_path, monkeypatch):
    vp = load_visp_module()
    mode_file = tmp_path / ".visp-mode"
    monkeypatch.setattr(vp, "MODE_FILE", mode_file)
    vp.set_current_mode("prod")
    assert mode_file.read_text() == "prod"
    assert vp.get_current_mode() == "prod"


def test_set_mode_dev(tmp_path, monkeypatch):
    vp = load_visp_module()
    monkeypatch.setattr(vp, "MODE_FILE", tmp_path / ".visp-mode")
    vp.set_current_mode("dev")
    assert vp.get_current_mode() == "dev"


# ── get_quadlets_dir ───────────────────────────────────────────────────────────


def test_get_quadlets_dir_dev(monkeypatch):
    vp = load_visp_module()
    monkeypatch.setattr(vp, "get_current_mode", lambda: "dev")
    result = vp.get_quadlets_dir()
    assert result == vp.QUADLETS_BASE_DIR / "dev"


def test_get_quadlets_dir_prod(monkeypatch):
    vp = load_visp_module()
    result = vp.get_quadlets_dir("prod")
    assert result == vp.QUADLETS_BASE_DIR / "prod"


def test_get_quadlets_dir_explicit_overrides_current_mode(monkeypatch):
    vp = load_visp_module()
    monkeypatch.setattr(vp, "get_current_mode", lambda: "dev")
    result = vp.get_quadlets_dir("prod")
    assert result.name == "prod"
