"""PermissionsManager: utilities to repair host filesystem permissions using podman unshare.

This is intended for rootless Podman installations where container-created files may be owned
by high numeric UIDs and not accessible to the host user. The manager provides dry-run
planning and an apply method which executes `podman unshare chown`/`chmod` commands.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Iterable, List

from .runner import Runner, color, Colors


class PermissionsManager:
    def __init__(self, runner: Runner, project_dir: Path | None = None):
        self.runner = runner
        self.project_dir = (
            Path(project_dir) if project_dir else Path(__file__).parent.parent
        )

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
        if host_owner:
            target = "0:0"
        else:
            target_uid = uid if uid is not None else os.getuid()
            target_gid = gid if gid is not None else os.getgid()
            target = f"{target_uid}:{target_gid}"

        cmds: List[str] = []
        for p in paths:
            pathstr = str(p)
            chown_cmd = ["podman", "unshare", "chown"]
            if recursive:
                chown_cmd.append("-R")
            chown_cmd.extend([target, pathstr])

            chmod_cmd = ["podman", "unshare", "chmod"]
            if recursive:
                chmod_cmd.append("-R")
            # Ensure at least traverse permission for dirs and read for files
            chmod_cmd.extend(["a+rx", pathstr])

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

        if host_owner:
            target = "0:0"
        else:
            target_uid = uid if uid is not None else os.getuid()
            target_gid = gid if gid is not None else os.getgid()
            target = f"{target_uid}:{target_gid}"

        for p in paths:
            pathstr = str(p)

            chown_cmd = ["podman", "unshare", "chown"]
            if recursive:
                chown_cmd.append("-R")
            chown_cmd.extend([target, pathstr])

            res = self.runner.run(chown_cmd, check=False)
            if res.returncode != 0:
                print(color(f"✗ chown failed for {pathstr}", Colors.RED))
                ok = False
                # do not bail out; attempt chmod too to surface errors

            chmod_cmd = ["podman", "unshare", "chmod"]
            if recursive:
                chmod_cmd.append("-R")
            chmod_cmd.extend(["a+rx", pathstr])

            res2 = self.runner.run(chmod_cmd, check=False)
            if res2.returncode != 0:
                print(color(f"✗ chmod failed for {pathstr}", Colors.RED))
                ok = False

        return ok
