"""systemd 25x answers never-loaded units with 'not-found' — still a reload hint (F2)."""

import sys
import types
from pathlib import Path

proj = str(Path(__file__).resolve().parents[2])
if proj not in sys.path:
    sys.path.insert(0, proj)

from vispctl.service import Service
from vispctl.service_manager import ServiceManager


class Fake:
    def __init__(self, state, rc):
        self.state, self.rc = state, rc
        self.calls = []

    def systemctl(self, action, unit, check=False):
        self.calls.append(action)
        if action == "is-enabled":
            return types.SimpleNamespace(returncode=self.rc, stdout=self.state, stderr="")
        return types.SimpleNamespace(returncode=0, stdout="", stderr="")

    def run_quiet(self, cmd):
        return 0, "", ""


def _mgr(tmp_path, state, rc):
    svc = Service("demo", "container", "demo.container")
    (tmp_path / "demo.container").write_text("[Container]\n")
    sm = ServiceManager(Fake(state, rc), [svc])
    sm.systemd_dir = tmp_path
    sm._resolve_targets = lambda names, reverse=False: [svc]
    sm._reload_systemd = lambda: None
    return sm


def test_notfound_state_prints_reload_hint(tmp_path, capsys):
    fake = Fake("not-found", 4)
    sm = _mgr(tmp_path, "not-found", 4)
    sm.enable("demo")
    out = capsys.readouterr().out
    assert "Not loaded yet" in out
    assert "enable" not in fake.calls  # never tries a doomed systemctl enable


def test_empty_state_still_prints_hint(tmp_path, capsys):
    fake = Fake("", 1)
    sm = _mgr(tmp_path, "", 1)
    sm.enable("demo")
    assert "Not loaded yet" in capsys.readouterr().out
    assert "enable" not in fake.calls
