"""Tests for vispctl/cleanup_containers.py."""

import builtins

from vispctl.cleanup_containers import cleanup_containers


class FakeRunner:
    def __init__(self, ps_output=""):
        self.ps_output = ps_output
        self.calls = []

    def run(self, cmd, **kwargs):
        self.calls.append(cmd)

        class R:
            returncode = 0
            stdout = ""
            stderr = ""

        r = R()
        # The discovery call carries the status=exited filter; the per-container
        # 'is it running?' check does not, and gets an empty answer (stopped).
        if cmd[:2] == ["podman", "ps"] and "status=exited" in cmd:
            r.stdout = self.ps_output
        return r


def _fake_runner(monkeypatch, ps_output):
    fr = FakeRunner(ps_output)
    monkeypatch.setattr("vispctl.cleanup_containers.Runner", lambda: fr)
    return fr


def test_no_containers(monkeypatch):
    _fake_runner(monkeypatch, "")
    result = cleanup_containers(mode="stopped", yes=True)
    assert result["status"] == "ok"
    assert result["removed"] == 0


def test_prompt_lists_containers_and_cancels_on_no(monkeypatch, capsys):
    _fake_runner(monkeypatch, "abc123def456 visp-session-foo\n")
    monkeypatch.setattr(builtins, "input", lambda prompt: "n")

    result = cleanup_containers(mode="stopped")

    out = capsys.readouterr().out
    assert "visp-session-foo" in out
    assert "abc123def4" in out
    assert result["status"] == "cancelled"
    assert result["removed"] == 0


def test_eof_cancels_cleanly(monkeypatch, capsys):
    _fake_runner(monkeypatch, "abc123def456 visp-session-foo\n")

    def eof_input(prompt):
        raise EOFError

    monkeypatch.setattr(builtins, "input", eof_input)

    result = cleanup_containers(mode="stopped")

    out = capsys.readouterr().out
    assert "visp-session-foo" in out
    assert result["status"] == "cancelled"
    assert "non-interactive" in result["message"]


def test_yes_removes_containers(monkeypatch):
    fr = _fake_runner(monkeypatch, "abc123def456 visp-session-foo\n")

    result = cleanup_containers(mode="stopped", yes=True)

    assert result["status"] == "ok"
    assert result["removed"] == 1
    assert ["podman", "rm", "abc123def456"] in fr.calls
    assert not any(c[:2] == ["podman", "stop"] for c in fr.calls)
