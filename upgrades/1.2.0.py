"""VISP 1.2.0 — emu-webapp-server is renamed annotation-server and also serves TRATT.

The server Artic talks to (the EMU-webApp websocket protocol) now also serves
TRATT, so it is named for what it does. Its service, container, image, quadlet,
external/ checkout and mounts/ directory all change name.

The old checkout and mounts directory are moved, not recreated, so local
branches and the service's .env (and prod logs) carry over. The converge then
removes the old quadlet (it is not in the service registry any more) and
installs annotation-server in its place.

TRATT used to be opened through an "Octra task" (session-manager's
createOctraTask, issue #282): every open minted a random 64-character id,
stored it in the octravirtualtasks collection and wrote it into the bundle's
_annot.json as "annotates": "<id>.wav" (and "name": "<bundle>_annot.json").
1.2.0 removes that path. After the converge, when nothing can write those
values any more, the annotation files get their real names back and the
collection is dropped.
"""

import json
import os
import re
import tempfile
from datetime import datetime
from pathlib import Path

from vispctl.upgrade import Step

OLD = "emu-webapp-server"
NEW = "annotation-server"

# A nanoid(64) (alphabet A-Za-z0-9_-) plus ".wav", as createOctraTask wrote it.
TASK_ID_AUDIO = re.compile(r"[A-Za-z0-9_-]{64}\.wav")
OCTRA_TASKS = "octravirtualtasks"


def _pairs(ctx):
    """(old, new) paths of everything named after the service on disk."""
    return [(ctx.project_dir / base / OLD, ctx.project_dir / base / NEW) for base in ("external", "mounts")]


def _check_rename(ctx):
    for old, new in _pairs(ctx):
        if old.exists() and new.exists():
            ctx.fail(
                f"both {old} and {new} exist. Keep the one that holds this server's state "
                f"(normally {old.name}), remove the other, and run the upgrade again"
            )
        if old.exists():
            ctx.info(f"{old.relative_to(ctx.project_dir)} → {new.relative_to(ctx.project_dir)}")


def _rename(ctx):
    # The registry no longer knows the old unit (ctx.stop would skip it), and it
    # must not hold the checkout, the .env or, in dev, port 17890 while they move.
    ctx.runner.systemctl("stop", f"{OLD}.service")
    for old, new in _pairs(ctx):
        if old.exists() and not new.exists():
            old.rename(new)
            ctx.info(f"moved {old.relative_to(ctx.project_dir)} → {new.relative_to(ctx.project_dir)}")

    checkout = ctx.project_dir / "external" / NEW
    if (checkout / ".git").exists():
        url = ctx.run(["git", "-C", str(checkout), "remote", "get-url", "origin"], capture=True, check=False)
        current = url.stdout.strip() if url.returncode == 0 else ""
        if OLD in current:
            ctx.run(["git", "-C", str(checkout), "remote", "set-url", "origin", current.replace(OLD, NEW)])
            ctx.info(f"origin of external/{NEW} now points at {current.replace(OLD, NEW)}")


def _renamed(ctx):
    return not any(old.exists() for old, _ in _pairs(ctx))


# ── Octra task leftovers (#282) ──────────────────────────────────────────────────


def _annotation_files(ctx):
    """(bundle name, path) of every bundle's _annot.json in every project's EMU-DB."""
    root = ctx.project_dir / "mounts" / "repositories"
    for path in sorted(root.glob("*/Data/VISP_emuDB/*_ses/*_bndl/*_annot.json")):
        bundle = path.parent.name[: -len("_bndl")]
        if path.name == f"{bundle}_annot.json":
            yield bundle, path


def _media_file(bundle_dir, bundle):
    """The bundle's audio file name: <bundle>.wav, else its only .wav file, else None."""
    if (bundle_dir / f"{bundle}.wav").is_file():
        return f"{bundle}.wav"
    wavs = sorted(p.name for p in bundle_dir.glob("*.wav") if p.is_file())
    return wavs[0] if len(wavs) == 1 else None


def _corrections(bundle, data, bundle_dir):
    """{field: (old, new)} for the values an Octra task open left in *data*.

    `annotates` is only pointed at audio that is there: a bundle whose audio
    cannot be found keeps its value (reported by _scan) rather than getting a
    name that is just as wrong.
    """
    fixes = {}
    annotates = data.get("annotates")
    if isinstance(annotates, str) and TASK_ID_AUDIO.fullmatch(annotates):
        media = _media_file(bundle_dir, bundle)
        if media:
            fixes["annotates"] = (annotates, media)
    # Always written together with annotates; the EMU-DB format wants the bare bundle name.
    if data.get("name") == f"{bundle}_annot.json":
        fixes["name"] = (data["name"], bundle)
    return fixes


def _rewrite(text, data, fixes):
    """*text* with *fixes* applied, changing only those values when the layout allows."""
    expected = dict(data)
    for field, (_, new) in fixes.items():
        expected[field] = new
    out = text
    for field, (old, new) in fixes.items():
        pattern = re.compile(rf'("{field}"\s*:\s*){re.escape(json.dumps(old))}')
        out, n = pattern.subn(lambda m: m.group(1) + json.dumps(new), out)
        if n != 1:
            break
    else:
        try:
            if json.loads(out) == expected:
                return out
        except json.JSONDecodeError:
            pass
    # Fallback (unusual layout): reserialise like the writers do (2-space indent).
    return json.dumps(expected, indent=2, ensure_ascii=False) + ("\n" if text.endswith("\n") else "")


def _scan(ctx):
    """[(path, bundle, text, data, fixes)] of the files to fix; [(path, error)] of unreadable ones.

    A task id whose bundle has no audio to point at is reported as unreadable
    too: the file is left alone for that value, and the operator told.
    """
    todo, unreadable = [], []
    for bundle, path in _annotation_files(ctx):
        try:
            text = path.read_text(encoding="utf-8")
            data = json.loads(text)
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as e:
            unreadable.append((path, e))
            continue
        if not isinstance(data, dict):
            continue
        fixes = _corrections(bundle, data, path.parent)
        annotates = data.get("annotates")
        if isinstance(annotates, str) and TASK_ID_AUDIO.fullmatch(annotates) and "annotates" not in fixes:
            unreadable.append(
                (path, "annotates names an Octra task, and the bundle has no single .wav to name instead")
            )
        if fixes:
            todo.append((path, bundle, text, data, fixes))
    return todo, unreadable


def _project_of(ctx, path):
    return path.relative_to(ctx.project_dir / "mounts" / "repositories").parts[0]


def _report_unreadable(ctx, unreadable):
    for path, error in unreadable:
        ctx.warn(f"{path.relative_to(ctx.project_dir)} left alone: {error}")


def _count_task_id_annotations(ctx):
    todo, unreadable = _scan(ctx)
    _report_unreadable(ctx, unreadable)
    if not todo:
        ctx.info("no annotation file holds an Octra task id")
        return
    projects = {_project_of(ctx, path) for path, *_ in todo}
    ctx.info(f"{len(todo)} annotation file(s) in {len(projects)} project(s) will get their bundle's name back")


def _fix_task_id_annotations(ctx):
    todo, unreadable = _scan(ctx)
    _report_unreadable(ctx, unreadable)
    if not todo:
        ctx.info("no annotation file holds an Octra task id")
        return

    log_dir = ctx.project_dir / "backups"
    log_dir.mkdir(exist_ok=True)
    log_path = log_dir / f"upgrade-1.2.0-annotations-{datetime.now().strftime('%Y%m%d_%H%M%S')}.tsv"
    failed, per_project = [], {}
    with log_path.open("w", encoding="utf-8") as log:
        log.write("file\tfield\told\tnew\n")
        for path, bundle, text, data, fixes in todo:
            new_text = _rewrite(text, data, fixes)
            try:
                # Atomic: a reader (annotation-server) sees the old file or the new one.
                fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
                try:
                    with os.fdopen(fd, "w", encoding="utf-8") as f:
                        f.write(new_text)
                    os.chmod(tmp, path.stat().st_mode & 0o7777)
                    os.replace(tmp, path)
                except BaseException:
                    Path(tmp).unlink(missing_ok=True)
                    raise
            except OSError as e:
                failed.append((path, e))
                continue
            rel = path.relative_to(ctx.project_dir)
            for field, (old, new) in fixes.items():
                log.write(f"{rel}\t{field}\t{old}\t{new}\n")
            project = _project_of(ctx, path)
            per_project[project] = per_project.get(project, 0) + 1
            log.flush()

    for project, n in sorted(per_project.items()):
        ctx.info(f"project {project}: {n} annotation file(s) fixed")
    ctx.info(f"every change (with the old values) is listed in {log_path.relative_to(ctx.project_dir)}")
    if failed:
        for path, error in failed:
            ctx.warn(f"could not write {path.relative_to(ctx.project_dir)}: {error}")
        ctx.fail(f"{len(failed)} annotation file(s) could not be written; fix the cause and run upgrade again")


def _mongosh(js):
    from vispctl.mongo import mongosh_json

    return mongosh_json(js)


def _octra_tasks_dropped(ctx):
    return OCTRA_TASKS not in (_mongosh("db.getCollectionNames()") or [])


def _upgrade_backup(ctx):
    """The MongoDB backup the built-in backup step took in this upgrade, if any."""
    data = ctx.state.data.get("in_progress", {}).get("data", {})
    return data.get("upgrade", {}).get("backup")


def _drop_octra_tasks(ctx):
    backup = _upgrade_backup(ctx)
    if backup:
        ctx.info(f"its documents are in the backup taken before the upgrade: {backup}")
    elif not ctx.confirm(f"This upgrade took no MongoDB backup (--no-backup). Drop {OCTRA_TASKS} anyway?"):
        ctx.fail(f"Not dropping {OCTRA_TASKS} without a backup; take one, or answer yes")
    count = _mongosh(f"db.{OCTRA_TASKS}.countDocuments()")
    _mongosh(f"db.{OCTRA_TASKS}.drop()")
    ctx.info(f"dropped {OCTRA_TASKS} ({count} document(s); nothing has read them since 1.2.0)")


STEPS = [
    Step(
        "check-annotation-server-rename",
        f"{OLD} can be renamed {NEW} (no directory of either name is in the way)",
        run=_check_rename,
        phase="preflight",
    ),
    Step(
        "rename-annotation-server",
        f"stop {OLD}, move external/{OLD} and mounts/{OLD} to {NEW}",
        run=_rename,
        done=_renamed,
    ),
    Step(
        "count-task-id-annotations",
        "count the annotation files whose name/annotates still hold an Octra task id",
        run=_count_task_id_annotations,
        phase="preflight",
    ),
    # post_apply: the converge has replaced the session-manager that wrote these values.
    Step(
        "fix-task-id-annotations",
        "give annotation files that hold an Octra task id their bundle's name back (#282)",
        run=_fix_task_id_annotations,
        phase="post_apply",
    ),
    Step(
        "drop-octra-tasks",
        f"drop the unused {OCTRA_TASKS} MongoDB collection",
        run=_drop_octra_tasks,
        phase="post_apply",
        done=_octra_tasks_dropped,
    ),
]
