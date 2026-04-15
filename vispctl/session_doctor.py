"""VISP session doctor — diagnose running session containers and their sidecars.

Cross-references four sources of truth to find orphans and inconsistencies:

  1. **Podman** — running ``visp-session-*`` containers (and ``*-proxy`` sidecars)
  2. **Socket directories** — ``mounts/sessions/<name>/`` on the host
  3. **Container labels** — ``visp.proxyFor``, ``visp.type``, ``visp.username``, etc.
  4. **Session-manager** — in-memory session list (via ``/api/debug/sessions``)

Detected problems:
  - Session containers with no matching proxy sidecar
  - Proxy sidecars with no matching session container (orphaned)
  - Socket directories on disk with no running container (stale)
  - Containers in bad states (exited, dead, created-but-never-started)
  - Label mismatches (proxy claims to belong to a non-existent session)
  - **Adrift** containers — running but session-manager doesn't know about them
    (typically after a session-manager restart); users cannot reach these sessions
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

_PROJECT_ROOT = Path(__file__).parent.parent
SESSIONS_DIR = _PROJECT_ROOT / "mounts" / "sessions"

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


# ── Podman helpers ─────────────────────────────────────────────────────────────


def _podman_ps_json(filters: list[str] | None = None, all_: bool = True) -> list[dict]:
    """Run ``podman ps --format json`` with optional filters and return parsed list."""
    cmd = ["podman", "ps", "--format", "json"]
    if all_:
        cmd.append("-a")
    for f in filters or []:
        cmd.extend(["--filter", f])
    result = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        return []
    try:
        data = json.loads(result.stdout)
        return data if isinstance(data, list) else []
    except json.JSONDecodeError:
        return []


def _podman_inspect(name_or_id: str) -> dict | None:
    """Inspect a single container, return parsed JSON or None."""
    result = subprocess.run(
        ["podman", "inspect", name_or_id],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        return None
    try:
        data = json.loads(result.stdout)
        return data[0] if isinstance(data, list) and data else None
    except json.JSONDecodeError:
        return None


# ── Data collection ────────────────────────────────────────────────────────────


def _collect_session_containers() -> dict[str, dict]:
    """Discover visp-session-* containers (excluding proxies and session-manager).

    Returns {container_name: {id, state, labels, created, ...}}.
    """
    containers = {}
    for prefix in ("visp-session-", "hsapp-session-"):
        for c in _podman_ps_json(filters=[f"name={prefix}"]):
            names = c.get("Names", [])
            name = names[0] if names else c.get("Name", "")
            # Skip session-manager itself, and proxy sidecars
            if "session-manager" in name:
                continue
            if name.endswith("-proxy"):
                continue
            containers[name] = {
                "id": c.get("Id", "")[:12],
                "state": c.get("State", "unknown"),
                "labels": c.get("Labels") or {},
                "created": c.get("Created", ""),
                "image": c.get("Image", ""),
            }
    return containers


def _collect_proxy_containers() -> dict[str, dict]:
    """Discover proxy sidecar containers (*-proxy).

    Returns {proxy_name: {id, state, labels, claims_session, ...}}.
    """
    proxies = {}
    for c in _podman_ps_json(filters=["name=-proxy"]):
        names = c.get("Names", [])
        name = names[0] if names else c.get("Name", "")
        if not name.endswith("-proxy"):
            continue
        labels = c.get("Labels") or {}
        proxies[name] = {
            "id": c.get("Id", "")[:12],
            "state": c.get("State", "unknown"),
            "labels": labels,
            "claims_session": labels.get("visp.proxyFor", ""),
            "created": c.get("Created", ""),
            "image": c.get("Image", ""),
        }
    return proxies


def _collect_socket_dirs() -> dict[str, dict]:
    """Scan mounts/sessions/ for socket directories.

    Returns {dir_name: {path, has_ui_sock, has_proxy_sock}}.
    """
    dirs = {}
    if not SESSIONS_DIR.exists():
        return dirs
    for d in sorted(SESSIONS_DIR.iterdir()):
        if not d.is_dir():
            continue
        dirs[d.name] = {
            "path": d,
            "has_ui_sock": (d / "ui.sock").exists(),
            "has_proxy_sock": (d / "proxy.sock").exists(),
        }
    return dirs


def _query_session_manager() -> list[dict] | None:
    """Query session-manager's in-memory session list via its debug endpoint.

    Returns a list of session dicts, or None if session-manager is unreachable.
    Uses ``podman exec`` because the HTTP port is not exposed to the host.
    """
    # Find the running session-manager container
    for name in ("session-manager", "systemd-session-manager"):
        result = subprocess.run(
            ["podman", "inspect", name, "--format", "{{.State.Running}}"],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode == 0 and result.stdout.strip() == "true":
            break
    else:
        return None  # session-manager not running

    result = subprocess.run(
        ["podman", "exec", name, "curl", "-sf", "http://localhost:8080/api/debug/sessions"],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        return None
    try:
        data = json.loads(result.stdout)
        return data if isinstance(data, list) else None
    except json.JSONDecodeError:
        return None


# ── Diagnosis ──────────────────────────────────────────────────────────────────


def _diagnose(
    sessions: dict[str, dict],
    proxies: dict[str, dict],
    socket_dirs: dict[str, dict],
    sm_sessions: list[dict] | None = None,
) -> list[dict]:
    """Build a list of session reports with issues.

    Each report represents one session container OR one orphaned entity.

    Args:
        sm_sessions: Session list from session-manager's /api/debug/sessions.
            If None, session-manager tracking checks are skipped.
    """
    reports = []
    seen_proxies = set()
    seen_socket_dirs = set()

    # Build lookup of container IDs that session-manager knows about.
    # session-manager stores shortDockerContainerId (12 chars).
    sm_tracked_ids: set[str] = set()
    sm_available = sm_sessions is not None
    if sm_available:
        for s in sm_sessions:
            cid = s.get("containerId")
            if cid:
                sm_tracked_ids.add(cid)

    # ── 1. Walk session containers ──────────────────────────────────────────
    for name, info in sorted(sessions.items()):
        report = {
            "name": name,
            "type": "session",
            "id": info["id"],
            "state": info["state"],
            "image": info["image"],
            "labels": info["labels"],
            "username": info["labels"].get("visp.username", "?"),
            "project_id": info["labels"].get("visp.projectId", "?"),
            "hs_app": info["labels"].get("visp.hsApp", "?"),
            "issues": [],
            "warnings": [],
            "proxy": None,
            "socket_dir": None,
        }

        # Container state check
        if info["state"] in ("exited", "dead"):
            report["issues"].append(f"Container is {info['state']} — should be cleaned up")
        elif info["state"] == "created":
            report["warnings"].append("Container is 'created' but never started")
        elif info["state"] != "running":
            report["warnings"].append(f"Unexpected state: {info['state']}")

        # Session-manager tracking check — is this container "adrift"?
        if sm_available and info["state"] == "running":
            if info["id"] not in sm_tracked_ids:
                report["issues"].append(
                    "Adrift — running but session-manager has no record of it (unreachable by users)"
                )

        # Expected proxy name
        expected_proxy = name + "-proxy"
        if expected_proxy in proxies:
            proxy = proxies[expected_proxy]
            seen_proxies.add(expected_proxy)
            report["proxy"] = proxy

            if proxy["state"] in ("exited", "dead"):
                report["issues"].append(f"Proxy sidecar is {proxy['state']}")
            elif proxy["state"] != "running":
                report["warnings"].append(f"Proxy in unexpected state: {proxy['state']}")

            # Verify label points back
            if proxy["claims_session"] and proxy["claims_session"] != name:
                report["warnings"].append(
                    f"Proxy label visp.proxyFor='{proxy['claims_session']}' doesn't match '{name}'"
                )
        else:
            # No proxy — this is only a problem for UDS-isolated containers
            # Check if using network=none (UDS mode)
            inspect = _podman_inspect(name)
            if inspect:
                net_mode = (inspect.get("HostConfig") or {}).get("NetworkMode", "")
                if net_mode == "none":
                    report["issues"].append("UDS-isolated (network=none) but no proxy sidecar found")
                else:
                    # Bridge-mode session, proxy not expected
                    pass

        # Socket directory check
        if name in socket_dirs:
            seen_socket_dirs.add(name)
            sd = socket_dirs[name]
            report["socket_dir"] = sd

            if info["state"] == "running":
                if not sd["has_ui_sock"]:
                    report["warnings"].append("Socket dir exists but ui.sock is missing")
                if not sd["has_proxy_sock"] and report["proxy"]:
                    report["warnings"].append("Socket dir exists but proxy.sock is missing")
        else:
            # Only an issue if using UDS
            inspect = _podman_inspect(name) if not report["proxy"] else None
            net_mode = ""
            if inspect:
                net_mode = (inspect.get("HostConfig") or {}).get("NetworkMode", "")
            if report["proxy"] or net_mode == "none":
                report["warnings"].append("No socket directory in mounts/sessions/")

        reports.append(report)

    # ── 2. Orphaned proxies (no matching session container) ─────────────────
    for pname, pinfo in sorted(proxies.items()):
        if pname in seen_proxies:
            continue
        report = {
            "name": pname,
            "type": "orphan-proxy",
            "id": pinfo["id"],
            "state": pinfo["state"],
            "image": pinfo["image"],
            "labels": pinfo["labels"],
            "username": "?",
            "project_id": "?",
            "hs_app": "?",
            "issues": [f"Orphaned proxy sidecar — no session container '{pinfo['claims_session']}' found"],
            "warnings": [],
            "proxy": None,
            "socket_dir": None,
        }
        # Check if the session it claims to belong to ever existed
        if pinfo["claims_session"]:
            report["issues"][0] = (
                f"Orphaned proxy — claims visp.proxyFor='{pinfo['claims_session']}' but that container is gone"
            )
        reports.append(report)

    # ── 3. Stale socket directories (no running container at all) ───────────
    for dirname, dirinfo in sorted(socket_dirs.items()):
        if dirname in seen_socket_dirs:
            continue
        # Also not an orphaned proxy's socket dir
        if any(p.get("claims_session") == dirname for p in proxies.values()):
            continue
        report = {
            "name": dirname,
            "type": "stale-socket-dir",
            "id": None,
            "state": None,
            "image": None,
            "labels": {},
            "username": "?",
            "project_id": "?",
            "hs_app": "?",
            "issues": ["Stale socket directory — no matching container exists"],
            "warnings": [],
            "proxy": None,
            "socket_dir": dirinfo,
        }
        extras = []
        if dirinfo["has_ui_sock"]:
            extras.append("ui.sock present")
        if dirinfo["has_proxy_sock"]:
            extras.append("proxy.sock present")
        if extras:
            report["issues"][0] += f" ({', '.join(extras)})"
        reports.append(report)

    return reports


# ── Rendering ──────────────────────────────────────────────────────────────────


def _state_icon(state: str | None) -> str:
    if state == "running":
        return f"{_C.GREEN}●{_C.NC}"
    if state in ("exited", "dead"):
        return f"{_C.RED}●{_C.NC}"
    if state == "created":
        return f"{_C.YELLOW}●{_C.NC}"
    return f"{_C.DIM}○{_C.NC}"


def _render_reports(
    reports: list[dict],
    *,
    show_healthy: bool = True,
    verbose: bool = False,
) -> None:
    """Print the session doctor tree to stdout."""
    if not reports:
        print(f"\n{_PASS} {_C.GREEN}No session containers found.{_C.NC}\n")
        return

    # Group: sessions first, then orphaned proxies, then stale dirs
    sessions = [r for r in reports if r["type"] == "session"]
    orphan_proxies = [r for r in reports if r["type"] == "orphan-proxy"]
    stale_dirs = [r for r in reports if r["type"] == "stale-socket-dir"]

    has_problems = any(r["issues"] or r["warnings"] for r in reports)

    if not show_healthy and not has_problems:
        print(f"\n{_PASS} {_C.GREEN}{_C.BOLD}All session containers are healthy!{_C.NC}\n")
        return

    # ── Active sessions ─────────────────────────────────────────────────────
    if sessions:
        print(f"\n{_C.BOLD}Session Containers{_C.NC}")
        print(f"{_C.DIM}{'─' * 70}{_C.NC}")

        for i, r in enumerate(sessions):
            is_last = i == len(sessions) - 1 and not orphan_proxies and not stale_dirs
            branch = _L if is_last else _T
            continuation = _S if is_last else _I

            # Status icon
            healthy = not r["issues"] and not r["warnings"]
            if not show_healthy and healthy:
                continue

            icon = _PASS if healthy else (_FAIL if r["issues"] else _WARN)
            state_dot = _state_icon(r["state"])

            print(f"{branch}{state_dot} {icon} {_C.BOLD}{r['name']}{_C.NC}")

            # Details
            details = [
                f"user={_C.CYAN}{r['username']}{_C.NC}",
                f"project={r['project_id']}",
                f"type={r['hs_app']}",
                f"state={r['state']}",
                f"id={r['id']}",
            ]
            print(f"{continuation}  {_C.DIM}{', '.join(details)}{_C.NC}")

            # Proxy status
            if r["proxy"]:
                proxy_state = _state_icon(r["proxy"]["state"])
                print(f"{continuation}  {proxy_state} Proxy sidecar: {r['proxy']['id']} ({r['proxy']['state']})")
            elif not r["issues"] or not any("proxy" in i.lower() for i in r["issues"]):
                print(f"{continuation}  {_C.DIM}No proxy sidecar (bridge-mode){_C.NC}")

            # Socket dir
            if r["socket_dir"]:
                sd = r["socket_dir"]
                sock_parts = []
                if sd["has_ui_sock"]:
                    sock_parts.append(f"{_C.GREEN}ui.sock{_C.NC}")
                else:
                    sock_parts.append(f"{_C.DIM}ui.sock{_C.NC}")
                if sd["has_proxy_sock"]:
                    sock_parts.append(f"{_C.GREEN}proxy.sock{_C.NC}")
                else:
                    sock_parts.append(f"{_C.DIM}proxy.sock{_C.NC}")
                print(f"{continuation}  Socket dir: {', '.join(sock_parts)}")

            if verbose:
                print(f"{continuation}  {_C.DIM}Image: {r['image']}{_C.NC}")

            # Issues
            for issue in r["issues"]:
                print(f"{continuation}  {_FAIL} {_C.RED}{issue}{_C.NC}")
            for warn in r["warnings"]:
                print(f"{continuation}  {_WARN} {_C.YELLOW}{warn}{_C.NC}")

    # ── Orphaned proxies ────────────────────────────────────────────────────
    if orphan_proxies:
        print(f"\n{_C.BOLD}Orphaned Proxy Sidecars{_C.NC}")
        print(f"{_C.DIM}{'─' * 70}{_C.NC}")

        for i, r in enumerate(orphan_proxies):
            is_last = i == len(orphan_proxies) - 1 and not stale_dirs
            branch = _L if is_last else _T
            continuation = _S if is_last else _I

            state_dot = _state_icon(r["state"])
            print(f"{branch}{state_dot} {_FAIL} {_C.BOLD}{r['name']}{_C.NC}")
            print(f"{continuation}  {_C.DIM}id={r['id']}, state={r['state']}{_C.NC}")
            for issue in r["issues"]:
                print(f"{continuation}  {_FAIL} {_C.RED}{issue}{_C.NC}")

    # ── Stale socket dirs ───────────────────────────────────────────────────
    if stale_dirs:
        print(f"\n{_C.BOLD}Stale Socket Directories{_C.NC}")
        print(f"{_C.DIM}{'─' * 70}{_C.NC}")

        for i, r in enumerate(stale_dirs):
            is_last = i == len(stale_dirs) - 1
            branch = _L if is_last else _T
            continuation = _S if is_last else _I

            print(f"{branch}{_FAIL} {r['socket_dir']['path']}")
            for issue in r["issues"]:
                print(f"{continuation}  {_C.RED}{issue}{_C.NC}")


# ── Cleanup actions ────────────────────────────────────────────────────────────


def _cleanup_orphans(
    reports: list[dict],
    *,
    dry_run: bool = True,
    yes: bool = False,
) -> dict:
    """Stop/remove orphaned containers and stale socket dirs.

    Returns summary dict with counts.
    """
    actionable = [r for r in reports if r["issues"]]
    if not actionable:
        return {"removed_containers": 0, "removed_dirs": 0, "status": "nothing_to_do"}

    # Preview
    print(f"\n{_C.BOLD}Cleanup plan:{_C.NC}")
    containers_to_remove = []
    dirs_to_remove = []

    for r in actionable:
        if r["type"] == "orphan-proxy":
            containers_to_remove.append((r["name"], r["id"]))
            print(f"  {_C.RED}Remove{_C.NC} orphaned proxy container: {r['name']} ({r['id']})")
        elif r["type"] == "stale-socket-dir":
            dirs_to_remove.append(r["socket_dir"]["path"])
            print(f"  {_C.RED}Remove{_C.NC} stale socket directory: {r['socket_dir']['path']}")
        elif r["type"] == "session" and r["state"] in ("exited", "dead"):
            containers_to_remove.append((r["name"], r["id"]))
            print(f"  {_C.RED}Remove{_C.NC} dead session container: {r['name']} ({r['id']})")
            # Also clean up its proxy if dead
            if r["proxy"] and r["proxy"]["state"] in ("exited", "dead"):
                proxy_name = r["name"] + "-proxy"
                containers_to_remove.append((proxy_name, r["proxy"]["id"]))
                print(f"  {_C.RED}Remove{_C.NC} dead proxy sidecar: {proxy_name} ({r['proxy']['id']})")
        elif r["type"] == "session" and any("adrift" in i.lower() for i in r["issues"]):
            containers_to_remove.append((r["name"], r["id"]))
            print(f"  {_C.RED}Remove{_C.NC} adrift session container: {r['name']} ({r['id']})")
            # Also clean up its proxy — it's equally unreachable
            if r["proxy"]:
                proxy_name = r["name"] + "-proxy"
                containers_to_remove.append((proxy_name, r["proxy"]["id"]))
                print(f"  {_C.RED}Remove{_C.NC} adrift proxy sidecar: {proxy_name} ({r['proxy']['id']})")
            # And socket dir
            if r.get("socket_dir"):
                path = r["socket_dir"]["path"]
                if isinstance(path, Path):
                    dirs_to_remove.append(path)
                    print(f"  {_C.RED}Remove{_C.NC} adrift socket directory: {path}")

    if not containers_to_remove and not dirs_to_remove:
        print(f"  {_C.DIM}(nothing actionable — issues may require manual attention){_C.NC}")
        return {"removed_containers": 0, "removed_dirs": 0, "status": "nothing_actionable"}

    if dry_run:
        print(f"\n{_C.CYAN}Dry run — no changes made. Use --clean to execute.{_C.NC}")
        return {
            "removed_containers": 0,
            "removed_dirs": 0,
            "would_remove_containers": len(containers_to_remove),
            "would_remove_dirs": len(dirs_to_remove),
            "status": "dry_run",
        }

    if not yes:
        total = len(containers_to_remove) + len(dirs_to_remove)
        answer = input(f"\nProceed with cleanup of {total} item(s)? (y/N): ").strip().lower()
        if answer not in ("y", "yes"):
            return {"removed_containers": 0, "removed_dirs": 0, "status": "cancelled"}

    removed_containers = 0
    removed_dirs = 0

    for cname, _cid in containers_to_remove:
        # Stop first (ignore errors — may already be stopped)
        subprocess.run(
            ["podman", "stop", "--time", "5", cname],
            capture_output=True,
            check=False,
        )
        result = subprocess.run(
            ["podman", "rm", "-f", cname],
            capture_output=True,
            check=False,
        )
        if result.returncode == 0:
            removed_containers += 1
            print(f"  {_PASS} Removed container: {cname}")
        else:
            print(f"  {_FAIL} Failed to remove: {cname}")

    for dirpath in dirs_to_remove:
        import shutil

        try:
            # Socket files may be owned by container user; try direct first, then podman unshare
            shutil.rmtree(dirpath)
            removed_dirs += 1
            print(f"  {_PASS} Removed directory: {dirpath}")
        except PermissionError:
            result = subprocess.run(
                ["podman", "unshare", "rm", "-rf", str(dirpath)],
                capture_output=True,
                check=False,
            )
            if result.returncode == 0:
                removed_dirs += 1
                print(f"  {_PASS} Removed directory (via unshare): {dirpath}")
            else:
                print(f"  {_FAIL} Failed to remove: {dirpath}")

    return {
        "removed_containers": removed_containers,
        "removed_dirs": removed_dirs,
        "status": "ok",
    }


# ── Main entry point ──────────────────────────────────────────────────────────


def run_session_doctor(
    *,
    show_healthy: bool = True,
    problems_only: bool = False,
    json_output: bool = False,
    verbose: bool = False,
    clean: bool = False,
    yes: bool = False,
) -> int:
    """Run the session container doctor.

    Returns the total number of issues found.
    """
    sessions = _collect_session_containers()
    proxies = _collect_proxy_containers()
    socket_dirs = _collect_socket_dirs()

    # Query session-manager for its in-memory session list.
    # This lets us detect "adrift" containers that are running but untracked.
    sm_sessions = _query_session_manager()

    reports = _diagnose(sessions, proxies, socket_dirs, sm_sessions=sm_sessions)

    total_issues = sum(len(r["issues"]) for r in reports)
    total_warnings = sum(len(r["warnings"]) for r in reports)

    if json_output:
        # Make reports JSON-serializable (convert Path objects)
        for r in reports:
            if r["socket_dir"]:
                r["socket_dir"]["path"] = str(r["socket_dir"]["path"])
        output = {
            "sessions": [r for r in reports if r["type"] == "session"],
            "orphan_proxies": [r for r in reports if r["type"] == "orphan-proxy"],
            "stale_socket_dirs": [r for r in reports if r["type"] == "stale-socket-dir"],
            "session_manager": {
                "reachable": sm_sessions is not None,
                "tracked_sessions": len(sm_sessions) if sm_sessions is not None else None,
            },
            "summary": {
                "total_sessions": len(sessions),
                "total_proxies": len(proxies),
                "total_socket_dirs": len(socket_dirs),
                "total_issues": total_issues,
                "total_warnings": total_warnings,
            },
        }
        print(json.dumps(output, indent=2))
        return total_issues

    # Print header
    sm_status = (
        f"{_C.GREEN}connected ({len(sm_sessions)} tracked){_C.NC}"
        if sm_sessions is not None
        else f"{_C.YELLOW}unreachable (adrift detection disabled){_C.NC}"
    )
    print(f"\n{_C.BOLD}Session Container Doctor{_C.NC}")
    print(
        f"{_C.DIM}Containers: {len(sessions)} sessions, {len(proxies)} proxies | "
        f"Socket dirs: {len(socket_dirs)} | Session-manager: {sm_status}{_C.NC}"
    )

    if problems_only:
        reports = [r for r in reports if r["issues"] or r["warnings"]]

    _render_reports(reports, show_healthy=show_healthy, verbose=verbose)

    # Summary line
    print()
    if total_issues == 0 and total_warnings == 0:
        print(f"{_PASS} {_C.GREEN}{_C.BOLD}All session containers are healthy!{_C.NC}")
    else:
        parts = []
        if total_issues:
            parts.append(f"{_C.RED}{total_issues} issue(s){_C.NC}")
        if total_warnings:
            parts.append(f"{_C.YELLOW}{total_warnings} warning(s){_C.NC}")
        print(f"Summary: {', '.join(parts)}")
        if total_issues and not clean:
            print(f"{_C.DIM}Run with --clean to remove orphans and stale dirs{_C.NC}")

    # Cleanup if requested
    if clean:
        _cleanup_orphans(reports, dry_run=False, yes=yes)
    elif total_issues:
        # Show dry-run preview
        _cleanup_orphans(reports, dry_run=True)

    print()
    return total_issues
