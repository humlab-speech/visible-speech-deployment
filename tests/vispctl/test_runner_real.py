"""Real-subprocess tests for vispctl.Runner — the one seam every other test fakes.

These deliberately execute real processes (/bin/true, /bin/false, a stub
systemctl placed first on PATH) so the subprocess kwargs are pinned by
observed behaviour instead of by a fake's opinion. Nothing here talks to a
real systemd manager or to podman.
"""

import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from vispctl.runner import Runner  # noqa: E402


@pytest.fixture()
def runner():
    return Runner()


def test_real_failure_raises_when_check_true(runner, tmp_path):
    script = tmp_path / "fail.sh"
    script.write_text("#!/bin/sh\nexit 3\n")
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    with pytest.raises(subprocess.CalledProcessError) as exc:
        runner.run([str(script)], capture=True, check=True)
    assert exc.value.returncode == 3


def test_real_rc_returned_when_check_false(runner, tmp_path):
    script = tmp_path / "fail.sh"
    script.write_text("#!/bin/sh\nexit 3\n")
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    res = runner.run([str(script)], capture=True, check=False)
    assert res.returncode == 3


def test_capture_returns_stdout_text(runner):
    res = runner.run(["/bin/echo", "seam-proof"], capture=True, check=False)
    assert res.stdout == "seam-proof\n"
    assert isinstance(res.stdout, str)


def test_input_kwarg_reaches_the_child(runner):
    res = runner.run(["/bin/cat"], capture=True, check=True, input="piped-in")
    assert res.stdout == "piped-in"


def test_run_quiet_maps_missing_binary_to_127(runner):
    rc, out, err = runner.run_quiet(["/definitely/not/here"])
    assert rc == 127
    assert out == ""
    assert err != ""


def _stub_systemctl(tmp_path, body: str) -> str:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    stub = bin_dir / "systemctl"
    stub.write_text("#!/bin/sh\n" + body)
    stub.chmod(stub.stat().st_mode | stat.S_IEXEC)
    return str(bin_dir)


def test_systemctl_always_targets_the_user_manager(runner, tmp_path, monkeypatch):
    argv_log = tmp_path / "argv.log"
    bin_dir = _stub_systemctl(tmp_path, f'printf "%s\\n" "$@" >> {argv_log}\necho ok\n')
    monkeypatch.setenv("PATH", bin_dir + os.pathsep + os.environ["PATH"])
    res = runner.systemctl("restart", "visp-demo.service")
    recorded = argv_log.read_text().splitlines()
    assert recorded[:2] == ["--user", "restart"]
    assert recorded[2] == "visp-demo.service"
    assert res.stdout.strip() == "ok"


@pytest.mark.parametrize(
    "state,expected", [("active", True), ("activating", True), ("inactive", False), ("failed", False)]
)
def test_unit_is_active_only_accepts_active_states(runner, tmp_path, monkeypatch, state, expected):
    bin_dir = _stub_systemctl(tmp_path, f"echo {state}\nexit {0 if state in ('active', 'activating') else 3}\n")
    monkeypatch.setenv("PATH", bin_dir + os.pathsep + os.environ["PATH"])
    assert runner.unit_is_active("anything") is expected


# ── TR-R2-02: argv pins that survived the class-stub era ─────────────────────


def _stub_tool(tmp_path, tool, body):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    stub = bin_dir / tool
    stub.write_text("#!/bin/sh\n" + body)
    stub.chmod(stub.stat().st_mode | stat.S_IEXEC)
    return str(bin_dir)


def test_unit_is_active_argv_is_exact(runner, tmp_path, monkeypatch):
    log = tmp_path / "argv.log"
    bin_dir = _stub_tool(tmp_path, "systemctl", f'printf "%s\\n" "$@" >> {log}\necho active\n')
    monkeypatch.setenv("PATH", bin_dir + os.pathsep + os.environ["PATH"])
    assert runner.unit_is_active("visp-demo") is True
    recorded = log.read_text().splitlines()
    # dropping '--user', 'is-active', or appending '.service' all must redden
    assert recorded == ["--user", "is-active", "visp-demo.service"]


def test_journalctl_executes_and_returns_completedprocess(runner, tmp_path, monkeypatch):
    log = tmp_path / "j.log"
    bin_dir = _stub_tool(tmp_path, "journalctl", f'printf "%s\\n" "$@" >> {log}\necho line\n')
    monkeypatch.setenv("PATH", bin_dir + os.pathsep + os.environ["PATH"])
    res = runner.journalctl("-u", "visp-demo.service", "-n", "5")
    assert isinstance(res, subprocess.CompletedProcess)
    assert res.returncode == 0
    assert (
        log.read_text().splitlines()[:6]
        == ["--user", "-u", "visp-demo.service", "-n", "5"][: len(log.read_text().splitlines())]
    )


def test_systemctl_default_check_is_false(runner, tmp_path, monkeypatch):
    # rc!=0 must NOT raise by default — callers read .returncode themselves.
    bin_dir = _stub_tool(tmp_path, "systemctl", "exit 4\n")
    monkeypatch.setenv("PATH", bin_dir + os.pathsep + os.environ["PATH"])
    res = runner.systemctl("is-enabled", "ghost.service")
    assert res.returncode == 4
