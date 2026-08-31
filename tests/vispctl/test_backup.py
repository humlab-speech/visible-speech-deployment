import io
import subprocess
import sys
import tarfile
from pathlib import Path

# Ensure project package is importable when running tests
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # noqa: E402

from vispctl.backup import BackupManager  # noqa: E402


class _Result:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


class FakeRunner:
    def __init__(self, tmpdir: Path, version_out="mongod version v4.4.3"):
        self.tmpdir = Path(tmpdir)
        self.version_out = version_out
        self.calls = []

    def run_quiet(self, cmd):
        self.calls.append(("run_quiet", cmd))
        # version detection
        if "mongod" in cmd:
            return 0, self.version_out, ""
        # find command in restore
        if "find" in cmd:
            return 0, "/tmp/visp_restore_extract/visp_mongodb_6.0.14_20260101_120000", ""
        return 0, "", ""

    def unit_is_active(self, unit):
        _, out, _ = self.run_quiet(["systemctl", "--user", "is-active", f"{unit}.service"])
        return out.strip() in ("active", "activating")

    def run(self, cmd, capture=False, check=True, **kwargs):
        self.calls.append(("run", cmd))
        # Simulate success for all commands
        # If podman cp of archive to host, create the file to satisfy exists() check
        if cmd[:2] == ["podman", "cp"]:
            # find destination (last arg)
            dest = cmd[-1]
            try:
                Path(dest).write_bytes(b"dummy")
            except Exception:
                pass
        return _Result(returncode=0)


class FailingRunner:
    """Faithfully simulates Runner.run: with check=True a non-zero returncode
    raises CalledProcessError (exactly like the real Runner). Commands whose
    argv contains any of *fail_substrings* fail with returncode 1.
    """

    def __init__(self, tmpdir, fail_substrings=(), version_out="mongod version v4.4.3"):
        self.tmpdir = Path(tmpdir)
        self.fail_substrings = list(fail_substrings)
        self.version_out = version_out
        self.calls = []

    def _should_fail(self, cmd):
        return any(s in arg for arg in cmd for s in self.fail_substrings)

    def run(self, cmd, capture=False, check=True, **kwargs):
        self.calls.append(("run", cmd))
        if cmd[:2] == ["podman", "cp"]:
            try:
                Path(cmd[-1]).write_bytes(b"dummy")
            except Exception:
                pass
        rc = 1 if self._should_fail(cmd) else 0
        if check and rc != 0:
            raise subprocess.CalledProcessError(rc, cmd)
        return _Result(returncode=rc, stdout="", stderr="simulated failure" if rc else "")

    def run_quiet(self, cmd):
        self.calls.append(("run_quiet", cmd))
        if "mongod" in cmd:
            return 0, self.version_out, ""
        if "find" in cmd:
            return 0, "/tmp/visp_restore_extract/visp_mongodb_6.0.14_20260101_120000", ""
        return 0, "", ""

    def unit_is_active(self, unit):
        _, out, _ = self.run_quiet(["systemctl", "--user", "is-active", f"{unit}.service"])
        return out.strip() in ("active", "activating")


def test_list_backups(tmp_path):
    d = tmp_path / "backups"
    d.mkdir()
    f1 = d / "visp_mongodb_6.0.14_20260101_120000.tar.gz"
    f2 = d / "visp_mongodb_6.0.14_20260102_130000.tar.gz"
    other = d / "unrelated.tar.gz"
    f1.write_text("x")
    f2.write_text("y")
    other.write_text("z")

    bm = BackupManager(FakeRunner(tmp_path), project_dir=tmp_path)
    backups = bm.list_backups(directory=d)
    assert backups == sorted([f1, f2])
    assert other not in backups


def test_backup_dry_run(tmp_path):
    runner = FakeRunner(tmp_path)
    bm = BackupManager(runner, project_dir=tmp_path)
    bm.sm.load_all = lambda: {"MONGO_ROOT_PASSWORD": "pw"}

    out = bm.backup(output=None, dry_run=True)
    assert out is not None
    assert out.name.endswith(".tar.gz")


def test_backup_missing_password(tmp_path):
    runner = FakeRunner(tmp_path)
    bm = BackupManager(runner, project_dir=tmp_path)
    bm.sm.load_all = lambda: {}

    out = bm.backup(output=None, dry_run=False)
    assert out is None


def test_backup_success_creates_file(tmp_path):
    runner = FakeRunner(tmp_path)
    bm = BackupManager(runner, project_dir=tmp_path)
    bm.sm.load_all = lambda: {"MONGO_ROOT_PASSWORD": "pw"}

    out_path = tmp_path / "out.tar.gz"
    res = bm.backup(output=out_path, dry_run=False)
    assert res == out_path
    assert out_path.exists()


def test_restore_missing_file(tmp_path):
    runner = FakeRunner(tmp_path)
    bm = BackupManager(runner, project_dir=tmp_path)
    bm.sm.load_all = lambda: {"MONGO_ROOT_PASSWORD": "pw"}

    res = bm.restore(tmp_path / "nope.tar.gz", force=True)
    assert res is False


def _make_tarball(
    path: Path,
    entries: list[tuple[str, bytes]] = (),
    links: list[tuple[str, str]] = (),
    fifos: list[str] = (),
) -> Path:
    """Build a real .tar.gz: entries=(name, content), links=(name, target), fifos=[names]."""
    with tarfile.open(path, "w:gz") as tf:
        for name, content in entries:
            ti = tarfile.TarInfo(name)
            ti.size = len(content)
            tf.addfile(ti, io.BytesIO(content))
        for name, target in links:
            ti = tarfile.TarInfo(name)
            ti.type = tarfile.SYMTYPE
            ti.linkname = target
            tf.addfile(ti)
        for name in fifos:
            ti = tarfile.TarInfo(name)
            ti.type = tarfile.FIFOTYPE
            tf.addfile(ti)
    return path


def test_restore_success(tmp_path):
    runner = FakeRunner(tmp_path)
    bm = BackupManager(runner, project_dir=tmp_path)
    bm.sm.load_all = lambda: {"MONGO_ROOT_PASSWORD": "pw"}

    backup = _make_tarball(tmp_path / "test.tar.gz", [("db/coll.bson", b"data")])

    res = bm.restore(backup, force=True)
    assert res is True


# ── Deterministic restore-dir resolution ──────────────────────────────────────


class ResolveRunner:
    """Configurable fake runner for _resolve_restore_dir tests."""

    def __init__(self, test_dir_exists=True, find_dirs=()):
        self.test_dir_exists = test_dir_exists
        self.find_dirs = list(find_dirs)
        self.calls = []

    def run_quiet(self, cmd):
        self.calls.append(cmd)
        if "test" in cmd and "-d" in cmd:
            return (0 if self.test_dir_exists else 1), "", ""
        if "find" in cmd:
            return 0, "\n".join(self.find_dirs), ""
        return 0, "", ""

    def run(self, cmd, capture=False, check=True, **kwargs):
        self.calls.append(cmd)
        return _Result(returncode=0)


def test_resolve_restore_dir_from_tarball_name(tmp_path):
    """Derives the dir from a visp_mongodb_*.tar.gz name when it exists."""
    runner = ResolveRunner(
        test_dir_exists=True, find_dirs=["/tmp/visp_restore_extract/visp_mongodb_6.0.14_20260101_120000"]
    )
    bm = BackupManager(runner, project_dir=tmp_path)
    assert (
        bm._resolve_restore_dir("visp_mongodb_6.0.14_20260101_120000.tar.gz")
        == "/tmp/visp_restore_extract/visp_mongodb_6.0.14_20260101_120000"
    )


def test_resolve_restore_dir_prefers_name_over_stale(tmp_path):
    """Even with a stale dir present, the name-derived dir is used (not head -1)."""
    # find would list the stale dir first, but the name-derived dir wins.
    runner = ResolveRunner(
        test_dir_exists=True,
        find_dirs=[
            "/tmp/visp_restore_extract/visp_mongodb_STALE",
            "/tmp/visp_restore_extract/visp_mongodb_6.0.14_20260101_120000",
        ],
    )
    bm = BackupManager(runner, project_dir=tmp_path)
    assert (
        bm._resolve_restore_dir("visp_mongodb_6.0.14_20260101_120000.tar.gz")
        == "/tmp/visp_restore_extract/visp_mongodb_6.0.14_20260101_120000"
    )


def test_resolve_restore_dir_falls_back_to_single_dir(tmp_path):
    """A renamed tarball falls back to the single visp_mongodb_* dir."""
    runner = ResolveRunner(find_dirs=["/tmp/visp_restore_extract/visp_mongodb_6.0.14_20260101_120000"])
    bm = BackupManager(runner, project_dir=tmp_path)
    assert (
        bm._resolve_restore_dir("renamed_backup.tar.gz")
        == "/tmp/visp_restore_extract/visp_mongodb_6.0.14_20260101_120000"
    )


def test_resolve_restore_dir_ambiguous_returns_none(tmp_path):
    """Multiple leftover dirs (and no name match) → ambiguous → None."""
    runner = ResolveRunner(
        find_dirs=["/tmp/visp_restore_extract/visp_mongodb_a", "/tmp/visp_restore_extract/visp_mongodb_b"]
    )
    bm = BackupManager(runner, project_dir=tmp_path)
    assert bm._resolve_restore_dir("renamed.tar.gz") is None


def test_resolve_restore_dir_none_found(tmp_path):
    """No dir found → None."""
    runner = ResolveRunner(find_dirs=[])
    bm = BackupManager(runner, project_dir=tmp_path)
    assert bm._resolve_restore_dir("renamed.tar.gz") is None


def test_restore_wipes_extract_dir_and_uses_derived_dir(tmp_path):
    """restore wipes the dedicated extract dir and restores from the name-derived dir."""

    class TrackingRunner(FakeRunner):
        def __init__(self, tmpdir):
            super().__init__(tmpdir)
            self.mongorestore_dir = None
            self.wiped_extract_dir = False
            self.created_extract_dir = False

        def run_quiet(self, cmd):
            if "test" in cmd and "-d" in cmd:
                return 0, "", ""  # derived dir exists
            return super().run_quiet(cmd)

        def run(self, cmd, capture=False, check=True, **kwargs):
            if "rm" in cmd and "/tmp/visp_restore_extract" in cmd:
                self.wiped_extract_dir = True
            if "mkdir" in cmd and "/tmp/visp_restore_extract" in cmd:
                self.created_extract_dir = True
            if "mongorestore" in cmd:
                self.mongorestore_dir = cmd[-1]
            return super().run(cmd, capture=capture, check=check, **kwargs)

    runner = TrackingRunner(tmp_path)
    bm = BackupManager(runner, project_dir=tmp_path)
    bm.sm.load_all = lambda: {"MONGO_ROOT_PASSWORD": "pw"}

    backup = _make_tarball(tmp_path / "visp_mongodb_6.0.14_20260101_120000.tar.gz", [("db/coll.bson", b"data")])

    res = bm.restore(backup, force=True)
    assert res is True
    assert runner.wiped_extract_dir is True
    assert runner.created_extract_dir is True
    assert runner.mongorestore_dir == "/tmp/visp_restore_extract/visp_mongodb_6.0.14_20260101_120000"


# ── Archive member validation (real tarballs, not faked listings) ─────────────


def _restore_tarball(tmp_path, tarball_path):
    runner = FakeRunner(tmp_path)
    bm = BackupManager(runner, project_dir=tmp_path)
    bm.sm.load_all = lambda: {"MONGO_ROOT_PASSWORD": "pw"}
    # no_snapshot: these tests assert on the restore call sequence itself
    return bm.restore(tarball_path, force=True, no_snapshot=True), runner


def test_restore_rejects_traversal_members(tmp_path, capsys):
    tb = _make_tarball(tmp_path / "visp_mongodb_6.0.14_20260101_120000.tar.gz", [("../../evil", b"x")])
    res, runner = _restore_tarball(tmp_path, tb)
    assert res is False
    assert "unsafe" in capsys.readouterr().out
    assert not any(c[0] == "run" and c[1][:2] == ["podman", "cp"] for c in runner.calls)  # rejected before copy
    assert not any("mongorestore" in str(c) for c in runner.calls)


def test_restore_rejects_absolute_paths(tmp_path, capsys):
    tb = _make_tarball(tmp_path / "b.tar.gz", [("/etc/passwd", b"x")])
    res, _ = _restore_tarball(tmp_path, tb)
    assert res is False
    assert "unsafe" in capsys.readouterr().out


def test_restore_rejects_symlink_members(tmp_path, capsys):
    tb = _make_tarball(tmp_path / "b.tar.gz", links=[("link", "/data")])
    res, _ = _restore_tarball(tmp_path, tb)
    assert res is False
    assert "unsafe" in capsys.readouterr().out


def test_restore_refuses_while_writers_running(tmp_path, capsys):
    tb = _make_tarball(tmp_path / "b.tar.gz", [("data/users/bson", b"x")])

    class WritersRunningRunner(FakeRunner):
        def run_quiet(self, cmd):
            self.calls.append(("run_quiet", cmd))
            if "is-active" in cmd:
                unit = cmd[-1].replace(".service", "")
                return 0, "active" if unit == "session-manager" else "inactive", ""
            return super().run_quiet(cmd)

    runner = WritersRunningRunner(tmp_path)
    bm = BackupManager(runner, project_dir=tmp_path)
    bm.sm.load_all = lambda: {"MONGO_ROOT_PASSWORD": "pw"}
    res = bm.restore(tb, force=True, no_snapshot=True)
    out = capsys.readouterr().out
    assert res is False
    assert "Refusing to restore" in out
    assert "session-manager" in out
    # refused before touching the database
    assert not any(c[0] == "run" and c[1][:2] == ["podman", "cp"] for c in runner.calls)
    assert not any("mongorestore" in str(c) for c in runner.calls)

    # the override flag lets it proceed past the guard
    capsys.readouterr()
    res = bm.restore(tb, force=True, no_snapshot=True, allow_running_writers=True)
    assert "Refusing to restore" not in capsys.readouterr().out


def test_restore_rejects_fifo_members(tmp_path, capsys):
    # A FIFO named like a .bson file would make mongorestore block forever.
    tb = _make_tarball(tmp_path / "b.tar.gz", fifos=["db/coll.bson"])
    res, _ = _restore_tarball(tmp_path, tb)
    assert res is False
    assert "unsafe" in capsys.readouterr().out


def test_restore_rejects_unreadable_archive(tmp_path, capsys):
    tb = tmp_path / "b.tar.gz"
    tb.write_bytes(b"not a tarball")
    res, _ = _restore_tarball(tmp_path, tb)
    assert res is False
    assert "not a readable tar.gz" in capsys.readouterr().out


def test_restore_accepts_safe_members(tmp_path):
    tb = _make_tarball(
        tmp_path / "visp_mongodb_6.0.14_20260101_120000.tar.gz",
        [("visp_mongodb_6.0.14_20260101_120000/db/coll.bson", b"data")],
    )
    res, _ = _restore_tarball(tmp_path, tb)
    assert res is True


# ── Failure paths (no traceback, friendly error, falsy return) ────────────────


def test_backup_mongodump_failure_returns_none(tmp_path, capsys):
    """A failing mongodump returns None with a friendly error (no traceback)."""
    runner = FailingRunner(tmp_path, fail_substrings=["mongodump"])
    bm = BackupManager(runner, project_dir=tmp_path)
    bm.sm.load_all = lambda: {"MONGO_ROOT_PASSWORD": "pw"}

    out = bm.backup(output=tmp_path / "out.tar.gz", dry_run=False)

    assert out is None
    assert "Backup failed" in capsys.readouterr().out


def test_restore_corrupt_archive_returns_false(tmp_path, capsys):
    """A failing extract (corrupt archive) returns False, no traceback."""
    runner = FailingRunner(tmp_path, fail_substrings=["-xzf"])
    bm = BackupManager(runner, project_dir=tmp_path)
    bm.sm.load_all = lambda: {"MONGO_ROOT_PASSWORD": "pw"}

    backup = _make_tarball(tmp_path / "test.tar.gz", [("db/coll.bson", b"corrupt")])

    res = bm.restore(backup, force=True)

    assert res is False
    assert "Failed to extract" in capsys.readouterr().out


def test_restore_mongorestore_failure_returns_false(tmp_path, capsys):
    """A failing mongorestore returns False with a friendly error, no traceback."""
    # "--drop" is unique to the mongorestore command (the backup file path in the
    # earlier cp step also lives under a tmp dir named after this test).
    runner = FailingRunner(tmp_path, fail_substrings=["--drop"])
    bm = BackupManager(runner, project_dir=tmp_path)
    bm.sm.load_all = lambda: {"MONGO_ROOT_PASSWORD": "pw"}

    backup = _make_tarball(tmp_path / "test.tar.gz", [("db/coll.bson", b"data")])

    res = bm.restore(backup, force=True, drop=True)

    assert res is False
    assert "Restore failed" in capsys.readouterr().out


# ── D4: restore --drop is opt-in (default keeps existing collections) ─────────


class _MongorestoreCapture(FakeRunner):
    """Records the argv of the mongorestore invocation."""

    def __init__(self, tmpdir):
        super().__init__(tmpdir)
        self.mongorestore_argv = None

    def run(self, cmd, capture=False, check=True, **kwargs):
        if "mongorestore" in cmd:
            self.mongorestore_argv = list(cmd)
        return super().run(cmd, capture=capture, check=check, **kwargs)


def test_restore_default_does_not_drop(tmp_path):
    """Without --drop, mongorestore is invoked without the --drop flag."""
    runner = _MongorestoreCapture(tmp_path)
    bm = BackupManager(runner, project_dir=tmp_path)
    bm.sm.load_all = lambda: {"MONGO_ROOT_PASSWORD": "pw"}

    backup = _make_tarball(tmp_path / "test.tar.gz", [("db/coll.bson", b"data")])

    res = bm.restore(backup, force=True)

    assert res is True
    assert runner.mongorestore_argv is not None
    assert "--drop" not in runner.mongorestore_argv


def test_restore_drop_flag_passes_drop(tmp_path):
    """With --drop, mongorestore is invoked with the --drop flag."""
    runner = _MongorestoreCapture(tmp_path)
    bm = BackupManager(runner, project_dir=tmp_path)
    bm.sm.load_all = lambda: {"MONGO_ROOT_PASSWORD": "pw"}

    backup = _make_tarball(tmp_path / "test.tar.gz", [("db/coll.bson", b"data")])

    res = bm.restore(backup, force=True, drop=True)

    assert res is True
    assert runner.mongorestore_argv is not None
    assert "--drop" in runner.mongorestore_argv


def test_backup_copy_failure_returns_none(tmp_path, capsys):
    """A failing podman cp (copy out) returns None, no traceback."""
    runner = FailingRunner(tmp_path, fail_substrings=["mongo:/tmp/visp_mongodb"])
    bm = BackupManager(runner, project_dir=tmp_path)
    bm.sm.load_all = lambda: {"MONGO_ROOT_PASSWORD": "pw"}

    out = bm.backup(output=tmp_path / "out.tar.gz", dry_run=False)

    assert out is None
    assert "Copy failed" in capsys.readouterr().out


def test_list_backups_empty_dir(tmp_path):
    bm = BackupManager(FakeRunner(tmp_path), project_dir=tmp_path)
    assert bm.list_backups(tmp_path) == []
