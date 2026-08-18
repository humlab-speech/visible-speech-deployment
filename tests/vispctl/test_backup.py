import sys
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
            return 0, "/tmp/visp_mongodb_6.0.14_20260101_120000", ""
        return 0, "", ""

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


def test_list_backups(tmp_path):
    d = tmp_path / "backups"
    d.mkdir()
    f1 = d / "a.tar.gz"
    f2 = d / "b.tar.gz"
    f1.write_text("x")
    f2.write_text("y")

    bm = BackupManager(FakeRunner(tmp_path), project_dir=tmp_path)
    backups = bm.list_backups(directory=d)
    assert backups == sorted([f1, f2])


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


def test_restore_success(tmp_path):
    runner = FakeRunner(tmp_path)
    bm = BackupManager(runner, project_dir=tmp_path)
    bm.sm.load_all = lambda: {"MONGO_ROOT_PASSWORD": "pw"}

    backup = tmp_path / "test.tar.gz"
    backup.write_bytes(b"x")

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
    runner = ResolveRunner(test_dir_exists=True, find_dirs=["/tmp/visp_mongodb_6.0.14_20260101_120000"])
    bm = BackupManager(runner, project_dir=tmp_path)
    assert (
        bm._resolve_restore_dir("visp_mongodb_6.0.14_20260101_120000.tar.gz")
        == "/tmp/visp_mongodb_6.0.14_20260101_120000"
    )


def test_resolve_restore_dir_prefers_name_over_stale(tmp_path):
    """Even with a stale dir present, the name-derived dir is used (not head -1)."""
    # find would list the stale dir first, but the name-derived dir wins.
    runner = ResolveRunner(
        test_dir_exists=True, find_dirs=["/tmp/visp_mongodb_STALE", "/tmp/visp_mongodb_6.0.14_20260101_120000"]
    )
    bm = BackupManager(runner, project_dir=tmp_path)
    assert (
        bm._resolve_restore_dir("visp_mongodb_6.0.14_20260101_120000.tar.gz")
        == "/tmp/visp_mongodb_6.0.14_20260101_120000"
    )


def test_resolve_restore_dir_falls_back_to_single_dir(tmp_path):
    """A renamed tarball falls back to the single visp_mongodb_* dir."""
    runner = ResolveRunner(find_dirs=["/tmp/visp_mongodb_6.0.14_20260101_120000"])
    bm = BackupManager(runner, project_dir=tmp_path)
    assert bm._resolve_restore_dir("renamed_backup.tar.gz") == "/tmp/visp_mongodb_6.0.14_20260101_120000"


def test_resolve_restore_dir_ambiguous_returns_none(tmp_path):
    """Multiple leftover dirs (and no name match) → ambiguous → None."""
    runner = ResolveRunner(find_dirs=["/tmp/visp_mongodb_a", "/tmp/visp_mongodb_b"])
    bm = BackupManager(runner, project_dir=tmp_path)
    assert bm._resolve_restore_dir("renamed.tar.gz") is None


def test_resolve_restore_dir_none_found(tmp_path):
    """No dir found → None."""
    runner = ResolveRunner(find_dirs=[])
    bm = BackupManager(runner, project_dir=tmp_path)
    assert bm._resolve_restore_dir("renamed.tar.gz") is None


def test_restore_uses_derived_dir_and_cleans_stale(tmp_path):
    """restore cleans stale dirs first and restores from the name-derived dir."""

    class TrackingRunner(FakeRunner):
        def __init__(self, tmpdir):
            super().__init__(tmpdir)
            self.mongorestore_dir = None
            self.cleaned_stale = False

        def run_quiet(self, cmd):
            if "test" in cmd and "-d" in cmd:
                return 0, "", ""  # derived dir exists
            return super().run_quiet(cmd)

        def run(self, cmd, capture=False, check=True, **kwargs):
            if "find" in cmd and "-exec" in cmd:
                self.cleaned_stale = True
            if "mongorestore" in cmd:
                self.mongorestore_dir = cmd[-1]
            return super().run(cmd, capture=capture, check=check, **kwargs)

    runner = TrackingRunner(tmp_path)
    bm = BackupManager(runner, project_dir=tmp_path)
    bm.sm.load_all = lambda: {"MONGO_ROOT_PASSWORD": "pw"}

    backup = tmp_path / "visp_mongodb_6.0.14_20260101_120000.tar.gz"
    backup.write_bytes(b"x")

    res = bm.restore(backup, force=True)
    assert res is True
    assert runner.cleaned_stale is True
    assert runner.mongorestore_dir == "/tmp/visp_mongodb_6.0.14_20260101_120000"
