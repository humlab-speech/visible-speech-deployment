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


@pytest.fixture(autouse=True)
def fake_builtins(monkeypatch):
    """Replace the steps that touch the real system with recorders."""
    import builtins

    CALLS.clear()
    monkeypatch.setattr(builtins, "VISP_TEST_CALLS", CALLS, raising=False)
    for name in ("_check_repos", "_check_webclient_config", "_backup", "_checkout", "_build", "_pull", "_converge"):
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
        "check_repos", "check_webclient_config", "1.1-check", "1.2-check",
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
    assert CALLS == ["check_repos", "check_webclient_config", "look"]
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
    assert CALLS == ["check_repos", "checkout", "pull", "converge"]


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
