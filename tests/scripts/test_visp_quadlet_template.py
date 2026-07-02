"""Tests for render_quadlet_template, get_current_mode, set_current_mode,
and get_quadlets_dir — now in vispctl.quadlets."""

import os

import vispctl.config as cfg_mod
import vispctl.quadlets as q


def _init_config_for_test(tmp_path):
    cfg_mod.init_config(project_dir=tmp_path)


# ── render_quadlet_template ────────────────────────────────────────────────────


def test_render_replaces_project_dir(tmp_path, monkeypatch):
    _init_config_for_test(tmp_path)
    monkeypatch.setattr(q, "load_env_file", lambda _: {})
    result = q.render_quadlet_template("Volume=@@PROJECT_DIR@@/mounts/data:/data:Z")
    assert str(tmp_path) in result
    assert "@@PROJECT_DIR@@" not in result


def test_render_replaces_uid(tmp_path, monkeypatch):
    _init_config_for_test(tmp_path)
    monkeypatch.setattr(q, "load_env_file", lambda _: {})
    result = q.render_quadlet_template("User=@@UID@@")
    assert str(os.getuid()) in result
    assert "@@UID@@" not in result


def test_render_replaces_env_vars(tmp_path, monkeypatch):
    _init_config_for_test(tmp_path)
    monkeypatch.setattr(q, "load_env_file", lambda _: {"BASE_DOMAIN": "visp.local"})
    result = q.render_quadlet_template("ServerName=@@BASE_DOMAIN@@")
    assert "visp.local" in result
    assert "@@BASE_DOMAIN@@" not in result


def test_render_leaves_unknown_tokens_intact(tmp_path, monkeypatch):
    _init_config_for_test(tmp_path)
    monkeypatch.setattr(q, "load_env_file", lambda _: {})
    result = q.render_quadlet_template("Foo=@@UNKNOWN@@")
    assert "@@UNKNOWN@@" in result


def test_render_multiple_substitutions(tmp_path, monkeypatch):
    _init_config_for_test(tmp_path)
    monkeypatch.setattr(q, "load_env_file", lambda _: {"A": "alpha", "B": "beta"})
    result = q.render_quadlet_template("x=@@A@@ y=@@B@@")
    assert "alpha" in result
    assert "beta" in result
    assert "@@A@@" not in result
    assert "@@B@@" not in result


# ── get_current_mode / set_current_mode ───────────────────────────────────────


def test_default_mode_is_dev_when_file_absent(tmp_path):
    _init_config_for_test(tmp_path)
    assert q.get_current_mode() == "dev"


def test_set_and_get_mode(tmp_path):
    _init_config_for_test(tmp_path)
    q.set_current_mode("prod")
    assert (tmp_path / ".visp-mode").read_text() == "prod"
    assert q.get_current_mode() == "prod"


def test_set_mode_dev(tmp_path):
    _init_config_for_test(tmp_path)
    q.set_current_mode("dev")
    assert q.get_current_mode() == "dev"


# ── get_quadlets_dir ───────────────────────────────────────────────────────────


def test_get_quadlets_dir_dev(tmp_path, monkeypatch):
    _init_config_for_test(tmp_path)
    monkeypatch.setattr(q, "get_current_mode", lambda: "dev")
    result = q.get_quadlets_dir()
    assert result == tmp_path / "quadlets" / "dev"


def test_get_quadlets_dir_prod(tmp_path):
    _init_config_for_test(tmp_path)
    result = q.get_quadlets_dir("prod")
    assert result == tmp_path / "quadlets" / "prod"


def test_get_quadlets_dir_explicit_overrides_current_mode(tmp_path, monkeypatch):
    _init_config_for_test(tmp_path)
    monkeypatch.setattr(q, "get_current_mode", lambda: "dev")
    result = q.get_quadlets_dir("prod")
    assert result.name == "prod"
