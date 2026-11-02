"""podman unshare interprets ids INSIDE the namespace — pin the exact argv."""

import os
import sys
import types
from pathlib import Path

proj = str(Path(__file__).resolve().parents[2])
if proj not in sys.path:
    sys.path.insert(0, proj)

from vispctl.permissions import PermissionsManager


class Rec:
    def __init__(self, rc=0):
        self.rc = rc
        self.cmds = []

    def run(self, cmd, check=True):
        self.cmds.append(list(cmd))
        return types.SimpleNamespace(returncode=self.rc, stdout="", stderr="")


def test_default_target_is_namespace_root_not_host_uid(tmp_path, monkeypatch):
    monkeypatch.setattr(os, "getuid", lambda: 4242)
    monkeypatch.setattr(os, "getgid", lambda: 4343)
    mgr = PermissionsManager(Rec(), project_dir=tmp_path)
    cmds = mgr.plan_fix([tmp_path / "mounts"])
    joined = " ".join(cmds)
    # host ids must never enter namespace coordinates (old bug: off by subuid base)
    assert "4242" not in joined and "4343" not in joined
    assert "podman unshare chown 0:0 " in joined


def test_explicit_ids_pass_through_as_namespace_coords(tmp_path):
    mgr = PermissionsManager(Rec(), project_dir=tmp_path)
    cmds = mgr.plan_fix([tmp_path / "m"], uid=1000, gid=100)
    assert any("chown 1000:100 " in c for c in cmds)


def test_chmod_grants_traverse_not_world_execute(tmp_path):
    mgr = PermissionsManager(Rec(), project_dir=tmp_path)
    cmds = mgr.plan_fix([tmp_path / "m"])
    assert any("chmod a+rX" in c for c in cmds)
    assert not any("a+rx" in c for c in cmds)  # world-execute on data files is a leak


def test_path_argument_is_present_in_every_command(tmp_path):
    mgr = PermissionsManager(Rec(), project_dir=tmp_path)
    target = tmp_path / "mounts"
    for cmd in mgr.plan_fix([target]):
        assert str(target) in cmd.split()


def test_apply_surfaces_failures_without_bailing(tmp_path):
    rec = Rec(rc=1)
    mgr = PermissionsManager(rec, project_dir=tmp_path)
    ok = mgr.apply_fix([tmp_path / "m"], recursive=True)
    assert ok is False
    tools = [c[2] for c in rec.cmds if len(c) > 2]
    assert tools == ["chown", "chmod"]  # chmod still attempted after chown failure


def test_recursive_flag_reaches_the_tools(tmp_path):
    rec = Rec()
    mgr = PermissionsManager(rec, project_dir=tmp_path)
    mgr.apply_fix([tmp_path / "m"], recursive=True)
    assert all("-R" in c for c in rec.cmds)
