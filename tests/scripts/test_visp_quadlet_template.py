"""Tests for render_quadlet_template, get_current_mode, set_current_mode,
and get_quadlets_dir — now in vispctl.quadlets."""

import os

import vispctl.quadlets as q

# ── render_quadlet_template ────────────────────────────────────────────────────


def test_render_replaces_project_dir(monkeypatch):
    monkeypatch.setattr(q, "load_env_vars", lambda _: {})
    result = q.render_quadlet_template("Volume=@@PROJECT_DIR@@/mounts/data:/data:Z")
    assert str(q._PROJECT_DIR) in result
    assert "@@PROJECT_DIR@@" not in result


def test_render_replaces_uid(monkeypatch):
    monkeypatch.setattr(q, "load_env_vars", lambda _: {})
    result = q.render_quadlet_template("User=@@UID@@")
    assert str(os.getuid()) in result
    assert "@@UID@@" not in result


def test_render_replaces_env_vars(monkeypatch):
    monkeypatch.setattr(q, "load_env_vars", lambda _: {"BASE_DOMAIN": "visp.local"})
    result = q.render_quadlet_template("ServerName=@@BASE_DOMAIN@@")
    assert "visp.local" in result
    assert "@@BASE_DOMAIN@@" not in result


def test_render_leaves_unknown_tokens_intact(monkeypatch):
    monkeypatch.setattr(q, "load_env_vars", lambda _: {})
    result = q.render_quadlet_template("Foo=@@UNKNOWN@@")
    assert "@@UNKNOWN@@" in result


def test_render_multiple_substitutions(monkeypatch):
    monkeypatch.setattr(q, "load_env_vars", lambda _: {"A": "alpha", "B": "beta"})
    result = q.render_quadlet_template("x=@@A@@ y=@@B@@")
    assert "alpha" in result
    assert "beta" in result
    assert "@@A@@" not in result
    assert "@@B@@" not in result


# ── get_current_mode / set_current_mode ───────────────────────────────────────


def test_default_mode_is_dev_when_file_absent(tmp_path, monkeypatch):
    monkeypatch.setattr(q, "_MODE_FILE", tmp_path / ".visp-mode")
    assert q.get_current_mode() == "dev"


def test_set_and_get_mode(tmp_path, monkeypatch):
    mode_file = tmp_path / ".visp-mode"
    monkeypatch.setattr(q, "_MODE_FILE", mode_file)
    q.set_current_mode("prod")
    assert mode_file.read_text() == "prod"
    assert q.get_current_mode() == "prod"


def test_set_mode_dev(tmp_path, monkeypatch):
    monkeypatch.setattr(q, "_MODE_FILE", tmp_path / ".visp-mode")
    q.set_current_mode("dev")
    assert q.get_current_mode() == "dev"


# ── get_quadlets_dir ───────────────────────────────────────────────────────────


def test_get_quadlets_dir_dev(monkeypatch):
    monkeypatch.setattr(q, "get_current_mode", lambda: "dev")
    result = q.get_quadlets_dir()
    assert result == q._QUADLETS_BASE_DIR / "dev"


def test_get_quadlets_dir_prod(monkeypatch):
    result = q.get_quadlets_dir("prod")
    assert result == q._QUADLETS_BASE_DIR / "prod"


def test_get_quadlets_dir_explicit_overrides_current_mode(monkeypatch):
    monkeypatch.setattr(q, "get_current_mode", lambda: "dev")
    result = q.get_quadlets_dir("prod")
    assert result.name == "prod"
