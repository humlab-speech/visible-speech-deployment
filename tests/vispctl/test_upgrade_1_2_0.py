"""upgrades/1.2.0.py: emu-webapp-server is renamed annotation-server."""

import subprocess
from pathlib import Path

import pytest

from vispctl.release import InstallState, Manifest
from vispctl.upgrade import UpgradeContext, UpgradeError, load_release_upgrade

REPO = Path(__file__).resolve().parents[2]


class FakeRunner:
    def __init__(self, origin="https://github.com/humlab-speech/emu-webapp-server.git"):
        self.origin = origin
        self.calls: list[list[str]] = []

    def unit_is_active(self, unit):
        return False

    def systemctl(self, *args, check=False):
        return self.run(["systemctl", "--user", *args])

    def run(self, cmd, capture=False, check=True, **kwargs):
        self.calls.append(cmd)
        out = ""
        if cmd[:1] == ["git"] and "get-url" in cmd:
            out = self.origin
        if cmd[:1] == ["git"] and "set-url" in cmd:
            self.origin = cmd[-1]
        return subprocess.CompletedProcess(cmd, 0, stdout=out + "\n", stderr="")


@pytest.fixture
def upgrade():
    return load_release_upgrade(REPO / "upgrades" / "1.2.0.py")


def _ctx(tmp_path, runner):
    ctx = UpgradeContext(
        tmp_path, runner, "prod", InstallState.load(tmp_path), Manifest(tmp_path / "r.json", {"visp": "1.2.0"}), "1.1.0"
    )
    ctx.release = "1.2.0"
    return ctx


def _step(upgrade, step_id):
    return next(s for s in upgrade.steps if s.id == step_id)


def _old_layout(tmp_path):
    (tmp_path / "external/emu-webapp-server/.git").mkdir(parents=True)
    (tmp_path / "external/emu-webapp-server/src").mkdir()
    (tmp_path / "mounts/emu-webapp-server").mkdir(parents=True)
    (tmp_path / "mounts/emu-webapp-server/.env").write_text("MONGO_DB_NAME=visp\n")


def test_rename_moves_checkout_and_mounts_and_repoints_origin(tmp_path, upgrade):
    _old_layout(tmp_path)
    runner = FakeRunner()
    ctx = _ctx(tmp_path, runner)
    step = _step(upgrade, "rename-annotation-server")

    assert not step.done(ctx)
    step.run(ctx)

    assert ["systemctl", "--user", "stop", "emu-webapp-server.service"] in runner.calls
    assert (tmp_path / "external/annotation-server/src").is_dir()
    assert (tmp_path / "mounts/annotation-server/.env").read_text() == "MONGO_DB_NAME=visp\n"
    assert not (tmp_path / "external/emu-webapp-server").exists()
    assert runner.origin == "https://github.com/humlab-speech/annotation-server.git"
    assert step.done(ctx)


def test_rename_stops_the_old_unit_before_moving_anything(tmp_path, upgrade):
    _old_layout(tmp_path)
    runner = FakeRunner()
    seen = []

    def systemctl(*args, check=False):
        seen.append((args, (tmp_path / "mounts/emu-webapp-server").exists()))
        return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

    runner.systemctl = systemctl
    _step(upgrade, "rename-annotation-server").run(_ctx(tmp_path, runner))

    assert seen == [(("stop", "emu-webapp-server.service"), True)]


def test_rename_resumes_after_a_partial_run(tmp_path, upgrade):
    _old_layout(tmp_path)
    (tmp_path / "mounts/emu-webapp-server").rename(tmp_path / "mounts/annotation-server")
    ctx = _ctx(tmp_path, FakeRunner())
    step = _step(upgrade, "rename-annotation-server")

    step.run(ctx)

    assert (tmp_path / "external/annotation-server").is_dir()
    assert step.done(ctx)


def test_rename_leaves_a_foreign_origin_alone(tmp_path, upgrade):
    _old_layout(tmp_path)
    runner = FakeRunner(origin="https://example.org/fork.git")
    _step(upgrade, "rename-annotation-server").run(_ctx(tmp_path, runner))

    assert runner.origin == "https://example.org/fork.git"
    assert not any("set-url" in c for c in runner.calls)


def test_server_without_the_old_service_skips_the_rename(tmp_path, upgrade):
    assert _step(upgrade, "rename-annotation-server").done(_ctx(tmp_path, FakeRunner()))


def test_preflight_refuses_when_both_names_exist(tmp_path, upgrade):
    _old_layout(tmp_path)
    (tmp_path / "mounts/annotation-server").mkdir()
    runner = FakeRunner()
    with pytest.raises(UpgradeError, match="both"):
        _step(upgrade, "check-annotation-server-rename").run(_ctx(tmp_path, runner))
    assert runner.calls == []  # preflight only looks


def test_preflight_passes_on_the_old_layout(tmp_path, upgrade):
    _old_layout(tmp_path)
    runner = FakeRunner()
    _step(upgrade, "check-annotation-server-rename").run(_ctx(tmp_path, runner))
    assert runner.calls == []
