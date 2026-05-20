"""Tests for vispctl.quadlets: get_quadlet_drift and setup_service_env_files."""

from pathlib import Path

from vispctl.quadlets import get_quadlet_drift, setup_service_env_files
from vispctl.service import Service


def _svc(name: str) -> Service:
    return Service(name, "container", f"{name}.container")


# ── get_quadlet_drift ──────────────────────────────────────────────────────────


def test_no_drift_when_content_matches(tmp_path: Path) -> None:
    src = tmp_path / "quadlets" / "dev"
    dst = tmp_path / "systemd"
    src.mkdir(parents=True)
    dst.mkdir()

    svc = _svc("mongo")
    (src / svc.file).write_text("content")
    (dst / svc.file).write_text("content")  # identical

    drifted, not_installed = get_quadlet_drift([svc], src, dst, render_fn=lambda t: t)
    assert drifted == []
    assert not_installed == []


def test_drift_detected_when_content_differs(tmp_path: Path) -> None:
    src = tmp_path / "quadlets" / "dev"
    dst = tmp_path / "systemd"
    src.mkdir(parents=True)
    dst.mkdir()

    svc = _svc("mongo")
    (src / svc.file).write_text("new content")
    (dst / svc.file).write_text("old content")

    drifted, not_installed = get_quadlet_drift([svc], src, dst, render_fn=lambda t: t)
    assert svc in drifted
    assert not_installed == []


def test_not_installed_when_target_absent(tmp_path: Path) -> None:
    src = tmp_path / "quadlets" / "dev"
    dst = tmp_path / "systemd"
    src.mkdir(parents=True)
    dst.mkdir()

    svc = _svc("apache")
    (src / svc.file).write_text("content")
    # target NOT created

    drifted, not_installed = get_quadlet_drift([svc], src, dst, render_fn=lambda t: t)
    assert not_installed == [svc]
    assert drifted == []


def test_source_absent_service_is_ignored(tmp_path: Path) -> None:
    src = tmp_path / "quadlets" / "dev"
    dst = tmp_path / "systemd"
    src.mkdir(parents=True)
    dst.mkdir()

    svc = _svc("whisperx")
    # source file does NOT exist

    drifted, not_installed = get_quadlet_drift([svc], src, dst, render_fn=lambda t: t)
    assert drifted == []
    assert not_installed == []


def test_render_fn_is_applied_before_comparison(tmp_path: Path) -> None:
    """render_fn transforming the source content should be taken into account."""
    src = tmp_path / "quadlets" / "dev"
    dst = tmp_path / "systemd"
    src.mkdir(parents=True)
    dst.mkdir()

    svc = _svc("apache")
    (src / svc.file).write_text("@@TOKEN@@")
    (dst / svc.file).write_text("replaced")  # what render_fn would produce

    render_fn = lambda t: t.replace("@@TOKEN@@", "replaced")  # noqa: E731
    drifted, not_installed = get_quadlet_drift([svc], src, dst, render_fn=render_fn)
    assert drifted == []
    assert not_installed == []


def test_multiple_services_classified_correctly(tmp_path: Path) -> None:
    src = tmp_path / "quadlets" / "dev"
    dst = tmp_path / "systemd"
    src.mkdir(parents=True)
    dst.mkdir()

    ok = _svc("mongo")
    drifted_svc = _svc("apache")
    missing = _svc("session-manager")

    (src / ok.file).write_text("same")
    (dst / ok.file).write_text("same")

    (src / drifted_svc.file).write_text("new")
    (dst / drifted_svc.file).write_text("old")

    (src / missing.file).write_text("content")
    # missing target intentionally absent

    drifted, not_installed = get_quadlet_drift([ok, drifted_svc, missing], src, dst, render_fn=lambda t: t)
    assert drifted == [drifted_svc]
    assert not_installed == [missing]


# ── setup_service_env_files ────────────────────────────────────────────────────


def test_setup_creates_emu_env_from_example(tmp_path: Path, capsys) -> None:
    emu_example = tmp_path / "external" / "emu-webapp-server" / ".env-example"
    emu_example.parent.mkdir(parents=True)
    emu_example.write_text("NORMAL_KEY=value\nMONGO_URI=secret\n")

    setup_service_env_files(tmp_path)

    target = tmp_path / "mounts" / "emu-webapp-server" / ".env"
    assert target.exists()
    content = target.read_text()
    assert "NORMAL_KEY=value" in content
    assert "MONGO_URI" not in content  # stripped as a secret key


def test_setup_skips_emu_env_if_already_exists(tmp_path: Path, capsys) -> None:
    emu_env = tmp_path / "mounts" / "emu-webapp-server" / ".env"
    emu_env.parent.mkdir(parents=True)
    emu_env.write_text("existing content")

    setup_service_env_files(tmp_path)

    # File should be unchanged
    assert emu_env.read_text() == "existing content"
    out = capsys.readouterr().out
    assert "already exists" in out


def test_setup_warns_when_emu_example_missing(tmp_path: Path, capsys) -> None:
    # Neither example nor target exists
    setup_service_env_files(tmp_path)
    out = capsys.readouterr().out
    assert "not found" in out or "deploy update" in out


def test_setup_creates_wsrng_env_from_example(tmp_path: Path, capsys) -> None:
    wsrng_example = tmp_path / "external" / "wsrng-server" / ".env-example"
    wsrng_example.parent.mkdir(parents=True)
    wsrng_example.write_text("PORT=9010\n")

    setup_service_env_files(tmp_path)

    target = tmp_path / "external" / "wsrng-server" / ".env"
    assert target.exists()
    assert "PORT=9010" in target.read_text()
