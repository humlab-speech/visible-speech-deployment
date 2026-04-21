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
