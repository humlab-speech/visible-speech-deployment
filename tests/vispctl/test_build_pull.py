"""Phase 2 build side: the ``--pull`` policy token and the post-build base lock.

Hermetic by construction: the fake runner records the ``podman build`` command
and answers only the two read commands the record path needs (``image inspect``
and ``images --format json``), and ``get_config`` is pointed at a ``tmp_path``
project tree so nothing is written into the repo. No podman, no network.
Idioms follow test_build.py / test_base_freshness.py.
"""

from __future__ import annotations

import json
import types

import pytest

import vispctl.build as build_mod
from vispctl import base_freshness as bf

BASE = "python:3.12-slim"
BASE_DIGEST = "sha256:191c9f00e2a3"
BUILT_ID = "sha256:b01dbaked"
CFG = {"context": "./docker/fake", "dockerfile": "Dockerfile", "image": "visp-fake"}


class _Runner:
    """Records the build command; answers the read commands the base-lock path needs."""

    def __init__(self, build_rc: int = 0):
        self.cmds: list[list[str]] = []
        self.build_rc = build_rc

    def run(self, cmd, check=True):  # noqa: ARG002 — the real Runner takes check
        self.cmds.append(list(cmd))
        return types.SimpleNamespace(returncode=self.build_rc)

    def run_quiet(self, cmd, **kwargs):  # noqa: ARG002
        cmd = list(cmd)
        if cmd[:3] == ["podman", "image", "inspect"]:
            return 0, BUILT_ID, ""
        if cmd[:3] == ["podman", "images", "--format"]:
            images = [
                {
                    "Id": "baseid1234567890",
                    "Names": [f"docker.io/library/{BASE}"],
                    "RepoDigests": [f"docker.io/library/python@{BASE_DIGEST}"],
                }
            ]
            return 0, json.dumps(images), ""
        return 1, "", ""


def _project(tmp_path, monkeypatch, checks: dict | None = None) -> None:
    """A one-stage Dockerfile, a tmp project dir as get_config().project_dir, optional cached checks."""
    dockerfile = tmp_path / "docker" / "fake" / "Dockerfile"
    dockerfile.parent.mkdir(parents=True, exist_ok=True)
    dockerfile.write_text(f"FROM {BASE}\nRUN true\n")
    if checks:
        lock = tmp_path / ".visp" / "base-lock.json"
        lock.parent.mkdir(parents=True, exist_ok=True)
        lock.write_text(json.dumps({"checks": checks}))
    monkeypatch.setattr(build_mod, "get_config", lambda: types.SimpleNamespace(project_dir=tmp_path))


def _cmd_built(runner: _Runner) -> list[str]:
    builds = [cmd for cmd in runner.cmds if cmd[:2] == ["podman", "build"]]
    assert builds, f"no podman build in {runner.cmds}"
    return builds[0]


def _run_build(tmp_path, monkeypatch, runner: _Runner, **args_kw) -> list[str]:
    _project(tmp_path, monkeypatch)
    args = types.SimpleNamespace(
        services=["fake"], list=False, no_cache=False, pull=False, offline=False, config=None, force=True
    )
    for key, value in args_kw.items():
        setattr(args, key, value)
    build_mod.cmd_build(args, runner=runner, build_configs={"fake": CFG}, node_configs={}, all_buildable=["fake"])
    return _cmd_built(runner)


@pytest.mark.parametrize(
    ("args_kw", "policy"),
    [
        ({}, "newer"),  # the new default: rebuilds actually refresh a moved tag
        ({"pull": True}, "always"),
        ({"offline": True}, "never"),
        ({"pull": True, "offline": True}, "never"),  # air-gapped wins over --pull
    ],
)
def test_cmd_build_passes_one_pull_policy_token(tmp_path, monkeypatch, args_kw, policy):
    cmd = _run_build(tmp_path, monkeypatch, _Runner(), **args_kw)
    assert f"--pull={policy}" in cmd
    assert "--pull" not in cmd  # one token, never the bare flag podman reads as "always"


def test_policy_is_stated_before_the_build(tmp_path, monkeypatch, capsys):
    _run_build(tmp_path, monkeypatch, _Runner())
    assert "--pull=newer" in capsys.readouterr().out


def test_successful_build_records_the_base_lock(tmp_path, monkeypatch, capsys):
    """The lock is what turns later base verdicts from heuristic into exact."""
    _project(
        tmp_path, monkeypatch, checks={BASE: {"remote_digest": BASE_DIGEST, "checked_at": "2026-10-22T06:31:00+00:00"}}
    )
    runner = _Runner()

    build_mod.cmd_build(
        types.SimpleNamespace(
            services=["fake"], list=False, no_cache=False, pull=False, offline=False, config=None, force=True
        ),
        runner=runner,
        build_configs={"fake": CFG},
        node_configs={},
        all_buildable=["fake"],
    )

    lock = json.loads((tmp_path / ".visp" / "base-lock.json").read_text())
    record = lock["images"][BUILT_ID]
    assert record["pull_policy"] == "newer"
    assert record["built_at"].startswith("20")
    base = record["bases"][BASE]
    assert base["digests"] == [BASE_DIGEST]
    assert base["stage"] == "runtime"
    assert base["id"] == "baseid123456"  # local base image, short id
    assert base["remote_digest"] == BASE_DIGEST  # from the checks cache, when one exists
    assert lock["checks"] == {BASE: {"remote_digest": BASE_DIGEST, "checked_at": "2026-10-22T06:31:00+00:00"}}
    assert f"Base: {BASE} {BASE_DIGEST.removeprefix('sha256:')}" in capsys.readouterr().out


def test_failed_build_records_nothing(tmp_path, monkeypatch):
    _project(tmp_path, monkeypatch)
    runner = _Runner(build_rc=1)
    bm = build_mod.BuildManager(runner, build_configs={"fake": CFG}, node_configs={})

    assert bm.build_image("fake", CFG, pull_policy="newer") is False
    assert not (tmp_path / ".visp").exists()


def test_uninspectable_image_records_nothing_but_still_succeeds(tmp_path, monkeypatch):
    """No image id means no key to record under: verdicts stay heuristic, the build stands."""
    _project(tmp_path, monkeypatch)
    runner = _Runner()
    runner.run_quiet = lambda cmd, **kwargs: (1, "", "")  # noqa: ARG005 — inspect says "no such image"
    bm = build_mod.BuildManager(runner, build_configs={"fake": CFG}, node_configs={})

    assert bm.build_image("fake", CFG, pull_policy="never") is True
    assert not (tmp_path / ".visp").exists()


def test_lock_write_failure_degrades_to_a_warning(tmp_path, monkeypatch, capsys):
    def _read_only(*args, **kwargs):  # noqa: ARG001
        raise OSError("read-only filesystem")

    _project(tmp_path, monkeypatch)
    monkeypatch.setattr(bf, "save_lock", _read_only)
    bm = build_mod.BuildManager(_Runner(), build_configs={"fake": CFG}, node_configs={})

    assert bm.build_image("fake", CFG, pull_policy="newer") is True
    assert "base lock not recorded" in capsys.readouterr().out
    assert not (tmp_path / ".visp" / "base-lock.json").exists()
