"""Tests for vispctl.upgrade — plan order, resume after failure, manual steps, MIN_FROM."""

import json
import textwrap
from pathlib import Path

import pytest

from vispctl import upgrade as up
from vispctl.exceptions import ConfigError
from vispctl.release import InstallState
from vispctl.upgrade import UpgradeError, build_plan, discover_upgrades, run_upgrade

CALLS: list[str] = []

_BUILTINS = (
    "_check_repos",
    "_check_network_backend",
    "_check_networks",
    "_check_webclient_config",
    "_backup",
    "_checkout",
    "_build",
    "_pull",
    "_converge",
)
# The real steps, kept before the autouse fixture swaps them for recorders.
REAL = {name: getattr(up, name) for name in _BUILTINS}


@pytest.fixture(autouse=True)
def fake_builtins(monkeypatch):
    """Replace the steps that touch the real system with recorders."""
    import builtins

    CALLS.clear()
    monkeypatch.setattr(builtins, "VISP_TEST_CALLS", CALLS, raising=False)
    for name in _BUILTINS:
        monkeypatch.setattr(up, name, lambda ctx, n=name: CALLS.append(n.lstrip("_")))
    yield


def _project(tmp_path: Path, target: str, installed: str | None, modules: dict[str, str]) -> Path:
    tmp_path.mkdir(parents=True, exist_ok=True)
    (tmp_path / "release.json").write_text(json.dumps({"visp": target, "components": {}}))
    (tmp_path / "upgrades").mkdir()
    for version, body in modules.items():
        (tmp_path / "upgrades" / f"{version}.py").write_text(body)
    if installed:
        InstallState.load(tmp_path).record(installed, "install")
    return tmp_path


RECORDER = """\
import builtins

from vispctl.upgrade import Step

CALLS = builtins.VISP_TEST_CALLS


def rec(name):
    return lambda ctx: CALLS.append(name)
"""


def _module(version: str, steps: str, extra: str = "") -> str:
    doc = f'"""VISP {version} — test."""\n'
    return doc + RECORDER + textwrap.dedent(extra) + "\nSTEPS = [\n" + textwrap.dedent(steps) + "]\n"


def test_plan_interleaves_releases_per_phase(tmp_path):
    project = _project(
        tmp_path,
        "1.2.0",
        "1.0.0",
        {
            "1.1.0": _module(
                "1.1.0",
                """
                Step("a-pre", "x", run=rec("1.1-pre")),
                Step("a-check", "x", run=rec("1.1-check"), phase="preflight"),
                Step("a-post", "x", run=rec("1.1-post"), phase="post_apply"),
                """,
            ),
            "1.2.0": _module(
                "1.2.0",
                """
                Step("b-built", "x", run=rec("1.2-built"), phase="post_build"),
                Step("b-pre", "x", run=rec("1.2-pre")),
                Step("b-check", "x", run=rec("1.2-check"), phase="preflight"),
                """,
            ),
            "1.0.0": _module("1.0.0", 'Step("old", "x", run=rec("never")),'),
            "1.3.0": _module("1.3.0", 'Step("new", "x", run=rec("never")),'),
        },
    )

    assert run_upgrade(project, runner=None, mode="prod", assume_yes=True) == 0
    assert CALLS == [
        "check_repos", "check_network_backend", "check_networks", "check_webclient_config",
        "1.1-check", "1.2-check",
        "backup", "1.1-pre", "1.2-pre",
        "checkout", "build", "pull",
        "1.2-built",
        "converge",
        "1.1-post",
    ]  # fmt: skip
    state = InstallState.load(project)
    assert state.version == "1.2.0" and state.in_progress is None


def test_failed_step_resumes_after_last_completed(tmp_path, monkeypatch):
    marker = tmp_path / "fail"
    marker.write_text("")
    module = _module(
        "1.1.0",
        """
        Step("one", "x", run=rec("one")),
        Step("two", "x", run=two),
        Step("three", "x", run=rec("three"), phase="post_apply"),
        """,
        extra=f"""
        from pathlib import Path

        def two(ctx):
            CALLS.append("two")
            ctx.data["seen"] = ctx.data.get("seen", 0) + 1
            if Path({str(marker)!r}).exists():
                ctx.fail("disk full")
        """,
    )
    project = _project(tmp_path / "p", "1.1.0", "1.0.0", {"1.1.0": module})

    assert run_upgrade(project, None, "prod", assume_yes=True) == 1
    assert CALLS[-2:] == ["one", "two"]
    state = InstallState.load(project)
    assert state.version == "1.0.0"
    assert "1.1.0:one" in state.in_progress["done"] and "1.1.0:two" not in state.in_progress["done"]

    marker.unlink()
    CALLS.clear()
    assert run_upgrade(project, None, "prod", assume_yes=True) == 0
    assert CALLS == ["two", "checkout", "build", "pull", "converge", "three"]
    state = InstallState.load(project)
    assert state.version == "1.1.0"


def test_step_data_survives_resume(tmp_path):
    module = _module(
        "1.1.0",
        """
        Step("remember", "x", run=remember),
        Step("use", "x", run=use, phase="post_apply"),
        """,
        extra="""
        def remember(ctx):
            ctx.data["path"] = "/backups/x"

        def use(ctx):
            CALLS.append(ctx.data["path"])
            ctx.fail("stop") if not ctx.data.get("second") else None

        """,
    )
    project = _project(tmp_path, "1.1.0", "1.0.0", {"1.1.0": module})
    assert run_upgrade(project, None, "prod", assume_yes=True) == 1
    state = InstallState.load(project)
    state.data["in_progress"]["data"]["1.1.0"]["second"] = True
    state.save()
    CALLS.clear()
    assert run_upgrade(project, None, "prod", assume_yes=True) == 0
    assert CALLS == ["/backups/x"]


def test_done_check_skips_and_manual_step_waits_for_operator(tmp_path):
    module = _module(
        "1.1.0",
        """
        Step("skip-me", "x", run=rec("ran"), done=lambda ctx: True),
        Step("by-hand", "x", manual="Rotate the SWAMID certificate."),
        """,
    )
    project = _project(tmp_path, "1.1.0", "1.0.0", {"1.1.0": module})
    answers = iter(["", "not yet", "done"])
    rc = run_upgrade(project, None, "prod", assume_yes=True, input_fn=lambda _: next(answers))
    assert rc == 0
    assert "ran" not in CALLS
    assert next(answers, None) is None  # every answer was consumed


def test_manual_step_abort_stops_upgrade(tmp_path):
    module = _module("1.1.0", 'Step("by-hand", "x", manual="Do it."),')
    project = _project(tmp_path, "1.1.0", "1.0.0", {"1.1.0": module})
    assert run_upgrade(project, None, "prod", assume_yes=True, input_fn=lambda _: "abort") == 1
    assert InstallState.load(project).version == "1.0.0"


def test_mode_filter(tmp_path):
    module = _module(
        "1.1.0",
        """
        Step("dev-only", "x", run=rec("dev"), modes=("dev",)),
        Step("both", "x", run=rec("both")),
        """,
    )
    project = _project(tmp_path, "1.1.0", "1.0.0", {"1.1.0": module})
    assert run_upgrade(project, None, "prod", assume_yes=True) == 0
    assert "both" in CALLS and "dev" not in CALLS


def test_min_from_requires_two_hops(tmp_path):
    module = _module("1.2.0", "", extra='MIN_FROM = "1.1.0"')
    project = _project(tmp_path, "1.2.0", "1.0.0", {"1.2.0": module})
    upgrades = discover_upgrades(project)
    from vispctl.release import Manifest

    with pytest.raises(UpgradeError, match="two hops"):
        build_plan("1.0.0", Manifest.load(project), upgrades, "prod")
    build_plan("1.1.0", Manifest.load(project), upgrades, "prod")  # fine from 1.1.0


def test_dry_run_runs_preflight_only_and_changes_nothing(tmp_path):
    module = _module(
        "1.1.0",
        """
        Step("look", "x", run=rec("look"), phase="preflight"),
        Step("change", "x", run=rec("change")),
        """,
    )
    project = _project(tmp_path, "1.1.0", "1.0.0", {"1.1.0": module})
    before = (project / "mounts" / "visp-state.json").read_text()
    assert run_upgrade(project, None, "prod", dry_run=True) == 0
    assert CALLS == ["check_repos", "check_network_backend", "check_networks", "check_webclient_config", "look"]
    assert (project / "mounts" / "visp-state.json").read_text() == before


def test_declined_confirmation_changes_nothing(tmp_path):
    project = _project(tmp_path, "1.1.0", "1.0.0", {})
    assert run_upgrade(project, None, "prod", input_fn=lambda _: "n") == 1
    assert CALLS == []


def test_refuses_downgrade_and_unrecorded_server(tmp_path):
    assert run_upgrade(_project(tmp_path / "a", "1.0.0", "1.1.0", {}), None, "prod", assume_yes=True) == 1
    assert run_upgrade(_project(tmp_path / "b", "1.0.0", None, {}), None, "prod", assume_yes=True) == 1
    assert CALLS == []


def test_up_to_date_is_a_noop(tmp_path):
    assert run_upgrade(_project(tmp_path, "1.0.0", "1.0.0", {}), None, "prod", assume_yes=True) == 0
    assert CALLS == []


def test_no_backup_no_build_flags(tmp_path):
    project = _project(tmp_path, "1.1.0", "1.0.0", {})
    assert run_upgrade(project, None, "prod", assume_yes=True, backup=False, build=False) == 0
    assert CALLS == ["check_repos", "check_network_backend", "check_networks", "checkout", "pull", "converge"]


@pytest.mark.parametrize(
    "steps, message",
    [
        ('Step("x", "x"),', "exactly one of run= and manual="),
        ('Step("x", "x", run=rec("a"), phase="later"),', "unknown phase"),
        ('Step("x", "x", run=rec("a")), Step("x", "y", run=rec("b")),', "duplicate step id"),
        ('Step("a:b", "x", run=rec("a")),', "contain no ':'"),
    ],
)
def test_invalid_modules_are_rejected(tmp_path, steps, message):
    project = _project(tmp_path, "1.1.0", "1.0.0", {"1.1.0": _module("1.1.0", steps)})
    with pytest.raises(ConfigError, match=message):
        discover_upgrades(project)


# ── built-in checks against a fake podman ─────────────────────────────────────────


class _Podman:
    """Answers the podman queries the network checks make."""

    def __init__(self, networks=None, ls_rc=0, backend="netavark"):
        self.networks = networks or {}  # name -> (driver, internal)
        self.ls_rc = ls_rc
        self.backend = backend

    def run_quiet(self, cmd, **kwargs):
        if cmd[:3] == ["podman", "network", "ls"]:
            return self.ls_rc, "\n".join(self.networks), ""
        if cmd[:3] == ["podman", "network", "inspect"]:
            driver, internal = self.networks[cmd[3]]
            return 0, f"{driver}|{str(internal).lower()}", ""
        if cmd[:2] == ["podman", "info"]:
            return (0, self.backend, "") if self.backend else (125, "", "boom")
        raise AssertionError(f"unexpected command {cmd}")


def _ctx(runner, tmp_path=None):
    from types import SimpleNamespace

    def fail(msg):
        raise UpgradeError(msg)

    return SimpleNamespace(runner=runner, project_dir=tmp_path, info=lambda msg: None, fail=fail)


_GOOD = {
    "systemd-visp-net": ("bridge", False),
    "systemd-tratt-net": ("bridge", True),
    "systemd-artic-net": ("bridge", True),
}


@pytest.mark.parametrize("networks", [_GOOD, {}, {"systemd-visp-net": ("bridge", False)}])
def test_check_networks_passes_when_networks_match_or_are_missing(networks):
    REAL["_check_networks"](_ctx(_Podman(networks)))  # missing ones are install's to create


def test_check_networks_names_the_mismatch_and_the_fix():
    networks = dict(_GOOD, **{"systemd-tratt-net": ("bridge", False), "systemd-artic-net": ("macvlan", True)})
    with pytest.raises(UpgradeError) as err:
        REAL["_check_networks"](_ctx(_Podman(networks)))
    msg = str(err.value)
    assert "systemd-tratt-net: exists but is not internal" in msg
    assert "systemd-artic-net: exists with driver macvlan" in msg
    assert "podman network rm systemd-tratt-net systemd-artic-net" in msg
    assert "./visp.py network ensure" in msg


def test_check_networks_fails_when_podman_cannot_list_them():
    with pytest.raises(UpgradeError, match="cannot list podman networks"):
        REAL["_check_networks"](_ctx(_Podman(ls_rc=125)))


def test_check_network_backend():
    REAL["_check_network_backend"](_ctx(_Podman(backend="netavark")))
    with pytest.raises(UpgradeError, match="podman system reset"):
        REAL["_check_network_backend"](_ctx(_Podman(backend="cni")))
    with pytest.raises(UpgradeError, match="Cannot tell"):
        REAL["_check_network_backend"](_ctx(_Podman(backend=None)))


def test_build_refreshes_base_images_before_building(tmp_path, monkeypatch):
    import vispctl.build as build_mod

    order = []

    class FakeBM:
        def __init__(self, runner, build_configs=None, node_configs=None):
            pass

        def run_builds(self, ordered, **kwargs):
            order.append(("build", list(ordered)))
            return {"success": list(ordered), "failed": [], "skipped": []}

    def refresh(runner, build_configs, ordered, **kwargs):
        order.append(("refresh", list(ordered)))

    monkeypatch.setattr(build_mod, "BuildManager", FakeBM)
    monkeypatch.setattr(build_mod, "refresh_base_images", refresh)
    REAL["_build"](_ctx(runner=None, tmp_path=tmp_path))
    assert [step for step, _ in order] == ["refresh", "build"]
    assert order[0][1] == order[1][1]  # the bases of exactly what is built
