import types

import vispctl.build as build_mod


def test_cmd_build_delegates_to_buildmanager(monkeypatch):
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

    runner = type("FakeRunner", (), {})()
    monkeypatch.setattr(build_mod, "BuildManager", FakeBM)
    monkeypatch.setattr(build_mod, "Runner", lambda: runner)

    all_buildable = list(build_mod.BUILD_CONFIGS.keys()) + list(build_mod.NODE_BUILD_CONFIGS.keys())

    # Test node target (single service)
    args = types.SimpleNamespace(
        services=["container-agent"], list=False, no_cache=False, pull=False, config=None, force=True
    )
    build_mod.cmd_build(
        args,
        runner=runner,
        build_configs=build_mod.BUILD_CONFIGS,
        node_configs=build_mod.NODE_BUILD_CONFIGS,
        all_buildable=all_buildable,
    )
    assert called.get("node")[0] == "container-agent"

    # Test image build path (single service)
    args2 = types.SimpleNamespace(services=["apache"], list=False, no_cache=False, pull=False, config=None, force=True)
    build_mod.cmd_build(
        args2,
        runner=runner,
        build_configs=build_mod.BUILD_CONFIGS,
        node_configs=build_mod.NODE_BUILD_CONFIGS,
        all_buildable=all_buildable,
    )
    assert "apache" in called.get("images", [])
