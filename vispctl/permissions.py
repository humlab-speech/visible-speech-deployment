"""PermissionsManager: utilities to repair host filesystem permissions using podman unshare.

This is intended for rootless Podman installations where container-created files may be owned
by high numeric UIDs and not accessible to the host user. The manager provides dry-run
planning and an apply method which executes `podman unshare chown`/`chmod` commands.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Iterable, List

from .config import get_config
from .runner import Colors, Runner, color


class PermissionsManager:
    def __init__(self, runner: Runner, project_dir: Path | None = None):
        self.runner = runner
        self.project_dir = Path(project_dir) if project_dir else get_config().project_dir

    def _resolve_target(self, uid: int | None, gid: int | None, host_owner: bool) -> str:
        if host_owner:
            return "0:0"
        target_uid = uid if uid is not None else os.getuid()
        target_gid = gid if gid is not None else os.getgid()
        return f"{target_uid}:{target_gid}"

    def _build_cmd(self, tool: str, path: str, recursive: bool, *args: str) -> list[str]:
        cmd = ["podman", "unshare", tool]
        if recursive:
            cmd.append("-R")
        cmd.extend(args)
        cmd.append(path)
        return cmd

    def plan_fix(
        self,
        paths: Iterable[Path],
        uid: int | None = None,
        gid: int | None = None,
        recursive: bool = False,
        host_owner: bool = False,
    ) -> List[str]:
        """Return a list of shell commands to run (as strings) to fix permissions.

        If `host_owner` is True, we will attempt a namespace-root chown (0:0) inside
        the user namespace (i.e. `podman unshare chown 0:0 ...`) which on some systems
        maps to the host user without requiring root. This is safe and explicit: we
        will *not* run any sudo commands from this code.
        """
        target = self._resolve_target(uid, gid, host_owner)
        cmds: List[str] = []
        for p in paths:
            pathstr = str(p)
            chown_cmd = self._build_cmd("chown", pathstr, recursive, target)
            chmod_cmd = self._build_cmd("chmod", pathstr, recursive, "a+rx")
            cmds.append(" ".join(chown_cmd))
            cmds.append(" ".join(chmod_cmd))
        return cmds

    def apply_fix(
        self,
        paths: Iterable[Path],
        uid: int | None = None,
        gid: int | None = None,
        recursive: bool = False,
        host_owner: bool = False,
    ) -> bool:
        """Execute podman unshare chown/chmod commands on each path.

        If `host_owner` is True, the function will run `podman unshare chown 0:0 ...`
        to attempt to make the host user the owner via userns mapping. No sudo is
        invoked by this function. After applying the commands, callers can check
        host ownership and act accordingly.

        Returns True if all operations returned 0, False otherwise.
        """
        ok = True
        target = self._resolve_target(uid, gid, host_owner)

        for p in paths:
            pathstr = str(p)
            chown_cmd = self._build_cmd("chown", pathstr, recursive, target)
            res = self.runner.run(chown_cmd, check=False)
            if res.returncode != 0:
                print(color(f"✗ chown failed for {pathstr}", Colors.RED))
                ok = False
                # do not bail out; attempt chmod too to surface errors

            chmod_cmd = self._build_cmd("chmod", pathstr, recursive, "a+rx")

            res2 = self.runner.run(chmod_cmd, check=False)
            if res2.returncode != 0:
                print(color(f"✗ chmod failed for {pathstr}", Colors.RED))
                ok = False

        return ok

    def verify_host_ownership(self, paths: Iterable[Path]) -> list[tuple[Path, int | None]]:
        """Return (path, host_uid) for paths not owned by the current user.

        Returns an empty list when all paths are owned by the current user.
        A ``None`` uid means the path could not be stat-ed (permission denied).
        """
        uid = os.getuid()
        mismatched: list[tuple[Path, int | None]] = []
        for p in paths:
            try:
                st = p.stat()
                if st.st_uid != uid:
                    mismatched.append((p, st.st_uid))
            except OSError:
                mismatched.append((p, None))
        return mismatched


def cmd_fix_permissions(args, project_dir: Path | None = None, runner: Runner | None = None) -> None:
    """Fix file ownership and permissions using 'podman unshare'."""
    if project_dir is None:
        project_dir = get_config().project_dir
    if runner is None:
        runner = Runner()

    if not args.paths:
        from .install import (
            MONGO_CONTAINER_GID,
            MONGO_CONTAINER_UID,
            MONGO_MOUNT_MODE,
            _resolve_rootless_host_id,
            fix_mongo_mount_ownership,
            fix_writable_permissions,
            get_container_writable_dirs,
            normalize_repository_ownership,
        )

        writable_dirs = get_container_writable_dirs(project_dir)
        existing_dirs = [p for p in writable_dirs if p.exists()]
        missing_dirs = [p for p in writable_dirs if not p.exists()]

        print(color("=== Install-Equivalent Permission Fix Plan ===", Colors.CYAN))

        if args.recursive or args.host_owner:
            print(
                color(
                    "Note: --recursive and --host-owner only apply with explicit --path targets; "
                    "the default mode mirrors './visp.py install'.",
                    Colors.YELLOW,
                )
            )

        for p in missing_dirs:
            print(color(f"! Path does not exist: {p.relative_to(project_dir)}", Colors.YELLOW))

        for p in existing_dirs:
            print(f"  chmod 777 {p}")
            print(f"  # fallback if needed: podman unshare chmod 777 {p}")

        mongo_mounts = [
            project_dir / "mounts/mongo/data",
            project_dir / "mounts/mongo/logs",
        ]
        for p in mongo_mounts:
            if p.exists():
                print(f"  podman unshare chown -R 999:999 {p}")
                print(f"  podman unshare chmod -R u+rwX,go-rwx {p}")
                host_uid = _resolve_rootless_host_id(MONGO_CONTAINER_UID, "uid")
                host_gid = _resolve_rootless_host_id(MONGO_CONTAINER_GID, "gid")
                if host_uid is not None and host_gid is not None:
                    print("  # if rootless podman cannot access the path, run as root:")
                    print(f"  sudo chown -R {host_uid}:{host_gid} {p}")
                    print(f"  sudo chmod -R {MONGO_MOUNT_MODE} {p}")
                else:
                    print("  # if rootless podman cannot access the path, inspect uid/gid maps and chown as root")

        repos_dir = project_dir / "mounts/repositories"
        if repos_dir.exists():
            print(f"  podman unshare chown -R 0:0 {repos_dir}")

        if not args.apply:
            print()
            print(color("Dry run complete. Re-run with --apply to make changes.", Colors.YELLOW))
            return

        print()
        print(color("Applying install-equivalent permission fixes...", Colors.CYAN))
        perm_fixed = fix_writable_permissions(project_dir)
        mongo_fixed = fix_mongo_mount_ownership(project_dir)
        repos_ok = normalize_repository_ownership(project_dir)

        print(f"  Container-writable directories fixed: {perm_fixed}")
        print(f"  Mongo mount paths normalized: {mongo_fixed}")
        if repos_ok:
            print("  Repository ownership normalized")
        else:
            print(color("  Repository ownership normalization failed", Colors.YELLOW))
        return

    if args.paths:
        paths = [Path(p) for p in args.paths]

    existing = [p for p in paths if p.exists()]
    missing = [p for p in paths if not p.exists()]
    for p in missing:
        print(color(f"! Path does not exist: {p}", Colors.YELLOW))

    if not existing:
        print(
            color(
                "No existing target paths to operate on. Use --path to specify one.",
                Colors.YELLOW,
            )
        )
        return

    pm = PermissionsManager(runner)
    planned = pm.plan_fix(
        existing,
        recursive=args.recursive,
        host_owner=args.host_owner,
    )

    print(color("=== Permission Fix Plan ===", Colors.CYAN))
    for c in planned:
        print(f"  {c}")

    if not args.apply:
        print()
        print(color("Dry run complete. Re-run with --apply to make changes.", Colors.YELLOW))
        return

    print()
    print(color("Applying permission fixes...", Colors.CYAN))

    ok = pm.apply_fix(
        existing,
        recursive=args.recursive,
        host_owner=args.host_owner,
    )

    if ok:
        print(color("Permissions fixed", Colors.GREEN))
    else:
        print(color("One or more operations failed", Colors.RED))

    mismatched = pm.verify_host_ownership(existing)
    if mismatched:
        print()
        print(
            color(
                "Ownership check: some paths are not owned by the current user on the host:",
                Colors.YELLOW,
            )
        )
        for p, uid in mismatched:
            if uid is None:
                print(f"  - {p}: cannot stat (permission denied)")
            else:
                print(f"  - {p}: host uid={uid} (current user uid={os.getuid()})")
        print(
            color(
                "Note: this can be normal under rootless Podman userns "
                "mapping. If you passed --host-owner and host ownership "
                "does not match, your system's userns mapping doesn't map "
                "namespace 0 to your host UID. In that case you can either "
                "remove files inside the namespace (podman unshare rm -rf) "
                "or manually chown as admin outside this script "
                "(not recommended for normal operation).",
                Colors.YELLOW,
            )
        )
