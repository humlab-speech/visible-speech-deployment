"""Tests for vispctl.image_update — './visp.py images update <service>'."""

import json
import subprocess
from argparse import Namespace
from types import SimpleNamespace

import pytest

from vispctl import image_update
from vispctl import images as images_mod
from vispctl.exceptions import ConfigError, ServiceError
from vispctl.image_update import (
    Choice,
    _warnings,
    build_choices,
    find_image_pins,
    prompt_for_tag,
    retag,
    run_image_update,
    set_image_pin,
)

MATOMO = "docker.io/library/matomo:5.8.0"


def _quadlets(root, dev_ref=MATOMO, prod_ref=MATOMO):
    for mode, ref in (("dev", dev_ref), ("prod", prod_ref)):
        d = root / "quadlets" / mode
        d.mkdir(parents=True, exist_ok=True)
        (d / "matomo.container").write_text(f"[Container]\nContainerName=matomo\nImage={ref}\nNetwork=visp-net.network\n")


# --- helpers ---


def test_find_image_pins_covers_both_modes(tmp_path):
    _quadlets(tmp_path)
    pins = find_image_pins(tmp_path, "matomo")
    assert [(p.mode, p.ref) for p in pins] == [("dev", MATOMO), ("prod", MATOMO)]
    assert find_image_pins(tmp_path, "nonexistent") == []


@pytest.mark.parametrize(
    "ref, expected",
    [
        (MATOMO, "docker.io/library/matomo:5.14.0"),
        ("docker.io/library/matomo:5.8.0@sha256:abc", "docker.io/library/matomo:5.14.0"),
        ("localhost:5000/team/matomo", "localhost:5000/team/matomo:5.14.0"),
    ],
)
def test_retag(ref, expected):
    assert retag(ref, "5.14.0") == expected


def test_set_image_pin_touches_only_the_image_line(tmp_path):
    _quadlets(tmp_path)
    pin = find_image_pins(tmp_path, "matomo")[1]
    set_image_pin(pin, "docker.io/library/matomo:5.14.0")
    assert pin.path.read_text() == (
        "[Container]\nContainerName=matomo\nImage=docker.io/library/matomo:5.14.0\nNetwork=visp-net.network\n"
    )


def test_build_choices_lists_each_newer_major():
    tags = ["6.0.27", "6.0.28", "7.0.45", "8.0.1", "8.3.13", "9.0.4", "9"]
    choices = build_choices("6.0.27", tags, rebuilt=False, pulled=True)
    assert [c.tag for c in choices] == ["6.0.28", "7.0.45", "8.3.13", "9.0.4"]
    assert "major" not in choices[0].label and "major upgrade" in choices[1].label


def test_build_choices_offers_current_tag_when_rebuilt():
    choices = build_choices("5.8.0", ["5.8.0"], rebuilt=True, pulled=True)
    assert [(c.tag, "rebuilt upstream" in c.label) for c in choices] == [("5.8.0", True)]
    assert build_choices("5.8.0", ["5.8.0"], rebuilt=False, pulled=True) == []


def _answers(*answers):
    it = iter(answers)

    def fn(prompt):
        try:
            return next(it)
        except StopIteration:
            raise EOFError from None

    return fn


def test_prompt_accepts_number_or_registry_tag():
    choices = [Choice("5.14.0", "newest 5.x")]
    tags = ["5.8.0", "5.9.0", "5.14.0"]
    assert prompt_for_tag(choices, tags, _answers("1")) == "5.14.0"
    assert prompt_for_tag(choices, tags, _answers("5.9.0")) == "5.9.0"
    assert prompt_for_tag(choices, tags, _answers("7", "nope", "5.9.0")) == "5.9.0"  # retries
    assert prompt_for_tag(choices, tags, _answers("")) is None
    with pytest.raises(ConfigError, match="--to"):
        prompt_for_tag(choices, tags, _answers())


def test_warnings():
    assert _warnings("5.8.0", "5.14.0") == []
    [major] = _warnings("6.0.27", "9.0.4")
    assert "major upgrade" in major and "skips 2 major" in major
    [older] = _warnings("5.14.0", "5.8.0")
    assert "OLDER" in older


# --- run_image_update ---


class _Runner:
    def __init__(self, images=(), pull_rc=0):
        self.images, self.pull_rc = list(images), pull_rc
        self.pulled = []

    def run_quiet(self, cmd):
        if cmd[:2] == ["podman", "images"]:
            return 0, json.dumps(self.images), ""
        return 0, "", ""

    def run(self, cmd, check=True, **kwargs):
        assert cmd[:2] == ["podman", "pull"]
        self.pulled.append(cmd[2])
        return subprocess.CompletedProcess(cmd, self.pull_rc)


class _Client:
    def __init__(self, tags, digest="sha256:same"):
        self.tags, self.digest = tags, digest

    def list_tags(self, ref):
        return self.tags

    def tag_digest(self, ref):
        return self.digest


LOCAL_MATOMO = {"Id": "aaa", "Names": [MATOMO], "RepoDigests": ["docker.io/library/matomo@sha256:same"]}


@pytest.fixture
def env(tmp_path, monkeypatch):
    _quadlets(tmp_path)
    monkeypatch.setattr(image_update, "get_current_mode", lambda: "prod")
    monkeypatch.setattr(images_mod, "get_current_mode", lambda: "prod")
    monkeypatch.setattr(images_mod, "get_config", lambda: SimpleNamespace(project_dir=tmp_path))
    return tmp_path


def _run(root, runner, client, answers=(), to=None, yes=False):
    calls = []
    rc = run_image_update(
        Namespace(service="matomo", to=to, yes=yes),
        SimpleNamespace(project_dir=root, runner=runner),
        client=client,
        input_fn=_answers(*answers),
        apply_fn=lambda svc, cfg: calls.append(("apply", svc)),
        restart_fn=lambda svc, cfg: calls.append(("restart", svc)),
    )
    return rc, calls


def _pins(root):
    return [p.ref for p in find_image_pins(root, "matomo")]


def test_update_pulls_then_rewrites_both_quadlets_then_applies(env, capsys):
    runner = _Runner([LOCAL_MATOMO])
    rc, calls = _run(env, runner, _Client(["5.8.0", "5.14.0"]), answers=["1", "y"])

    assert rc == 0
    assert runner.pulled == ["docker.io/library/matomo:5.14.0"]
    assert _pins(env) == ["docker.io/library/matomo:5.14.0"] * 2
    assert calls == [("apply", "matomo")]
    out = capsys.readouterr().out
    assert "core:update" in out  # Matomo's post-update step
    assert "git commit quadlets/dev/matomo.container quadlets/prod/matomo.container" in out


def test_failed_pull_changes_nothing(env):
    runner = _Runner([LOCAL_MATOMO], pull_rc=1)
    with pytest.raises(ServiceError, match="Nothing was changed"):
        _run(env, runner, _Client(["5.8.0", "5.14.0"]), to="5.14.0", yes=True)
    assert _pins(env) == [MATOMO, MATOMO]


def test_declining_confirmation_changes_nothing(env):
    runner = _Runner([LOCAL_MATOMO])
    rc, calls = _run(env, runner, _Client(["5.8.0", "5.14.0"]), answers=["1", "n"])
    assert rc == 1 and runner.pulled == [] and calls == []
    assert _pins(env) == [MATOMO, MATOMO]


def test_rebuilt_tag_is_pulled_and_restarted_without_quadlet_edit(env):
    runner = _Runner([LOCAL_MATOMO])
    rc, calls = _run(env, runner, _Client(["5.8.0"], digest="sha256:rebuilt"), answers=["1", "y"])
    assert rc == 0
    assert runner.pulled == [MATOMO]
    assert _pins(env) == [MATOMO, MATOMO]
    assert calls == [("restart", "matomo")]


def test_to_must_exist_in_registry(env):
    with pytest.raises(ConfigError, match="no tag '5.99.0'"):
        _run(env, _Runner([LOCAL_MATOMO]), _Client(["5.8.0", "5.14.0"]), to="5.99.0", yes=True)


def test_own_images_are_refused(env):
    (env / "quadlets" / "prod" / "matomo.container").write_text("Image=localhost/visp-matomo:latest\n")
    with pytest.raises(ConfigError, match="build"):
        _run(env, _Runner(), _Client([]))
