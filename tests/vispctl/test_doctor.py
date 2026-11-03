"""Tests for vispctl.doctor — project health checks."""

import os
from unittest.mock import patch

import pytest

from vispctl.doctor import _diagnose_project, parse_only_ids, run_doctor
from vispctl.exceptions import UserError

# ── Helpers ────────────────────────────────────────────────────────────────────


def _fake_project(pid="proj123"):
    """Build a minimal MongoDB project document."""
    return {
        "id": pid,
        "name": "Test Project",
        "members": [{"username": "testuser_at_example_dot_com", "role": "admin"}],
        "sessions": [],
    }


# ── --only parsing ─────────────────────────────────────────────────────────────


def test_parse_only_ids_none():
    assert parse_only_ids(None) is None


def test_parse_only_ids_plain():
    assert parse_only_ids("a3f1,b2e4") == {"a3f1", "b2e4"}


def test_parse_only_ids_strips_whitespace():
    assert parse_only_ids(" a3f1 , b2e4 ") == {"a3f1", "b2e4"}


def test_parse_only_ids_drops_empty_parts():
    assert parse_only_ids("a3f1,,b2e4,") == {"a3f1", "b2e4"}


def test_parse_only_ids_blank_returns_empty_set():
    # A whitespace-only value selects no fixes — never "all fixes".
    assert parse_only_ids(" , ") == set()


# ── --apply without --fix ──────────────────────────────────────────────────────


def test_apply_without_fix_raises():
    with patch("vispctl.doctor.mongosh_json", return_value=[]) as m:
        with pytest.raises(UserError, match="--apply requires --fix"):
            run_doctor(apply=True, fix=False)
    # The error must precede any database access.
    m.assert_not_called()


def test_apply_with_fix_does_not_warn(capsys):
    with patch("vispctl.doctor.mongosh_json", return_value=[]):
        run_doctor(apply=True, fix=True)

    out = capsys.readouterr().out
    assert "--apply has no effect" not in out


# ── Permission-denied project dirs ─────────────────────────────────────────────


def test_unreadable_session_dir_reported_as_issue(tmp_path):
    """A permission-denied session dir must be reported, not traceback."""
    if os.geteuid() == 0:
        pytest.skip("running as root — permission bits are ignored")

    repos = tmp_path / "repositories"
    db = repos / "proj123" / "Data" / "VISP_emuDB"
    session_dir = db / "Session_1_ses"
    session_dir.mkdir(parents=True)
    (db / "VISP_DBconfig.json").write_text("{}")
    session_dir.chmod(0o000)
    try:
        with patch("vispctl.doctor._get_repos_path", return_value=repos):
            report = _diagnose_project(_fake_project())
    finally:
        session_dir.chmod(0o755)

    assert any("Cannot fully scan project on disk" in i for i in report["issues"])
    assert any("PermissionError" in i for i in report["issues"])
    # The fix section relies on a complete scan and must be skipped
    assert report["fixes"] == []


def test_unreadable_emudb_dir_reported_as_issue(tmp_path):
    """A permission-denied emuDB dir must be reported, not traceback."""
    if os.geteuid() == 0:
        pytest.skip("running as root — permission bits are ignored")

    repos = tmp_path / "repositories"
    db = repos / "proj123" / "Data" / "VISP_emuDB"
    db.mkdir(parents=True)
    db.chmod(0o000)
    try:
        with patch("vispctl.doctor._get_repos_path", return_value=repos):
            report = _diagnose_project(_fake_project())
    finally:
        db.chmod(0o755)

    assert any("Cannot fully scan project on disk" in i for i in report["issues"])
    assert report["fixes"] == []


def test_scan_error_raised_by_helper_is_caught(tmp_path):
    """Any OSError from the scan helpers is caught and reported."""
    repos = tmp_path / "repositories"
    db = repos / "proj123" / "Data" / "VISP_emuDB"
    db.mkdir(parents=True)
    (db / "VISP_DBconfig.json").write_text("{}")

    with (
        patch("vispctl.doctor._get_repos_path", return_value=repos),
        patch("vispctl.doctor._disk_sessions", side_effect=PermissionError(13, "Permission denied")),
    ):
        report = _diagnose_project(_fake_project())

    assert any("Cannot fully scan project on disk (PermissionError)" in i for i in report["issues"])
    assert report["fixes"] == []


def test_readable_project_scans_normally(tmp_path):
    """A fully readable project produces no scan-error issues."""
    repos = tmp_path / "repositories"
    db = repos / "proj123" / "Data" / "VISP_emuDB"
    (db / "Session_1_ses" / "my_recording_bndl").mkdir(parents=True)
    (db / "VISP_DBconfig.json").write_text("{}")
    (db / "Session_1_ses" / "my_recording_bndl" / "my_recording.wav").write_bytes(b"RIFF")

    with patch("vispctl.doctor._get_repos_path", return_value=repos):
        report = _diagnose_project(_fake_project())

    assert not any("Cannot fully scan project on disk" in i for i in report["issues"])
    assert report["stats"]["audio_files"] == 1


# ── round-2 R2-DOC-01: --session/--bundle must scope the fix/apply pass ──────


def _mk_orphan_session(db_path, name):
    ses = db_path / f"{name}_ses"
    (ses / "x_bndl").mkdir(parents=True)
    return ses


def test_orphan_fix_respects_session_filter(tmp_path, monkeypatch):
    from vispctl import doctor

    monkeypatch.setattr(doctor, "_get_repos_path", lambda: tmp_path / "repos")
    db = tmp_path / "db"
    db.mkdir()
    keep = _mk_orphan_session(db, "keepme")
    drop = _mk_orphan_session(db, "dropme")
    monkeypatch.setattr(doctor, "_disk_sessions", lambda pid: {"keepme": keep, "dropme": drop})

    def real_move(src, dest):
        import shutil

        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(src), str(dest))
        return True

    monkeypatch.setattr(doctor, "_podman_move", real_move)
    fixes: list[dict] = []
    # --session names what IS in scope: the filtered-out orphan must survive.
    doctor._fix_orphan_bundles("proj", db, {}, fixes, dry_run=False, session_filter="dropme")
    moved = [f["desc"] for f in fixes if f["status"] == "applied"]
    assert any("dropme" in m for m in moved)
    assert not any("keepme" in m for m in moved)
    assert (db / "keepme_ses").exists()
    assert not (db / "dropme_ses").exists()


def test_prune_respects_session_filter(tmp_path, monkeypatch):
    from vispctl import doctor

    monkeypatch.setattr(doctor, "_get_repos_path", lambda: tmp_path / "repos")
    db = tmp_path / "db"
    bl = db / "bundleLists"
    bl.mkdir(parents=True)
    entries = [
        {"session": "keepme", "name": "b1"},  # stale (no dirs) but out of scope
        {"session": "dropme", "name": "b2"},  # stale and in scope
    ]
    (bl / "list.json").write_text(__import__("json").dumps(entries))
    fixes: list[dict] = []
    doctor._fix_stale_bundle_list_entries(
        "proj", db, fixes, dry_run=False, session_filter="dropme"
    )  # scope=dropme: keepme entry must be kept unstale-checked
    kept = __import__("json").loads((bl / "list.json").read_text())
    assert {"session": "keepme", "name": "b1"} in kept
    assert {"session": "dropme", "name": "b2"} not in kept


def test_diagnose_passes_scope_to_both_fixers(monkeypatch, tmp_path):
    """Call-site wiring: filters must reach the fix functions, not just exist."""
    from vispctl import doctor

    seen: list[dict] = []

    def spy_orphan(*a, **k):
        seen.append(("orphan", k))

    def spy_prune(*a, **k):
        seen.append(("prune", k))

    monkeypatch.setattr(doctor, "_fix_orphan_bundles", spy_orphan)
    monkeypatch.setattr(doctor, "_fix_stale_bundle_list_entries", spy_prune)
    db = tmp_path / "db"
    db.mkdir()
    (tmp_path / "repos" / "p1").mkdir(parents=True)  # repo_dir gate
    monkeypatch.setattr(doctor, "_emu_db_path", lambda pid: db)
    monkeypatch.setattr(doctor, "_disk_sessions", lambda pid: {})
    monkeypatch.setattr(doctor, "_bundle_list_entries", lambda pid: {})
    monkeypatch.setattr(doctor, "_get_repos_path", lambda: tmp_path / "repos")

    doctor._diagnose_project(
        {"id": "p1", "sessions": []},
        fix=True,
        apply=True,
        session_filter="keepme",
        bundle_filter="b9",
    )
    assert len(seen) == 2
    for _name, kwargs in seen:
        assert kwargs.get("session_filter") == "keepme"
        assert kwargs.get("bundle_filter") == "b9"


def test_run_doctor_returns_issue_count(monkeypatch):
    """Contract: return value == error count (drives visp.py's exit code)."""
    from vispctl import doctor

    monkeypatch.setattr(doctor, "mongosh_json", lambda q: [{"id": "p1", "sessions": []}])
    monkeypatch.setattr(
        doctor,
        "_diagnose_project",
        lambda *a, **k: {
            "id": "p1",
            "name": "p1",
            "issues": ["one error"],
            "warnings": [],
            "stats": {"audio_files": 0, "total_audio_bytes": 0},
            "sessions": [],
        },
    )
    monkeypatch.setattr(doctor, "_find_disk_orphans", lambda ids: [])
    monkeypatch.setattr(doctor, "_render_tree", lambda *a, **k: None)
    assert doctor.run_doctor(show_files=False) == 1
