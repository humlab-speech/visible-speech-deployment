"""VISP project doctor — tree-view of all projects with health checks.

Shows a tree-like overview of every user's projects, their sessions, bundles,
and audio files, cross-referencing MongoDB ↔ disk ↔ emuDB bundle lists.
"""

import hashlib
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from .mongo import mongosh_json

_PROJECT_ROOT = Path(__file__).parent.parent
REPOS_PATH = _PROJECT_ROOT / "mounts" / "repositories"
EMU_DB_NAME = "VISP_emuDB"
AUDIO_EXTS = {".wav", ".mp3", ".flac", ".ogg"}
LOST_FOUND_DIR = "_lost+found"


def _fix_id(*parts: str) -> str:
    """Deterministic 4-char hex ID from descriptive parts (stable across runs)."""
    key = ":".join(parts)
    return hashlib.sha256(key.encode()).hexdigest()[:4]


def _add_fix(fixes: list[dict], fix_id: str, desc: str, status: str) -> None:
    """Append a structured fix entry."""
    fixes.append({"id": fix_id, "desc": desc, "status": status})


# ── Colour / symbol helpers ────────────────────────────────────────────────────


class _C:
    RED = "\033[0;31m"
    GREEN = "\033[0;32m"
    YELLOW = "\033[1;33m"
    CYAN = "\033[0;36m"
    DIM = "\033[2m"
    BOLD = "\033[1m"
    NC = "\033[0m"


_PASS = f"{_C.GREEN}✓{_C.NC}"
_WARN = f"{_C.YELLOW}⚠{_C.NC}"
_FAIL = f"{_C.RED}✗{_C.NC}"

# Tree-drawing characters (UTF-8 box drawing)
_T = "├── "
_L = "└── "
_I = "│   "
_S = "    "


# ── Filesystem helpers ─────────────────────────────────────────────────────────


def _emu_db_path(project_id: str) -> Path:
    return REPOS_PATH / project_id / "Data" / EMU_DB_NAME


def _disk_sessions(project_id: str) -> dict[str, Path]:
    """Return {session_name: path} for _ses/ dirs on disk."""
    db = _emu_db_path(project_id)
    if not db.exists():
        return {}
    return {d.name.removesuffix("_ses"): d for d in sorted(db.iterdir()) if d.is_dir() and d.name.endswith("_ses")}


def _disk_bundles(session_path: Path) -> dict[str, Path]:
    """Return {bundle_name: path} for _bndl/ dirs in a session."""
    return {
        d.name.removesuffix("_bndl"): d
        for d in sorted(session_path.iterdir())
        if d.is_dir() and d.name.endswith("_bndl")
    }


def _audio_files(bundle_path: Path) -> list[Path]:
    """Return audio files inside a bundle directory."""
    return sorted(f for f in bundle_path.iterdir() if f.is_file() and f.suffix.lower() in AUDIO_EXTS)


def _has_annot(bundle_path: Path) -> bool:
    """Check if any _annot.json file exists in a bundle."""
    return any(f.name.endswith("_annot.json") for f in bundle_path.iterdir() if f.is_file())


def _has_transcription(bundle_path: Path) -> list[str]:
    """Return list of transcription file types present (.srt, .txt)."""
    return sorted(f.suffix for f in bundle_path.iterdir() if f.is_file() and f.suffix in {".srt", ".txt"})


def _bundle_list_entries(project_id: str) -> dict[str, set[tuple[str, str]]]:
    """Return {filename: set((session, bundle))} for bundle list files."""
    bl_dir = _emu_db_path(project_id) / "bundleLists"
    if not bl_dir.exists():
        return {}
    result = {}
    for f in sorted(bl_dir.glob("*.json")):
        try:
            entries = json.loads(f.read_text())
            result[f.name] = {(e["session"], e["name"]) for e in entries}
        except (json.JSONDecodeError, KeyError):
            result[f.name] = set()
    return result


def _human_size(size_bytes: int) -> str:
    """Format bytes as human-readable size."""
    for unit in ("B", "KB", "MB", "GB"):
        if size_bytes < 1024:
            return f"{size_bytes:.1f}{unit}" if unit != "B" else f"{size_bytes}{unit}"
        size_bytes /= 1024
    return f"{size_bytes:.1f}TB"


def _file_to_bundle_name(filename: str) -> str:
    """Convert a file name to expected bundle directory name (strip ext, spaces→underscores)."""
    stem = Path(filename).stem
    return stem.replace(" ", "_")


# ── Fix helpers ────────────────────────────────────────────────────────────────


def _append_manifest(project_path: Path, message: str) -> None:
    """Append a timestamped line to the project's _lost+found/manifest.txt."""
    lf = project_path / "Data" / EMU_DB_NAME / LOST_FOUND_DIR
    manifest = lf / "manifest.txt"
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    line = f"[{ts}] {message}\n"
    try:
        lf.mkdir(parents=True, exist_ok=True)
        with manifest.open("a") as f:
            f.write(line)
    except PermissionError:
        subprocess.run(
            ["podman", "unshare", "mkdir", "-p", str(lf)],
            capture_output=True,
            check=False,
        )
        subprocess.run(
            ["podman", "unshare", "bash", "-c", f"cat >> {manifest}"],
            input=line.encode(),
            capture_output=True,
            check=False,
        )


def _podman_move(src: Path, dst: Path) -> bool:
    """Move a path using podman unshare (files are container-owned)."""
    subprocess.run(
        ["podman", "unshare", "mkdir", "-p", str(dst.parent)],
        capture_output=True,
        check=False,
    )
    result = subprocess.run(
        ["podman", "unshare", "mv", str(src), str(dst)],
        capture_output=True,
        check=False,
    )
    return result.returncode == 0


def _podman_write(path: Path, content: str) -> bool:
    """Write content to a container-owned file via podman unshare."""
    result = subprocess.run(
        ["podman", "unshare", "bash", "-c", f"cat > {path}"],
        input=content.encode(),
        capture_output=True,
        check=False,
    )
    return result.returncode == 0


def _fix_stale_bundle_list_entries(
    project_id: str,
    db_path: Path,
    fixes: list[dict],
    *,
    dry_run: bool = True,
    only_ids: set[str] | None = None,
) -> None:
    """Remove bundle list entries that reference missing sessions or bundles on disk.

    When *dry_run* is True (the default) only reports what would change.
    When *only_ids* is set, only fixes whose ID is in the set are applied.
    """
    bl_dir = db_path / "bundleLists"
    if not bl_dir.exists():
        return

    repo_path = REPOS_PATH / project_id

    for bl_file in sorted(bl_dir.glob("*.json")):
        try:
            raw = bl_file.read_text()
            entries = json.loads(raw)
        except (json.JSONDecodeError, PermissionError):
            continue

        if not isinstance(entries, list):
            continue

        kept = []
        to_remove = []  # (entry, fix_id)
        for entry in entries:
            ses = entry.get("session", "")
            bndl = entry.get("name", "")
            ses_dir = db_path / f"{ses}_ses"
            bndl_dir = ses_dir / f"{bndl}_bndl"

            if not ses_dir.exists() or not bndl_dir.exists():
                fid = _fix_id("stale_bl", project_id, bl_file.name, ses, bndl)
                to_remove.append((entry, fid))
            else:
                kept.append(entry)

        if not to_remove:
            continue

        # Determine which removals to actually perform
        active = []  # entries to remove
        for entry, fid in to_remove:
            desc = f"Prune from {bl_file.name}: session '{entry.get('session')}' bundle '{entry.get('name')}' — missing on disk"
            if dry_run:
                _add_fix(fixes, fid, desc, "would")
            elif only_ids is not None and fid not in only_ids:
                _add_fix(fixes, fid, desc, "skipped")
                kept.append(entry)  # keep it — user didn't select this fix
            else:
                active.append((entry, fid, desc))

        if active:
            new_content = json.dumps(kept, indent=2) + "\n"
            try:
                bl_file.write_text(new_content)
                written = True
            except PermissionError:
                written = _podman_write(bl_file, new_content)

            if written:
                for entry, fid, desc in active:
                    _add_fix(fixes, fid, desc, "applied")
                    _append_manifest(repo_path, f"[{fid}] {desc}")
            else:
                for entry, fid, desc in active:
                    _add_fix(fixes, fid, desc, "failed")


def _fix_orphan_bundles(
    project_id: str,
    db_path: Path,
    mongo_sessions: dict,
    fixes: list[dict],
    *,
    dry_run: bool = True,
    only_ids: set[str] | None = None,
) -> None:
    """Move orphan sessions/bundles to _lost+found/.

    - Entire sessions not in MongoDB -> move the whole ``*_ses/`` directory
      (including session metadata ``.json`` and all bundles inside it).
    - Individual bundles not in MongoDB (but session is valid) → move just
      the ``*_bndl/`` directory.

    When *dry_run* is True (the default) only reports what would change.
    When *only_ids* is set, only fixes whose ID is in the set are applied.
    """
    disk_ses = _disk_sessions(project_id)
    repo_path = REPOS_PATH / project_id
    lf_base = db_path / LOST_FOUND_DIR

    for ses_name, ses_path in disk_ses.items():
        if ses_name not in mongo_sessions:
            # ── Whole session is orphaned — move entire directory ───────
            fid = _fix_id("orphan_session", project_id, ses_name)
            desc = (
                f"Move orphan session '{ses_name}' to "
                f"{LOST_FOUND_DIR}/{ses_name}_ses/ — "
                f"entire session on disk but not in MongoDB"
            )

            if dry_run:
                _add_fix(fixes, fid, desc, "would")
            elif only_ids is not None and fid not in only_ids:
                _add_fix(fixes, fid, desc, "skipped")
            else:
                dest = lf_base / f"{ses_name}_ses"
                if dest.exists():
                    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
                    dest = lf_base / f"{ses_name}_ses_{ts}"

                moved = _podman_move(ses_path, dest)
                if moved:
                    _add_fix(fixes, fid, desc, "applied")
                    _append_manifest(repo_path, f"[{fid}] {desc}")
                else:
                    _add_fix(fixes, fid, f"Move orphan session '{ses_name}'", "failed")
            continue  # Don't also process individual bundles

        # ── Session is in MongoDB — check individual bundles ───────────
        mongo_files = set()
        for f in mongo_sessions[ses_name].get("files", []):
            mongo_files.add(_file_to_bundle_name(f["name"]))

        for bndl_name, bndl_path in _disk_bundles(ses_path).items():
            if bndl_name not in mongo_files:
                fid = _fix_id("orphan_bundle", project_id, ses_name, bndl_name)
                desc = (
                    f"Move orphan bundle '{bndl_name}' from '{ses_name}' to "
                    f"{LOST_FOUND_DIR}/{ses_name}_ses/{bndl_name}_bndl/ — "
                    f"exists on disk but not in MongoDB"
                )

                if dry_run:
                    _add_fix(fixes, fid, desc, "would")
                elif only_ids is not None and fid not in only_ids:
                    _add_fix(fixes, fid, desc, "skipped")
                else:
                    dest = lf_base / f"{ses_name}_ses" / f"{bndl_name}_bndl"
                    if dest.exists():
                        ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
                        dest = lf_base / f"{ses_name}_ses" / f"{bndl_name}_bndl_{ts}"

                    moved = _podman_move(bndl_path, dest)
                    if moved:
                        _add_fix(fixes, fid, desc, "applied")
                        _append_manifest(repo_path, f"[{fid}] {desc}")
                    else:
                        _add_fix(fixes, fid, f"Move orphan bundle '{bndl_name}' in '{ses_name}'", "failed")


# ── Project health diagnosis ──────────────────────────────────────────────────


def _diagnose_project(
    project: dict,
    fix_cache: bool = False,
    fix: bool = False,
    apply: bool = False,
    only_ids: set[str] | None = None,
    session_filter: str | None = None,
    bundle_filter: str | None = None,
) -> dict:
    """Diagnose a project's health. Returns a structured report.

    *fix*    — enable fix mode (dry-run: show what would change).
    *apply*  — actually perform the fixes (requires *fix* to also be True).
    *only_ids* — when applying, only fix items whose ID is in this set.
    *session_filter* / *bundle_filter* — restrict diagnosis to a specific
    session or bundle (name, not suffixed).  Filtering narrows the scope of
    both diagnosis and fixes.
    """
    pid = project["id"]
    pname = project.get("name", "?")
    report = {
        "id": pid,
        "name": pname,
        "members": project.get("members", []),
        "issues": [],
        "warnings": [],
        "fixes": [],
        "sessions": [],
        "stats": {"audio_files": 0, "total_audio_bytes": 0, "bundles": 0, "transcriptions": 0},
    }

    repo_dir = REPOS_PATH / pid
    if not repo_dir.exists():
        report["issues"].append("Repository directory missing on disk")
        return report

    db_path = _emu_db_path(pid)
    if not db_path.exists():
        report["warnings"].append("emuDB not set up yet (no Data/VISP_emuDB/)")
        return report

    # DBconfig.json
    dbconfig = db_path / "VISP_DBconfig.json"
    if not dbconfig.exists():
        report["issues"].append("VISP_DBconfig.json missing")

    # Stale cache
    cache = db_path / "VISP_emuDBcache.sqlite"
    if cache.exists():
        if fix_cache:
            try:
                cache.unlink()
                report["warnings"].append("Stale emuDB cache deleted (--fix-cache)")
            except PermissionError:
                # File owned by container user namespace — use podman unshare
                result = subprocess.run(["podman", "unshare", "rm", "-f", str(cache)], capture_output=True, check=False)
                if result.returncode == 0:
                    report["warnings"].append("Stale emuDB cache deleted via podman unshare (--fix-cache)")
                else:
                    report["issues"].append(f"Cannot delete stale cache (permission denied): {cache}")
        else:
            report["warnings"].append("Stale emuDB cache present (may cause load_emuDB failures, use --fix-cache)")

    # Gather data from both sources
    mongo_sessions = {s["name"]: s for s in project.get("sessions", []) if not s.get("deleted")}
    disk_sessions = _disk_sessions(pid)
    bundle_lists = _bundle_list_entries(pid)

    # Combine the union of session names, optionally filtered
    all_session_names = sorted(set(mongo_sessions.keys()) | set(disk_sessions.keys()))
    if session_filter:
        all_session_names = [n for n in all_session_names if n == session_filter]

    for ses_name in all_session_names:
        in_mongo = ses_name in mongo_sessions
        on_disk = ses_name in disk_sessions
        ses_report = {
            "name": ses_name,
            "in_mongo": in_mongo,
            "on_disk": on_disk,
            "bundles": [],
        }

        if in_mongo and not on_disk:
            report["issues"].append(f"Session '{ses_name}' in MongoDB but missing on disk")
        elif on_disk and not in_mongo:
            report["warnings"].append(f"Session '{ses_name}' on disk but not in MongoDB (orphan)")

        # Gather bundle info from disk
        disk_bndls = _disk_bundles(disk_sessions[ses_name]) if on_disk else {}

        # Gather expected bundles from MongoDB files
        mongo_files = {}
        if in_mongo:
            for f in mongo_sessions[ses_name].get("files", []):
                bndl_name = _file_to_bundle_name(f["name"])
                mongo_files[bndl_name] = f

        # Union of bundle names, optionally filtered
        all_bundle_names = sorted(set(disk_bndls.keys()) | set(mongo_files.keys()))
        if bundle_filter:
            all_bundle_names = [n for n in all_bundle_names if n == bundle_filter]

        for bndl_name in all_bundle_names:
            bndl_on_disk = bndl_name in disk_bndls
            bndl_in_mongo = bndl_name in mongo_files
            bndl_report = {
                "name": bndl_name,
                "on_disk": bndl_on_disk,
                "in_mongo": bndl_in_mongo,
                "audio_files": [],
                "has_annot": False,
                "transcriptions": [],
                "in_bundle_lists": [],
            }

            if bndl_on_disk:
                bndl_path = disk_bndls[bndl_name]
                audio = _audio_files(bndl_path)
                bndl_report["audio_files"] = [{"name": f.name, "size": f.stat().st_size} for f in audio]
                bndl_report["has_annot"] = _has_annot(bndl_path)
                bndl_report["transcriptions"] = _has_transcription(bndl_path)
                report["stats"]["audio_files"] += len(audio)
                report["stats"]["total_audio_bytes"] += sum(f.stat().st_size for f in audio)
                report["stats"]["bundles"] += 1
                if bndl_report["transcriptions"]:
                    report["stats"]["transcriptions"] += 1

            # Check bundle list presence
            for bl_name, bl_entries in bundle_lists.items():
                if (ses_name, bndl_name) in bl_entries:
                    bndl_report["in_bundle_lists"].append(bl_name)

            if bndl_on_disk and not bndl_in_mongo:
                report["warnings"].append(f"Bundle '{bndl_name}' in session '{ses_name}' on disk but not in MongoDB")
            if bndl_in_mongo and not bndl_on_disk:
                report["issues"].append(f"Bundle '{bndl_name}' in session '{ses_name}' in MongoDB but missing on disk")
            if bndl_on_disk and not bndl_report["audio_files"]:
                report["issues"].append(f"Bundle '{bndl_name}' in session '{ses_name}' has no audio file")
            if bndl_on_disk and not bndl_report["has_annot"]:
                report["warnings"].append(f"Bundle '{bndl_name}' in session '{ses_name}' has no annotation file")
            if bndl_on_disk and not bndl_report["in_bundle_lists"]:
                report["warnings"].append(f"Bundle '{bndl_name}' in session '{ses_name}' missing from all bundle lists")

            ses_report["bundles"].append(bndl_report)

        report["sessions"].append(ses_report)

    # ── Bundle list internal consistency (from audit) ──────────────────────────
    bl_dir = _emu_db_path(pid) / "bundleLists"
    if bl_dir.exists():
        bl_raw: dict[str, list[dict] | str] = {}
        for f in sorted(bl_dir.glob("*.json")):
            try:
                bl_raw[f.name] = json.loads(f.read_text())
            except json.JSONDecodeError as e:
                bl_raw[f.name] = f"PARSE_ERROR: {e}"

        for fname, entries in bl_raw.items():
            if isinstance(entries, str):
                report["issues"].append(f"bundleLists/{fname}: {entries}")
                continue

            for entry in entries:
                ses, bndl = entry.get("session", "?"), entry.get("name", "?")
                ses_dir = db_path / f"{ses}_ses"
                if not ses_dir.exists():
                    report["issues"].append(
                        f"bundleLists/{fname}: references session '{ses}' but {ses}_ses/ missing on disk"
                    )
                elif not (ses_dir / f"{bndl}_bndl").exists():
                    report["issues"].append(
                        f"bundleLists/{fname}: references bundle '{bndl}' in '{ses}' but {bndl}_bndl/ missing"
                    )

        # Check if bundle list files are consistent with each other
        bl_lists = [v for v in bl_raw.values() if isinstance(v, list)]
        if len(bl_lists) > 1:
            canonical = bl_lists[0]
            if not all(v == canonical for v in bl_lists[1:]):
                report["warnings"].append("Bundle list files differ from each other")

    # ── Duplicate annotation item IDs (from audit) ─────────────────────────────
    disk_ses = _disk_sessions(pid)
    for ses_name, ses_path in disk_ses.items():
        for bndl_name, bndl_path in _disk_bundles(ses_path).items():
            for annot_path in sorted(bndl_path.glob("*_annot.json")):
                try:
                    annot = json.loads(annot_path.read_text())
                except json.JSONDecodeError as e:
                    report["issues"].append(f"{ses_name}/{bndl_name}/{annot_path.name}: invalid JSON: {e}")
                    continue

                seen: dict[int, str] = {}
                for level in annot.get("levels", []):
                    level_name = level.get("name", "?")
                    for item in level.get("items", []):
                        item_id = item.get("id")
                        if item_id in seen:
                            report["issues"].append(
                                f"{ses_name}/{bndl_name}/{annot_path.name}: "
                                f"duplicate item id={item_id} (levels '{seen[item_id]}' and '{level_name}')"
                            )
                        else:
                            seen[item_id] = level_name

    # ── Apply fixes if requested ───────────────────────────────────────────────
    if fix and db_path.exists():
        dry_run = not apply
        # Move orphan bundles first, then prune bundle lists (order matters:
        # moving orphans creates new stale bundle list entries that the
        # prune step should clean up in the same pass)
        _fix_orphan_bundles(pid, db_path, mongo_sessions, report["fixes"], dry_run=dry_run, only_ids=only_ids)
        _fix_stale_bundle_list_entries(pid, db_path, report["fixes"], dry_run=dry_run, only_ids=only_ids)

    return report


# ── Tree rendering ─────────────────────────────────────────────────────────────


def _status_icon(issues: int, warnings: int) -> str:
    if issues:
        return _FAIL
    if warnings:
        return _WARN
    return _PASS


def _render_tree(reports: list[dict], show_files: bool = True, show_healthy: bool = True) -> None:
    """Render tree-view output grouped by user."""
    # Group projects by owner (first admin member)
    by_user: dict[str, list[dict]] = {}
    for r in reports:
        members = r.get("members", [])
        admins = [m["username"] for m in members if m.get("role") == "admin"]
        owner = admins[0] if admins else "(no owner)"
        by_user.setdefault(owner, []).append(r)

    # Also include non-admin members for cross-reference
    member_projects: dict[str, list[str]] = {}
    for r in reports:
        for m in r.get("members", []):
            if m.get("role") != "admin":
                member_projects.setdefault(m["username"], []).append(r["name"])

    total_issues = sum(len(r["issues"]) for r in reports)
    total_warnings = sum(len(r["warnings"]) for r in reports)
    total_projects = len(reports)
    total_audio = sum(r["stats"]["audio_files"] for r in reports)
    total_bundles = sum(r["stats"]["bundles"] for r in reports)
    total_bytes = sum(r["stats"]["total_audio_bytes"] for r in reports)

    icon = _status_icon(total_issues, total_warnings)
    print(f"\n{_C.BOLD}VISP Project Health Overview{_C.NC}  {icon}")
    print(
        f"{_C.DIM}{total_projects} projects, {total_bundles} bundles, {total_audio} audio files ({_human_size(total_bytes)}){_C.NC}"
    )
    print()

    users = sorted(by_user.keys())
    for ui, user in enumerate(users):
        is_last_user = ui == len(users) - 1
        user_projects = by_user[user]
        user_issues = sum(len(p["issues"]) for p in user_projects)
        user_warnings = sum(len(p["warnings"]) for p in user_projects)
        user_icon = _status_icon(user_issues, user_warnings)

        # Friendly username display
        display_name = user.replace("_at_", "@").replace("_dot_", ".")
        prefix = _L if is_last_user else _T
        print(f"{prefix}{_C.CYAN}{display_name}{_C.NC}  {user_icon}  {_C.DIM}({len(user_projects)} project(s)){_C.NC}")

        # Also-member-of note
        if user in member_projects:
            shared = ", ".join(member_projects[user])
            child_prefix = _S if is_last_user else _I
            print(f"{child_prefix}{_C.DIM}also member of: {shared}{_C.NC}")

        for pi, proj in enumerate(user_projects):
            is_last_proj = pi == len(user_projects) - 1
            proj_issues = len(proj["issues"])
            proj_warnings = len(proj["warnings"])
            proj_icon = _status_icon(proj_issues, proj_warnings)

            if not show_healthy and proj_issues == 0 and proj_warnings == 0:
                continue

            # Prefix for this level
            parent = _S if is_last_user else _I
            branch = _L if is_last_proj else _T

            stats = proj["stats"]
            stat_str = (
                f"{stats['bundles']} bundles, {stats['audio_files']} audio ({_human_size(stats['total_audio_bytes'])})"
            )
            if stats["transcriptions"]:
                stat_str += f", {stats['transcriptions']} transcribed"

            print(f"{parent}{branch}{proj_icon} {_C.BOLD}{proj['name']}{_C.NC}" f"  {_C.DIM}{proj['id']}{_C.NC}")

            child_prefix = parent + (_S if is_last_proj else _I)

            # Stats line
            print(f"{child_prefix}{_C.DIM}{stat_str}{_C.NC}")

            # Issues/warnings/fixes summary
            for issue in proj["issues"]:
                print(f"{child_prefix}{_FAIL} {_C.RED}{issue}{_C.NC}")
            for warn in proj["warnings"]:
                print(f"{child_prefix}{_WARN} {_C.YELLOW}{warn}{_C.NC}")
            for fx in proj.get("fixes", []):
                fid = fx["id"]
                desc = fx["desc"]
                st = fx["status"]
                tag = f"{_C.BOLD}[{fid}]{_C.NC}"
                if st == "would":
                    print(f"{child_prefix}{_C.CYAN}🔧 {tag} {_C.CYAN}{desc}{_C.NC}")
                elif st == "applied":
                    print(f"{child_prefix}{_PASS} {tag} {_C.GREEN}FIXED: {desc}{_C.NC}")
                elif st == "skipped":
                    print(f"{child_prefix}{_C.DIM}⊘  {tag} Skipped: {desc}{_C.NC}")
                elif st == "failed":
                    print(f"{child_prefix}{_FAIL} {tag} {_C.RED}FAILED: {desc}{_C.NC}")

            # Sessions tree
            if show_files and proj["sessions"]:
                sessions = proj["sessions"]
                for si, ses in enumerate(sessions):
                    is_last_ses = si == len(sessions) - 1
                    ses_branch = _L if is_last_ses else _T

                    mongo_tag = "" if ses["in_mongo"] else f" {_C.RED}(not in MongoDB){_C.NC}"
                    disk_tag = "" if ses["on_disk"] else f" {_C.RED}(not on disk){_C.NC}"
                    ses_icon = _PASS if (ses["in_mongo"] and ses["on_disk"]) else _FAIL

                    print(f"{child_prefix}{ses_branch}{ses_icon} {ses['name']}{mongo_tag}{disk_tag}")

                    ses_prefix = child_prefix + (_S if is_last_ses else _I)

                    for bi, bndl in enumerate(ses["bundles"]):
                        is_last_bndl = bi == len(ses["bundles"]) - 1
                        bndl_branch = _L if is_last_bndl else _T

                        # Bundle status
                        bndl_problems = []
                        if not bndl["on_disk"]:
                            bndl_problems.append("missing on disk")
                        if not bndl["in_mongo"]:
                            bndl_problems.append("not in MongoDB")
                        if bndl["on_disk"] and not bndl["audio_files"]:
                            bndl_problems.append("no audio")
                        if bndl["on_disk"] and not bndl["has_annot"]:
                            bndl_problems.append("no annotation")

                        bndl_icon = _FAIL if bndl_problems else _PASS

                        # Audio info
                        audio_info = ""
                        if bndl["audio_files"]:
                            af = bndl["audio_files"][0]
                            audio_info = f" {_C.DIM}[{af['name']}, {_human_size(af['size'])}]{_C.NC}"

                        # Extras
                        extras = []
                        if bndl["transcriptions"]:
                            extras.append(f"transcribed ({', '.join(bndl['transcriptions'])})")
                        if bndl_problems:
                            extras.append(f"{_C.RED}{'; '.join(bndl_problems)}{_C.NC}")

                        extra_str = f"  {_C.DIM}{' | '.join(extras)}{_C.NC}" if extras else ""

                        print(f"{ses_prefix}{bndl_branch}{bndl_icon} {bndl['name']}{audio_info}{extra_str}")

    print()


# ── Orphan detection ───────────────────────────────────────────────────────────


def _find_disk_orphans(mongo_project_ids: set[str]) -> list[str]:
    """Find project directories on disk that have no MongoDB entry."""
    if not REPOS_PATH.exists():
        return []
    return sorted(d.name for d in REPOS_PATH.iterdir() if d.is_dir() and d.name not in mongo_project_ids)


# ── Main entry point ──────────────────────────────────────────────────────────


def run_doctor(
    project_id: str | None = None,
    show_files: bool = True,
    show_healthy: bool = True,
    problems_only: bool = False,
    json_output: bool = False,
    fix_cache: bool = False,
    fix: bool = False,
    apply: bool = False,
    only_ids: set[str] | None = None,
    session_filter: str | None = None,
    bundle_filter: str | None = None,
) -> int:
    """Run the doctor check. Returns the total number of issues (errors, not warnings)."""

    # Fetch projects from MongoDB
    if project_id:
        project = mongosh_json(f"db.projects.findOne({{id: '{project_id}'}})")
        if not project:
            print(f"{_C.RED}Project '{project_id}' not found in MongoDB{_C.NC}")
            return 1
        projects = [project]
    else:
        projects = mongosh_json("db.projects.find({}).toArray()") or []

    if not projects:
        print("No projects found in MongoDB.")
        return 0

    # Diagnose each project
    reports = [
        _diagnose_project(
            p,
            fix_cache=fix_cache,
            fix=fix,
            apply=apply,
            only_ids=only_ids,
            session_filter=session_filter,
            bundle_filter=bundle_filter,
        )
        for p in projects
    ]

    # Check for disk orphans
    mongo_ids = {p["id"] for p in projects}
    disk_orphans = _find_disk_orphans(mongo_ids)

    if json_output:
        output = {
            "projects": reports,
            "disk_orphans": disk_orphans,
            "summary": {
                "total_projects": len(reports),
                "total_issues": sum(len(r["issues"]) for r in reports),
                "total_warnings": sum(len(r["warnings"]) for r in reports),
                "total_audio_files": sum(r["stats"]["audio_files"] for r in reports),
                "total_audio_bytes": sum(r["stats"]["total_audio_bytes"] for r in reports),
                "total_fixes": sum(len(r.get("fixes", [])) for r in reports),
                "fixes": [fx for r in reports for fx in r.get("fixes", [])],
            },
        }
        print(json.dumps(output, indent=2))
        return output["summary"]["total_issues"]

    if problems_only:
        reports = [r for r in reports if r["issues"] or r["warnings"]]
        if not reports and not disk_orphans:
            print(f"\n{_PASS} {_C.GREEN}{_C.BOLD}All projects are healthy!{_C.NC}")
            return 0

    # Render tree
    _render_tree(reports, show_files=show_files, show_healthy=show_healthy)

    # Disk orphans
    if disk_orphans:
        print(f"{_WARN} {_C.YELLOW}Orphaned directories on disk (no MongoDB entry):{_C.NC}")
        for orphan in disk_orphans:
            print(f"  {_FAIL} {REPOS_PATH / orphan}")
        print()

    # Final summary
    total_issues = sum(len(r["issues"]) for r in reports)
    total_warnings = sum(len(r["warnings"]) for r in reports)
    all_fixes = [fx for r in reports for fx in r.get("fixes", [])]
    total_dry = sum(1 for fx in all_fixes if fx["status"] == "would")
    total_applied = sum(1 for fx in all_fixes if fx["status"] == "applied")
    total_failed = sum(1 for fx in all_fixes if fx["status"] == "failed")
    total_skipped = sum(1 for fx in all_fixes if fx["status"] == "skipped")
    dry_ids = [fx["id"] for fx in all_fixes if fx["status"] == "would"]

    if total_issues == 0 and total_warnings == 0 and not disk_orphans:
        print(f"{_PASS} {_C.GREEN}{_C.BOLD}All projects are healthy!{_C.NC}\n")
    else:
        parts = []
        if total_issues:
            parts.append(f"{_C.RED}{total_issues} error(s){_C.NC}")
        if total_warnings:
            parts.append(f"{_C.YELLOW}{total_warnings} warning(s){_C.NC}")
        if total_applied:
            parts.append(f"{_C.GREEN}{total_applied} fix(es) applied{_C.NC}")
        if total_dry:
            parts.append(f"{_C.CYAN}{total_dry} fix(es) available{_C.NC}")
        if total_skipped:
            parts.append(f"{_C.DIM}{total_skipped} skipped{_C.NC}")
        if total_failed:
            parts.append(f"{_C.RED}{total_failed} fix(es) failed{_C.NC}")
        if disk_orphans:
            parts.append(f"{_C.YELLOW}{len(disk_orphans)} orphaned dir(s){_C.NC}")
        print(f"Summary: {', '.join(parts)}")
        if dry_ids:
            all_ids = ",".join(dry_ids)
            print(f"{_C.DIM}Apply all:      --fix --apply{_C.NC}")
            print(f"{_C.DIM}Apply specific:  --fix --apply --only {all_ids}{_C.NC}")
        print()

    return total_issues
