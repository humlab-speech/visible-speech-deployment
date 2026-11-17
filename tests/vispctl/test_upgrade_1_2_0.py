"""upgrades/1.2.0.py: emu-webapp-server is renamed annotation-server."""

import json
import subprocess
import sys
from pathlib import Path

import pytest

from vispctl.release import InstallState, Manifest
from vispctl.upgrade import UpgradeContext, UpgradeError, load_release_upgrade

REPO = Path(__file__).resolve().parents[2]


class FakeRunner:
    def __init__(self, origin="https://github.com/humlab-speech/emu-webapp-server.git"):
        self.origin = origin
        self.calls: list[list[str]] = []

    def unit_is_active(self, unit):
        return False

    def systemctl(self, *args, check=False):
        return self.run(["systemctl", "--user", *args])

    def run(self, cmd, capture=False, check=True, **kwargs):
        self.calls.append(cmd)
        out = ""
        if cmd[:1] == ["git"] and "get-url" in cmd:
            out = self.origin
        if cmd[:1] == ["git"] and "set-url" in cmd:
            self.origin = cmd[-1]
        return subprocess.CompletedProcess(cmd, 0, stdout=out + "\n", stderr="")


@pytest.fixture
def upgrade():
    return load_release_upgrade(REPO / "upgrades" / "1.2.0.py")


def _ctx(tmp_path, runner):
    ctx = UpgradeContext(
        tmp_path, runner, "prod", InstallState.load(tmp_path), Manifest(tmp_path / "r.json", {"visp": "1.2.0"}), "1.1.0"
    )
    ctx.release = "1.2.0"
    return ctx


def _step(upgrade, step_id):
    return next(s for s in upgrade.steps if s.id == step_id)


def _old_layout(tmp_path):
    (tmp_path / "external/emu-webapp-server/.git").mkdir(parents=True)
    (tmp_path / "external/emu-webapp-server/src").mkdir()
    (tmp_path / "mounts/emu-webapp-server").mkdir(parents=True)
    (tmp_path / "mounts/emu-webapp-server/.env").write_text("MONGO_DB_NAME=visp\n")


def test_rename_moves_checkout_and_mounts_and_repoints_origin(tmp_path, upgrade):
    _old_layout(tmp_path)
    runner = FakeRunner()
    ctx = _ctx(tmp_path, runner)
    step = _step(upgrade, "rename-annotation-server")

    assert not step.done(ctx)
    step.run(ctx)

    assert ["systemctl", "--user", "stop", "emu-webapp-server.service"] in runner.calls
    assert (tmp_path / "external/annotation-server/src").is_dir()
    assert (tmp_path / "mounts/annotation-server/.env").read_text() == "MONGO_DB_NAME=visp\n"
    assert not (tmp_path / "external/emu-webapp-server").exists()
    assert runner.origin == "https://github.com/humlab-speech/annotation-server.git"
    assert step.done(ctx)


def test_rename_stops_the_old_unit_before_moving_anything(tmp_path, upgrade):
    _old_layout(tmp_path)
    runner = FakeRunner()
    seen = []

    def systemctl(*args, check=False):
        seen.append((args, (tmp_path / "mounts/emu-webapp-server").exists()))
        return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

    runner.systemctl = systemctl
    _step(upgrade, "rename-annotation-server").run(_ctx(tmp_path, runner))

    assert seen == [(("stop", "emu-webapp-server.service"), True)]


def test_rename_resumes_after_a_partial_run(tmp_path, upgrade):
    _old_layout(tmp_path)
    (tmp_path / "mounts/emu-webapp-server").rename(tmp_path / "mounts/annotation-server")
    ctx = _ctx(tmp_path, FakeRunner())
    step = _step(upgrade, "rename-annotation-server")

    step.run(ctx)

    assert (tmp_path / "external/annotation-server").is_dir()
    assert step.done(ctx)


def test_rename_leaves_a_foreign_origin_alone(tmp_path, upgrade):
    _old_layout(tmp_path)
    runner = FakeRunner(origin="https://example.org/fork.git")
    _step(upgrade, "rename-annotation-server").run(_ctx(tmp_path, runner))

    assert runner.origin == "https://example.org/fork.git"
    assert not any("set-url" in c for c in runner.calls)


def test_server_without_the_old_service_skips_the_rename(tmp_path, upgrade):
    assert _step(upgrade, "rename-annotation-server").done(_ctx(tmp_path, FakeRunner()))


def test_preflight_refuses_when_both_names_exist(tmp_path, upgrade):
    _old_layout(tmp_path)
    (tmp_path / "mounts/annotation-server").mkdir()
    runner = FakeRunner()
    with pytest.raises(UpgradeError, match="both"):
        _step(upgrade, "check-annotation-server-rename").run(_ctx(tmp_path, runner))
    assert runner.calls == []  # preflight only looks


def test_preflight_passes_on_the_old_layout(tmp_path, upgrade):
    _old_layout(tmp_path)
    runner = FakeRunner()
    _step(upgrade, "check-annotation-server-rename").run(_ctx(tmp_path, runner))
    assert runner.calls == []


# ── Octra task leftovers (#282) ──────────────────────────────────────────────────

TASK_ID = "V1StGXR8_Z5jdHi6B-myT" + "a" * 43  # nanoid alphabet, 64 chars


def _bundle(tmp_path, project, session, bundle, annotation, raw=None, audio=True):
    d = tmp_path / "mounts/repositories" / project / "Data/VISP_emuDB" / f"{session}_ses" / f"{bundle}_bndl"
    d.mkdir(parents=True)
    if audio:
        (d / (audio if isinstance(audio, str) else f"{bundle}.wav")).write_bytes(b"RIFF")
    path = d / f"{bundle}_annot.json"
    path.write_text(raw if raw is not None else json.dumps(annotation, indent=2) + "\n")
    return path


def _annot(name, annotates, extra=None):
    return {"name": name, "annotates": annotates, "sampleRate": 16000, "levels": [], "links": [], **(extra or {})}


def test_preflight_counts_without_changing_anything(tmp_path, upgrade, capsys):
    bad = _bundle(tmp_path, "p1", "s1", "prompt_1", _annot("prompt_1_annot.json", f"{TASK_ID}.wav"))
    _bundle(tmp_path, "p1", "s2", "prompt_1", _annot("prompt_1", "prompt_1.wav"))
    before = bad.read_text()

    _step(upgrade, "count-task-id-annotations").run(_ctx(tmp_path, FakeRunner()))

    assert bad.read_text() == before
    assert "1 annotation file(s) in 1 project(s)" in capsys.readouterr().out
    assert not (tmp_path / "backups").exists()


def test_fix_restores_the_bundle_name_and_logs_the_old_values(tmp_path, upgrade):
    levels = [
        {"name": "Transcription", "type": "SEGMENT", "items": [{"id": 1, "labels": [{"name": "x", "value": "hej"}]}]}
    ]
    bad = _bundle(tmp_path, "p1", "s1", "prompt_1", _annot("prompt_1_annot.json", f"{TASK_ID}.wav", {"levels": levels}))
    good = _bundle(tmp_path, "p1", "s2", "prompt_1", _annot("prompt_1", "prompt_1.wav"))
    good_before = good.read_text()

    _step(upgrade, "fix-task-id-annotations").run(_ctx(tmp_path, FakeRunner()))

    data = json.loads(bad.read_text())
    assert data["annotates"] == "prompt_1.wav"
    assert data["name"] == "prompt_1"
    assert data["levels"] == levels
    assert good.read_text() == good_before
    [log] = (tmp_path / "backups").glob("upgrade-1.2.0-annotations-*.tsv")
    lines = log.read_text().splitlines()
    assert (
        f"mounts/repositories/p1/Data/VISP_emuDB/s1_ses/prompt_1_bndl/prompt_1_annot.json\tannotates\t{TASK_ID}.wav\tprompt_1.wav"
        in lines
    )
    assert len(lines) == 3  # header + annotates + name


def test_fix_changes_only_the_two_values(tmp_path, upgrade):
    # A layout json.dumps would not reproduce: the diff must still be just those values.
    raw = (
        '{\n  "name": "b1_annot.json",\n  "annotates": "' + TASK_ID + '.wav",\n'
        '  "sampleRate": 16000,\n  "levels": [ ],\n  "links": [ ]\n}'
    )
    path = _bundle(tmp_path, "p1", "s1", "b1", None, raw=raw)

    _step(upgrade, "fix-task-id-annotations").run(_ctx(tmp_path, FakeRunner()))

    assert path.read_text() == raw.replace(TASK_ID + ".wav", "b1.wav").replace('"b1_annot.json"', '"b1"')


def test_fix_is_safe_to_rerun(tmp_path, upgrade):
    path = _bundle(tmp_path, "p1", "s1", "b1", _annot("b1", f"{TASK_ID}.wav"))
    step = _step(upgrade, "fix-task-id-annotations")
    step.run(_ctx(tmp_path, FakeRunner()))
    after = path.read_text()

    step.run(_ctx(tmp_path, FakeRunner()))

    assert path.read_text() == after
    assert json.loads(after)["annotates"] == "b1.wav"


def test_fix_leaves_other_annotates_values_and_broken_files_alone(tmp_path, upgrade):
    other = _bundle(tmp_path, "p1", "s1", "b1", _annot("b1", "something_else.wav"))
    short = _bundle(tmp_path, "p1", "s1", "b2", _annot("b2", "a" * 63 + ".wav"))
    broken = _bundle(tmp_path, "p1", "s1", "b3", None, raw="{not json")
    before = [p.read_text() for p in (other, short, broken)]

    _step(upgrade, "fix-task-id-annotations").run(_ctx(tmp_path, FakeRunner()))

    assert [p.read_text() for p in (other, short, broken)] == before


def test_fix_fails_when_a_file_cannot_be_written(tmp_path, upgrade, monkeypatch):
    path = _bundle(tmp_path, "p1", "s1", "b1", _annot("b1", f"{TASK_ID}.wav"))
    module = sys.modules[upgrade.steps[0].run.__module__]

    def deny(*args, **kwargs):
        raise PermissionError("denied")

    monkeypatch.setattr(module.os, "replace", deny)
    with pytest.raises(UpgradeError, match="could not be written"):
        _step(upgrade, "fix-task-id-annotations").run(_ctx(tmp_path, FakeRunner()))
    assert TASK_ID in path.read_text()
    assert sorted(p.name for p in path.parent.iterdir()) == ["b1.wav", path.name]  # no temp file left


def _fake_mongo(monkeypatch, upgrade, collections):
    module = sys.modules[upgrade.steps[0].run.__module__]
    calls = []

    def mongosh(js):
        calls.append(js)
        if js == "db.getCollectionNames()":
            return list(collections)
        if js.endswith(".countDocuments()"):
            return 7
        if js.endswith(".drop()"):
            collections.remove("octravirtualtasks")
            return True
        raise AssertionError(js)

    monkeypatch.setattr(module, "_mongosh", mongosh)
    return calls


def test_drop_octra_tasks_after_the_upgrade_backup(tmp_path, upgrade, monkeypatch):
    collections = ["projects", "octravirtualtasks"]
    calls = _fake_mongo(monkeypatch, upgrade, collections)
    ctx = _ctx(tmp_path, FakeRunner())
    ctx.state.data["in_progress"] = {"data": {"upgrade": {"backup": "backups/x.tar.gz"}}}
    step = _step(upgrade, "drop-octra-tasks")

    assert not step.done(ctx)
    step.run(ctx)

    assert "db.octravirtualtasks.drop()" in calls
    assert step.done(ctx)


def test_drop_octra_tasks_asks_when_there_was_no_backup(tmp_path, upgrade, monkeypatch):
    collections = ["octravirtualtasks"]
    calls = _fake_mongo(monkeypatch, upgrade, collections)
    ctx = _ctx(tmp_path, FakeRunner())
    ctx.input_fn = lambda prompt: "n"

    with pytest.raises(UpgradeError, match="without a backup"):
        _step(upgrade, "drop-octra-tasks").run(ctx)
    assert "db.octravirtualtasks.drop()" not in calls


def test_fix_points_annotates_only_at_audio_that_is_there(tmp_path, upgrade, capsys):
    renamed = _bundle(tmp_path, "p1", "s1", "b1", _annot("b1", f"{TASK_ID}.wav"), audio="recording.wav")
    missing = _bundle(tmp_path, "p1", "s1", "b2", _annot("b2_annot.json", f"{TASK_ID}.wav"), audio=False)

    _step(upgrade, "fix-task-id-annotations").run(_ctx(tmp_path, FakeRunner()))

    assert json.loads(renamed.read_text())["annotates"] == "recording.wav"
    data = json.loads(missing.read_text())
    assert data["annotates"] == f"{TASK_ID}.wav"  # left alone, and reported
    assert data["name"] == "b2"  # the name is still fixed
    assert "b2_annot.json" in capsys.readouterr().out
