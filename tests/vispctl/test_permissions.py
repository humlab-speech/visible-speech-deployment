import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # noqa: E402

from vispctl.permissions import PermissionsManager  # noqa: E402


class FakeResult:
    def __init__(self, returncode=0):
        self.returncode = returncode


class FakeRunner:
    def __init__(self):
        self.calls = []

    def run(self, cmd, check=True, **kwargs):
        self.calls.append(cmd)
        return FakeResult(returncode=0)


def test_plan_fix_default_uid(tmp_path):
    runner = FakeRunner()
    pm = PermissionsManager(runner, project_dir=tmp_path)

    paths = [Path("/tmp/foo"), Path("/tmp/bar")]
    cmds = pm.plan_fix(paths, recursive=False)

    assert any("podman unshare chown" in c for c in cmds)
    assert any("podman unshare chmod" in c for c in cmds)


def test_apply_fix_calls_runner(tmp_path):
    runner = FakeRunner()
    pm = PermissionsManager(runner, project_dir=tmp_path)

    paths = [Path("/tmp/foo")]
    ok = pm.apply_fix(paths, recursive=False)

    assert ok is True
    # two calls per path (chown + chmod)
    assert len(runner.calls) == 2
    assert runner.calls[0][:3] == ["podman", "unshare", "chown"]
    assert runner.calls[1][:3] == ["podman", "unshare", "chmod"]


def test_apply_fix_recursive(tmp_path):
    runner = FakeRunner()
    pm = PermissionsManager(runner, project_dir=tmp_path)

    paths = [Path("/tmp/foo")]
    ok = pm.apply_fix(paths, recursive=True)

    assert ok is True
    assert "-R" in runner.calls[0]
    assert "-R" in runner.calls[1]


def test_plan_fix_host_owner(tmp_path):
    runner = FakeRunner()
    pm = PermissionsManager(runner, project_dir=tmp_path)

    paths = [Path("/tmp/foo")]
    cmds = pm.plan_fix(paths, recursive=False, host_owner=True)

    assert any("chown" in c and c.split()[3].startswith("0:0") for c in cmds)


def test_apply_fix_host_owner_calls_chown_zero(tmp_path):
    runner = FakeRunner()
    pm = PermissionsManager(runner, project_dir=tmp_path)

    paths = [Path("/tmp/foo")]
    ok = pm.apply_fix(paths, recursive=False, host_owner=True)

    assert ok is True
    assert runner.calls[0][:3] == ["podman", "unshare", "chown"]
    # ensure chown target is 0:0 in the command list
    assert any("0:0" in " ".join(call) for call in runner.calls)
