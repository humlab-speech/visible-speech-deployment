"""A failed build must not report success (see fix: build exit code).

Pins: cmd_build returns 1 when any image failed (0 otherwise), and the visp.py
entry point turns that into a non-zero process exit so 'visp.py build && ...'
chains stop on a half-built system.
"""

import types

import visp
import vispctl.build as build_mod


class _FakeRunner:
    def run_quiet(self, cmd, **kwargs):  # no running containers -> nothing stale
        return 1, "", ""


def _fake_bm(result):
    class FakeBM:
        def __init__(self, runner, build_configs=None, node_configs=None):
            self.build_configs = build_configs or {}
            self.node_configs = node_configs or {}

        def check_version_drift(self, ordered, mode):
            return [], False

        def prepare_build_context(self, svc_name, config):
            return True

        def run_builds(self, ordered, no_cache=False, pull=False, build_config=None):
            return result

    return FakeBM


def _run_build(monkeypatch, result):
    runner = _FakeRunner()
    monkeypatch.setattr(build_mod, "BuildManager", _fake_bm(result))
    monkeypatch.setattr(build_mod, "Runner", lambda: runner)
    args = types.SimpleNamespace(
        services=["apache"], list=False, no_cache=False, pull=False, config=None, force=True, no_pull=True
    )
    return build_mod.cmd_build(
        args,
        runner=runner,
        build_configs={"apache": build_mod.BUILD_CONFIGS["apache"]},
        node_configs={},
        all_buildable=["apache"],
    )


def test_failed_image_returns_failure(monkeypatch):
    assert _run_build(monkeypatch, {"success": [], "failed": ["apache"], "skipped": []}) == 1


def test_successful_and_skipped_builds_return_zero(monkeypatch):
    assert _run_build(monkeypatch, {"success": ["apache"], "failed": [], "skipped": []}) == 0
    assert _run_build(monkeypatch, {"success": [], "failed": [], "skipped": ["apache"]}) == 0


def test_cli_exits_nonzero_when_builds_fail(monkeypatch):
    calls = []
    monkeypatch.setattr(visp, "_cmd_build", lambda *a, **k: calls.append(1) or 1)
    try:
        visp.cmd_build(types.SimpleNamespace())
        raise AssertionError("cmd_build must exit when the build manager reports failure")
    except SystemExit as exit_info:
        assert exit_info.code == 1
    assert calls == [1]


def test_cli_stays_green_when_builds_pass(monkeypatch):
    monkeypatch.setattr(visp, "_cmd_build", lambda *a, **k: 0)
    visp.cmd_build(types.SimpleNamespace())  # no SystemExit


def test_blocked_build_before_starting_exits_nonzero(monkeypatch):
    """A build refused before it starts is still a failed build for chains."""
    calls = []

    class BM:
        def __init__(self, runner, build_configs=None, node_configs=None):
            pass

        def check_version_drift(self, ordered, mode):
            return ("node version mismatch", True)  # blocking (PROD without --force)

        def prepare_build_context(self, *a, **k):
            calls.append("context")
            return True

        def run_builds(self, *a, **k):
            calls.append("builds")
            return {"success": [], "failed": [], "skipped": []}

    monkeypatch.setattr(build_mod, "BuildManager", BM)
    monkeypatch.setattr(build_mod, "Runner", lambda: _FakeRunner())
    args = types.SimpleNamespace(
        services=["apache"], list=False, no_cache=False, pull=False, config=None, force=False, no_pull=True
    )
    rc = build_mod.cmd_build(
        args,
        runner=_FakeRunner(),
        build_configs={"apache": build_mod.BUILD_CONFIGS["apache"]},
        node_configs={},
        all_buildable=["apache"],
    )
    assert rc == 1
    assert "builds" not in calls  # nothing was built; the exit code is the only signal
