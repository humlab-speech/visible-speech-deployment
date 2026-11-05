"""Tests for vispctl.mongo_upgrade.hop — the throwaway-container FCV hop."""

import subprocess
from pathlib import Path

import pytest

from vispctl import mongo_upgrade
from vispctl.release import InstallState, Manifest
from vispctl.upgrade import UpgradeContext, UpgradeError


class FakeMongo:
    """Answers the podman commands hop() sends, for a data directory at *fcv*."""

    def __init__(self, fcv="6.0", starts=True, set_ok=True):
        self.fcv, self.starts, self.set_ok = fcv, starts, set_ok
        self.calls: list[list[str]] = []

    def unit_is_active(self, unit):
        return False

    def run(self, cmd, capture=False, check=True, **kwargs):
        self.calls.append(cmd)
        out = ""
        js = cmd[-1] if "--eval" in cmd else ""
        if cmd[1] == "inspect":
            out = "true" if self.starts else "false"
        elif "ping" in js:
            out = "1" if self.starts else ""
        elif "setFeatureCompatibilityVersion" in js:
            if self.set_ok:
                self.fcv = js.split("'")[1]
            out = "1" if self.set_ok else "0"
        elif "featureCompatibilityVersion" in js:
            out = self.fcv
        return subprocess.CompletedProcess(cmd, 0, stdout=out + "\n", stderr="")


def _ctx(tmp_path, runner):
    state = InstallState.load(tmp_path)
    ctx = UpgradeContext(tmp_path, runner, "prod", state, Manifest(tmp_path / "r.json", {"visp": "1.1.0"}), "1.0.0")
    ctx.release = "1.1.0"
    return ctx


@pytest.fixture(autouse=True)
def fast(monkeypatch):
    monkeypatch.setattr(mongo_upgrade.time, "sleep", lambda s: None)


def test_hop_raises_fcv_records_it_and_cleans_up(tmp_path):
    runner = FakeMongo(fcv="6.0")
    ctx = _ctx(tmp_path, runner)

    assert mongo_upgrade.hop(ctx, "docker.io/library/mongo:7.0.45", Path("/data")) == "7.0"

    sets = [c[-1] for c in runner.calls if "setFeatureCompatibilityVersion" in c[-1]]
    assert sets == ["db.adminCommand({setFeatureCompatibilityVersion: '7.0', confirm: true}).ok"]
    run = next(c for c in runner.calls if c[1] == "run")
    assert "--network=none" in run and "docker.io/library/mongo:7.0.45" in run
    assert ctx.data["mongo_fcv"] == "7.0"
    assert InstallState.load(tmp_path).data["in_progress"]["data"]["1.1.0"]["mongo_fcv"] == "7.0"  # saved
    assert runner.calls[-1][:3] == ["podman", "rm", "-f"]  # throwaway container removed


def test_hop_leaves_a_higher_fcv_alone(tmp_path):
    runner = FakeMongo(fcv="7.0")
    assert mongo_upgrade.hop(_ctx(tmp_path, runner), "mongo:7.0.45", Path("/data")) == "7.0"
    assert not any("setFeatureCompatibilityVersion" in c[-1] for c in runner.calls)


def test_hop_fails_with_logs_when_mongod_does_not_start(tmp_path):
    runner = FakeMongo(starts=False)
    with pytest.raises(UpgradeError, match="did not come up"):
        mongo_upgrade.hop(_ctx(tmp_path, runner), "mongo:7.0.45", Path("/data"))
    assert runner.calls[-1][:3] == ["podman", "rm", "-f"]


def test_hop_fails_when_set_fcv_fails(tmp_path):
    with pytest.raises(UpgradeError, match="setFeatureCompatibilityVersion 7.0 failed"):
        mongo_upgrade.hop(_ctx(tmp_path, FakeMongo(set_ok=False)), "mongo:7.0.45", Path("/data"))


@pytest.mark.parametrize("version, expected", [("7.0.45", (7, 0)), ("6.0", (6, 0)), ("8", (8, 0))])
def test_major(version, expected):
    assert mongo_upgrade.major(version) == expected
