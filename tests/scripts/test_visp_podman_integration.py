import importlib.util
import types
from pathlib import Path


def load_visp_module():
    spec = importlib.util.spec_from_file_location("vp", str(Path.cwd() / "visp.py"))
    vp = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(vp)
    return vp


def test_cmd_build_delegates_to_buildmanager(monkeypatch):
    vp = load_visp_module()
    called = {}

    class FakeBM:
        def __init__(self, runner, build_configs=None, node_configs=None):
            called["init"] = True
            self.build_configs = build_configs or {}
            self.node_configs = node_configs or {}

        def check_version_drift(self, ordered, mode):
            return [], False

        def run_builds(self, ordered, no_cache=False, pull=False, build_config=None):
            node_names = set(self.node_configs.keys())
            for svc_name in ordered:
                if svc_name in node_names:
                    cfg = self.node_configs[svc_name]
                    self.build_node_project(svc_name, cfg, no_cache, build_config)
                else:
                    cfg = self.build_configs[svc_name]
                    self.build_image(svc_name, cfg, no_cache=no_cache, pull=pull)
            return {"success": list(ordered), "failed": [], "skipped": []}

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

    # Test node target (single service)
    args = types.SimpleNamespace(
        services=["container-agent"], list=False, no_cache=False, pull=False, config=None, force=True
    )
    vp.cmd_build(args)
    assert called.get("node")[0] == "container-agent"

    # Test image build path (single service)
    args2 = types.SimpleNamespace(services=["apache"], list=False, no_cache=False, pull=False, config=None, force=True)
    vp.cmd_build(args2)
    assert "apache" in called.get("images", [])
