"""VISP emuDB consistency audit — cross-checks MongoDB, disk, and bundle lists."""

import json
import sys
from pathlib import Path

from .mongo import mongosh_json

_PROJECT_ROOT = Path(__file__).parent.parent
REPOS_PATH = _PROJECT_ROOT / "mounts" / "repositories"
EMU_DB_PREFIX = "VISP"


# ── Colour helpers ─────────────────────────────────────────────────────────────


class _C:
    RED = "\033[0;31m"
    GREEN = "\033[0;32m"
    YELLOW = "\033[1;33m"
    BOLD = "\033[1m"
    DIM = "\033[2m"
    NC = "\033[0m"


def _ok(msg: str) -> None:
    print(f"  {_C.GREEN}✓{_C.NC} {msg}")


def _warn(msg: str) -> None:
    print(f"  {_C.YELLOW}⚠{_C.NC} {msg}")


def _error(msg: str) -> None:
    print(f"  {_C.RED}✗{_C.NC} {msg}")


def _info(msg: str) -> None:
    print(f"  {_C.DIM}{msg}{_C.NC}")


# ── emuDB filesystem helpers ───────────────────────────────────────────────────


def _emu_db_path(project_id: str) -> Path:
    return REPOS_PATH / project_id / "Data" / f"{EMU_DB_PREFIX}_emuDB"


def _disk_sessions(project_id: str) -> list[str]:
    """Session names present as _ses/ directories on disk."""
    db = _emu_db_path(project_id)
    if not db.exists():
        return []
    return [d.name.removesuffix("_ses") for d in db.iterdir() if d.is_dir() and d.name.endswith("_ses")]


def _disk_bundles_for_session(project_id: str, session_name: str) -> list[str]:
    """Bundle names present as _bndl/ directories inside a session."""
    ses_dir = _emu_db_path(project_id) / f"{session_name}_ses"
    if not ses_dir.exists():
        return []
    return [d.name.removesuffix("_bndl") for d in ses_dir.iterdir() if d.is_dir() and d.name.endswith("_bndl")]


def _bundle_list_entries(project_id: str) -> dict[str, list[dict]]:
    """Return {filename: [{session, name}, ...]} for all bundle list files."""
    bl_dir = _emu_db_path(project_id) / "bundleLists"
    if not bl_dir.exists():
        return {}
    result = {}
    for f in sorted(bl_dir.glob("*.json")):
        try:
            result[f.name] = json.loads(f.read_text())
        except json.JSONDecodeError as e:
            result[f.name] = f"PARSE_ERROR: {e}"  # type: ignore[assignment]
    return result


def _annot_files_for_bundle(project_id: str, session: str, bundle: str) -> list[Path]:
    bndl_dir = _emu_db_path(project_id) / f"{session}_ses" / f"{bundle}_bndl"
    return list(bndl_dir.glob("*_annot.json")) if bndl_dir.exists() else []


# ── Audit logic ────────────────────────────────────────────────────────────────


def audit_project(project: dict, fix_cache: bool = False) -> int:
    """Audit one project. Returns the number of issues found."""
    pid = project.get("id", str(project.get("_id", "?")))
    name = project.get("name", "?")
    issues = 0

    print(f"\n{_C.BOLD}Project: {name}{_C.NC}  {_C.DIM}({pid}){_C.NC}")

    # 1. Repository directory
    repo_dir = REPOS_PATH / pid
    if not repo_dir.exists():
        _error(f"Repository directory missing: {repo_dir}")
        return 1  # No point continuing without the repo
    _ok("Repository directory exists")

    # 2. emuDB directory
    db_path = _emu_db_path(pid)
    if not db_path.exists():
        _warn("emuDB directory missing — project may not have been set up yet")
        return 1
    _ok("emuDB directory exists")

    # 3. Stale cache
    cache_file = db_path / "VISP_emuDBcache.sqlite"
    if cache_file.exists():
        if fix_cache:
            cache_file.unlink()
            _warn("Stale emuDB cache deleted (--fix-cache)")
        else:
            _warn("Stale emuDB cache present — may cause load_emuDB() failures  (re-run with --fix-cache to remove)")
        issues += 1
    else:
        _ok("No stale emuDB cache")

    # 4. MongoDB sessions vs disk
    mongo_sessions = {s["name"] for s in project.get("sessions", [])}
    on_disk = set(_disk_sessions(pid))

    only_in_mongo = mongo_sessions - on_disk
    only_on_disk = on_disk - mongo_sessions

    if not only_in_mongo and not only_on_disk:
        _ok(f"MongoDB sessions match disk  ({len(mongo_sessions)} session(s))")
    else:
        for s in sorted(only_in_mongo):
            _error(f"Session '{s}' is in MongoDB but has NO _ses/ directory on disk")
            issues += 1
        for s in sorted(only_on_disk):
            _warn(f"Session '{s}' has a _ses/ directory on disk but is NOT in MongoDB (orphan / ghost directory)")
            issues += 1

    # 5. Bundle lists vs disk and MongoDB
    bl_data = _bundle_list_entries(pid)
    if not bl_data:
        _warn("No bundle list files found in bundleLists/")
        issues += 1
    else:
        bl_values = [v for v in bl_data.values() if isinstance(v, list)]

        if len(bl_values) > 1:
            canonical = bl_values[0]
            if all(v == canonical for v in bl_values[1:]):
                _ok(f"All {len(bl_data)} bundle list file(s) are identical")
            else:
                _warn("Bundle list files differ from each other:")
                for fname, entries in bl_data.items():
                    if isinstance(entries, list):
                        _info(f"  {fname}: {len(entries)} entries")
                issues += 1

        for fname, entries in bl_data.items():
            if isinstance(entries, str):  # parse error stored as string
                _error(f"bundleLists/{fname}: {entries}")
                issues += 1
                continue

            bl_set = {(e["session"], e["name"]) for e in entries}

            for entry in entries:
                ses, bndl = entry["session"], entry["name"]
                ses_dir = db_path / f"{ses}_ses"
                bndl_dir = ses_dir / f"{bndl}_bndl"
                if ses not in mongo_sessions:
                    _warn(f"bundleLists/{fname}: references session '{ses}' which is not in MongoDB (orphan session)")
                    issues += 1
                if not ses_dir.exists():
                    _error(f"bundleLists/{fname}: references session '{ses}' but {ses}_ses/ does not exist on disk")
                    issues += 1
                elif not bndl_dir.exists():
                    _error(
                        f"bundleLists/{fname}: references bundle '{bndl}' in session '{ses}' "
                        f"but {bndl}_bndl/ does not exist on disk"
                    )
                    issues += 1

            for ses in sorted(mongo_sessions & on_disk):
                for bndl in _disk_bundles_for_session(pid, ses):
                    if (ses, bndl) not in bl_set:
                        _warn(
                            f"Bundle '{bndl}' in session '{ses}' exists on disk "
                            f"but is missing from bundleLists/{fname}"
                        )
                        issues += 1

        _ok(
            f"Bundle list check complete  "
            f"({sum(len(v) for v in bl_values)} bundle entries across {len(bl_data)} file(s))"
        )

    # 6. Duplicate item IDs in _annot.json
    dup_found = False
    for ses in sorted(on_disk):
        for bndl in _disk_bundles_for_session(pid, ses):
            for annot_path in _annot_files_for_bundle(pid, ses, bndl):
                try:
                    annot = json.loads(annot_path.read_text())
                except json.JSONDecodeError as e:
                    _error(f"{ses}/{bndl}: invalid JSON in {annot_path.name}: {e}")
                    issues += 1
                    continue

                all_ids = []
                for level in annot.get("levels", []):
                    for item in level.get("items", []):
                        all_ids.append((item.get("id"), level.get("name", "?")))

                seen: dict = {}
                for item_id, level_name in all_ids:
                    if item_id in seen:
                        _error(
                            f"{ses}/{bndl}/{annot_path.name}: duplicate item id={item_id} "
                            f"(level '{level_name}' and '{seen[item_id]}')"
                        )
                        issues += 1
                        dup_found = True
                    else:
                        seen[item_id] = level_name

    if not dup_found:
        _ok("No duplicate annotation item IDs found")

    # Summary
    if issues == 0:
        print(f"  {_C.GREEN}{_C.BOLD}All checks passed{_C.NC}")
    else:
        print(f"  {_C.YELLOW}{_C.BOLD}{issues} issue(s) found{_C.NC}")

    return issues


def run_audit(project_id: str | None = None, fix_cache: bool = False) -> int:
    """Run audit across all projects (or one). Returns total issue count."""
    print(f"{_C.BOLD}VISP Database Audit{_C.NC}")
    print(f"Repositories: {REPOS_PATH}")

    if project_id:
        project = mongosh_json(f"db.projects.findOne({{id: '{project_id}'}})")
        if not project:
            print(f"{_C.RED}Project '{project_id}' not found in MongoDB{_C.NC}", file=sys.stderr)
            sys.exit(1)
        projects = [project]
    else:
        projects = mongosh_json("db.projects.find({}).toArray()")
        if not projects:
            print("No projects found in MongoDB.")
            return 0

    total = sum(audit_project(p, fix_cache=fix_cache) for p in projects)

    print(f"\n{'─' * 60}")
    if total == 0:
        print(f"{_C.GREEN}{_C.BOLD}All projects OK{_C.NC}")
    else:
        print(f"{_C.YELLOW}{_C.BOLD}Total issues: {total}{_C.NC}")

    return total
