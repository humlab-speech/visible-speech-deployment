"""Existing networks must match their spec, not merely exist (round-2 HIGH)."""

import sys
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # noqa: E402

from vispctl.network import NetworkManager


class Fake:
    def __init__(self, ls_names, inspect_out="bridge|false", inspect_rc=0):
        self.ls = "\n".join(ls_names)
        self.inspect_out = inspect_out
        self.inspect_rc = inspect_rc

    def run_quiet(self, cmd):
        if cmd[:3] == ["podman", "network", "ls"]:
            return 0, self.ls, ""
        if cmd[:3] == ["podman", "network", "inspect"]:
            if self.inspect_rc:
                return self.inspect_rc, "", "boom"
            name = cmd[3]
            if name == "systemd-tratt-net" or name == "systemd-artic-net":
                return 0, self.inspect_out, ""
            return 0, "bridge|false", ""
        raise AssertionError(cmd)

    def run(self, cmd, check=True):
        return types.SimpleNamespace(returncode=0)


def _mgr(fake):
    return NetworkManager(fake, containers_conf=Path("/tmp/cc.conf"))


def test_non_internal_tratt_net_is_refused():
    fake = Fake(["systemd-visp-net", "systemd-tratt-net", "systemd-artic-net"], inspect_out="bridge|false")
    assert _mgr(fake).ensure_networks_exist() is False


def test_internal_tratt_net_passes():
    fake = Fake(["systemd-visp-net", "systemd-tratt-net", "systemd-artic-net"], inspect_out="bridge|true")
    assert _mgr(fake).ensure_networks_exist() is True


def test_uninspectable_network_is_refused():
    fake = Fake(["systemd-visp-net", "systemd-tratt-net", "systemd-artic-net"], inspect_rc=1)
    assert _mgr(fake).ensure_networks_exist() is False
