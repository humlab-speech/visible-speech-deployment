"""Tests for load_env_vars and parse_env_bool in vispctl.runner."""

from pathlib import Path

import pytest

from vispctl.runner import load_env_vars, parse_env_bool

# ── load_env_vars ──────────────────────────────────────────────────────────────


def test_load_env_vars_basic(tmp_path: Path) -> None:
    env = tmp_path / ".env"
    env.write_text("FOO=bar\nBAZ=123\n", encoding="utf-8")
    result = load_env_vars(env)
    assert result == {"FOO": "bar", "BAZ": "123"}


def test_load_env_vars_skips_comments(tmp_path: Path) -> None:
    env = tmp_path / ".env"
    env.write_text("# this is a comment\nKEY=value\n", encoding="utf-8")
    result = load_env_vars(env)
    assert "# this is a comment" not in result
    assert result["KEY"] == "value"


def test_load_env_vars_skips_blank_lines(tmp_path: Path) -> None:
    env = tmp_path / ".env"
    env.write_text("\n\nKEY=value\n\n", encoding="utf-8")
    result = load_env_vars(env)
    assert result == {"KEY": "value"}


def test_load_env_vars_value_with_equals(tmp_path: Path) -> None:
    """Values that contain '=' should be preserved correctly."""
    env = tmp_path / ".env"
    env.write_text("URL=http://host/path?a=1&b=2\n", encoding="utf-8")
    result = load_env_vars(env)
    assert result["URL"] == "http://host/path?a=1&b=2"


def test_load_env_vars_strips_whitespace(tmp_path: Path) -> None:
    env = tmp_path / ".env"
    env.write_text("  KEY  =  value  \n", encoding="utf-8")
    result = load_env_vars(env)
    assert result["KEY"] == "value"


def test_load_env_vars_missing_file(tmp_path: Path) -> None:
    result = load_env_vars(tmp_path / "nonexistent.env")
    assert result == {}


# ── parse_env_bool ─────────────────────────────────────────────────────────────


@pytest.mark.parametrize("val", ["1", "true", "True", "TRUE", "yes", "YES", "on", "ON"])
def test_parse_env_bool_truthy(val: str) -> None:
    assert parse_env_bool(val) is True


@pytest.mark.parametrize("val", ["0", "false", "False", "FALSE", "no", "NO", "off", "OFF"])
def test_parse_env_bool_falsy(val: str) -> None:
    assert parse_env_bool(val) is False


def test_parse_env_bool_none_uses_default_true() -> None:
    assert parse_env_bool(None, default=True) is True


def test_parse_env_bool_none_uses_default_false() -> None:
    assert parse_env_bool(None, default=False) is False


def test_parse_env_bool_unknown_value_falls_back_to_default() -> None:
    assert parse_env_bool("maybe", default=True) is True
    assert parse_env_bool("maybe", default=False) is False
