import types
import importlib.util
from pathlib import Path


def load_visp_module():
    spec = importlib.util.spec_from_file_location(
        "vp", str(Path.cwd() / "visp-podman.py")
    )
    vp = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(vp)
    return vp


def test_cmd_build_delegates_to_buildmanager(monkeypatch):
    vp = load_visp_module()
    called = {}

    class FakeBM:
        def __init__(self, runner, build_configs=None, node_configs=None):
            called["init"] = True

        def build_node_project(self, name, config, no_cache, build_config):
            called["node"] = (name, build_config)
            return True

        def build_image(self, svc_name, config, no_cache=False, pull=False):
            called.setdefault("images", []).append(svc_name)
            return True

        def prepare_build_context(self, svc_name, config):
            called.setdefault("prepared", []).append(svc_name)
            return True

    # Monkeypatch BuildManager in the loaded module
    monkeypatch.setattr(vp, "BuildManager", FakeBM)

    # Test node target
    args = types.SimpleNamespace(
        service="container-agent", list=False, no_cache=False, pull=False, config=None
    )
    vp.cmd_build(args)
    assert called.get("node")[0] == "container-agent"

    # Test image build path
    args2 = types.SimpleNamespace(
        service="apache", list=False, no_cache=False, pull=False, config=None
    )
    vp.cmd_build(args2)
    assert "apache" in called.get("images", [])
