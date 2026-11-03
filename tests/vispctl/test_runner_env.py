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
    # default=False: with the old default=True the assertion was a tautology —
    # emptying the truthy set kept all 8 green (R2 F1).
    assert parse_env_bool(val, default=False) is True


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


# ── R2 pins: defaults, quote stripping, comment fixtures ─────────────────────


def test_parse_env_bool_none_default_false() -> None:
    assert parse_env_bool(None, default=False) is False


def test_load_env_vars_strips_quotes(tmp_path: Path) -> None:
    # Documented quote-stripping is in the loader (R2 F2), never pinned.
    f = tmp_path / "q.env"
    f.write_text('a="q uoted"\nb=\'yes\'\nc=unbalanced"\n')
    assert load_env_vars(f) == {"a": "q uoted", "b": "yes", "c": "unbalanced"}


def test_parse_env_bool_case_insensitive_via_lower_pinned() -> None:
    # A single case variant pinned with default=False kills .lower() removal
    # even if the parametrized truthy set ever narrows.
    assert parse_env_bool("TrUe", default=False) is True


def test_load_env_vars_ignores_comment_with_equals(tmp_path: Path) -> None:
    # env.py:18 mutant survived because no fixture had '# key=value' (R2 F3).
    f = tmp_path / "x.env"
    f.write_text("# key=value\nreal=1\n")
    result = load_env_vars(f)
    assert result == {"real": "1"}
