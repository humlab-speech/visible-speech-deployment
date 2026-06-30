"""PermissionsManager: utilities to repair host filesystem permissions using podman unshare.

This is intended for rootless Podman installations where container-created files may be owned
by high numeric UIDs and not accessible to the host user. The manager provides dry-run
planning and an apply method which executes `podman unshare chown`/`chmod` commands.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Iterable, List

from .runner import Colors, Runner, color


class PermissionsManager:
    def __init__(self, runner: Runner, project_dir: Path | None = None):
        self.runner = runner
        self.project_dir = Path(project_dir) if project_dir else Path(__file__).parent.parent

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
