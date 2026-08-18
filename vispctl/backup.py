"""BackupManager: handles MongoDB backup and restore tasks."""

from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime
from pathlib import Path

from .config import get_config
from .runner import Colors, Runner, color
from .secrets import SecretManager

_MONGO_CONFIG_PATH = "/tmp/.visp_mongo_conf"


class BackupManager:
    def __init__(self, runner: Runner, project_dir: Path | None = None):
        self.runner = runner
        self.project_dir = Path(project_dir) if project_dir else get_config().project_dir
        self.sm = SecretManager(self.runner, project_dir=self.project_dir)

    def _detect_mongo_version(self) -> str:
        rc, out, _ = self.runner.run_quiet(["podman", "exec", "mongo", "mongod", "--version"])
        if rc == 0 and out:
            for line in out.splitlines():
                if "version" in line.lower():
                    if "v" in line:
                        return line.split("v")[-1].split()[0].split("-")[0]
        return "unknown"

    def list_backups(self, directory: Path | None = None) -> list[Path]:
        d = Path(directory) if directory else Path(".")
        if not d.exists():
            return []
        return sorted([p for p in d.glob("*.tar.gz") if p.is_file()])

    def _get_mongo_password(self) -> str | None:
        """Load MongoDB root password from secrets."""
        env = self.sm.load_all()
        return env.get("MONGO_ROOT_PASSWORD")

    def _write_mongo_config(self, mongo_password: str, container_path: str) -> bool:
        """Write a mongodump/mongorestore `--config` YAML file into the container.

        The file holds only the password; all other options stay on the CLI. This
        avoids exposing the password via the process command line (visible in `ps`).
        """
        payload = f"password: {json.dumps(mongo_password)}\n"
        host_path = None
        try:
            with tempfile.NamedTemporaryFile("w", delete=False) as f:
                host_path = f.name
                f.write(payload)
            os.chmod(host_path, 0o600)
            res = self.runner.run(["podman", "cp", host_path, f"mongo:{container_path}"], check=False)
            if res.returncode != 0:
                return False
            self.runner.run(
                ["podman", "exec", "mongo", "chmod", "600", container_path],
                check=False,
            )
            return True
        finally:
            if host_path:
                try:
                    os.unlink(host_path)
                except OSError:
                    pass

    def _remove_mongo_config(self, container_path: str) -> None:
        """Remove the temporary config file from the container."""
        self.runner.run(
            ["podman", "exec", "mongo", "rm", "-f", container_path],
            check=False,
        )

    def _cleanup_stale_backup_dirs(self) -> None:
        """Remove any stale ``visp_mongodb_*`` dirs left in the container's /tmp.

        A previously interrupted restore can leave an extracted dir behind; if it
        is present when a new restore runs, the old ``find ... | head -1`` logic
        could pick it up instead of the freshly extracted archive.
        """
        self.runner.run(
            [
                "podman",
                "exec",
                "mongo",
                "find",
                "/tmp",
                "-maxdepth",
                "1",
                "-name",
                "visp_mongodb_*",
                "-type",
                "d",
                "-exec",
                "rm",
                "-rf",
                "{}",
                ";",
            ],
            check=False,
        )

    def _resolve_restore_dir(self, tarball_name: str) -> str | None:
        """Determine the extracted backup dir inside the container's /tmp.

        Prefers the dir derived from the tarball name — a ``visp.py backup``
        archive is named ``visp_mongodb_<version>_<timestamp>.tar.gz`` and its
        top-level dir is the same name without the ``.tar.gz``. Falls back to the
        single ``visp_mongodb_*`` dir left after the stale-dir cleanup. Returns
        None if the dir cannot be determined unambiguously.
        """
        stem = tarball_name
        for suffix in (".tar.gz", ".tgz"):
            if stem.endswith(suffix):
                stem = stem[: -len(suffix)]
                break
        if stem.startswith("visp_mongodb_"):
            candidate = f"/tmp/{stem}"
            rc, _, _ = self.runner.run_quiet(["podman", "exec", "mongo", "test", "-d", candidate])
            if rc == 0:
                return candidate

        rc, out, _ = self.runner.run_quiet(
            [
                "podman",
                "exec",
                "mongo",
                "find",
                "/tmp",
                "-maxdepth",
                "1",
                "-name",
                "visp_mongodb_*",
                "-type",
                "d",
            ]
        )
        if rc != 0:
            return None
        dirs = [line for line in out.strip().splitlines() if line.strip()]
        if len(dirs) == 1:
            return dirs[0]
        return None

    def backup(self, output: Path | None = None, dry_run: bool = False) -> Path | None:
        """Perform a MongoDB backup and return the path to the created archive.

        If dry_run is True, print planned actions and return a suggested path
        without making changes.
        """
        mongo_password = self._get_mongo_password()
        if not mongo_password:
            print(
                color(
                    "✗ MONGO_ROOT_PASSWORD not found in .env or .env.secrets",
                    Colors.RED,
                )
            )
            return None

        mongo_version = self._detect_mongo_version()
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_name = f"visp_mongodb_{mongo_version}_{timestamp}"
        backup_dir = f"/tmp/{backup_name}"
        archive_in_container = f"{backup_dir}.tar.gz"
        output_path = Path(output) if output else Path(f"./{backup_name}.tar.gz")

        print(color("=== MongoDB Backup ===", Colors.CYAN))
        print()
        print(f"  MongoDB version: {color(mongo_version, Colors.GREEN)}")
        print(f"  Backup name: {color(backup_name + '.tar.gz', Colors.GREEN)}")
        print()

        if dry_run:
            print(
                color(
                    "Dry run: would run mongodump inside container and copy out archive.",
                    Colors.YELLOW,
                )
            )
            print(
                f"  Would run: podman exec mongo mongodump "
                f"--config={_MONGO_CONFIG_PATH} "
                f"--username=root --authenticationDatabase=admin --out={backup_dir}"
            )
            print(f"  Would run: podman exec mongo tar -czf " f"{archive_in_container} -C /tmp {backup_name}")
            print(f"  Would run: podman cp mongo:{archive_in_container} {output_path}")
            return output_path

        return self._backup_impl(mongo_password, backup_dir, archive_in_container, backup_name, output_path)

    def _backup_impl(
        self,
        mongo_password: str,
        backup_dir: str,
        archive_in_container: str,
        backup_name: str,
        output_path: Path,
    ) -> Path | None:
        """Core backup logic."""
        if not self._write_mongo_config(mongo_password, _MONGO_CONFIG_PATH):
            print(color("✗ Could not write mongodump config file", Colors.RED))
            return None

        try:
            # Run mongodump inside container
            print("Running mongodump...")
            res = self.runner.run(
                [
                    "podman",
                    "exec",
                    "mongo",
                    "mongodump",
                    f"--config={_MONGO_CONFIG_PATH}",
                    "--username=root",
                    "--authenticationDatabase=admin",
                    f"--out={backup_dir}",
                ],
                check=False,
            )
            if res.returncode != 0:
                print(color("✗ Backup failed", Colors.RED))
                return None

            # Compress inside container
            print("\nCompressing backup...")
            res = self.runner.run(
                [
                    "podman",
                    "exec",
                    "mongo",
                    "tar",
                    "-czf",
                    archive_in_container,
                    "-C",
                    "/tmp",
                    backup_name,
                ],
                check=False,
            )
            if res.returncode != 0:
                print(color("✗ Compression failed", Colors.RED))
                return None

            # Copy backup out of container
            print(f"\nCopying to {output_path}...")
            res = self.runner.run(
                ["podman", "cp", f"mongo:{archive_in_container}", str(output_path)],
                check=False,
            )
            if res.returncode != 0:
                print(color("✗ Copy failed", Colors.RED))
                return None

            # Cleanup inside container
            self.runner.run(
                [
                    "podman",
                    "exec",
                    "mongo",
                    "rm",
                    "-rf",
                    backup_dir,
                    archive_in_container,
                ],
                check=False,
            )
        finally:
            self._remove_mongo_config(_MONGO_CONFIG_PATH)

        # Verify file
        if output_path.exists():
            print(color(f"✓ Backup complete: {output_path}", Colors.GREEN))
            print(
                "\nNote: This backup contains ONLY the database "
                "(users, sessions, metadata).\n      Audio files in "
                "mounts/repositories/ should be backed up separately."
            )
            return output_path

        print(color("✗ Backup file not found after copy", Colors.RED))
        return None

    def restore(self, backup_file: Path, force: bool = False) -> bool:
        """Restore MongoDB from backup file. If force is False, prompt the user."""
        b = Path(backup_file)
        if not b.exists():
            print(color(f"✗ Backup file not found: {b}", Colors.RED))
            return False

        if not force:
            resp = input("This will restore the database and overwrite data. " "Continue? (yes/no): ")
            if resp.strip().lower() not in ("yes", "y"):
                print("Restore cancelled.")
                return False

        # Copy file into container
        res = self.runner.run(["podman", "cp", str(b), "mongo:/tmp/restore.tar.gz"], check=False)
        if res.returncode != 0:
            print(color("✗ Failed to copy backup into container", Colors.RED))
            return False

        # Remove stale visp_mongodb_* dirs from any previously interrupted restore
        # so they can't be mistaken for the archive we are about to extract.
        self._cleanup_stale_backup_dirs()

        # Extract archive inside container
        res = self.runner.run(
            [
                "podman",
                "exec",
                "mongo",
                "tar",
                "-xzf",
                "/tmp/restore.tar.gz",
                "-C",
                "/tmp",
            ],
            check=False,
        )
        if res.returncode != 0:
            print(color("✗ Failed to extract backup inside container", Colors.RED))
            return False

        # Determine the extracted directory deterministically (derive it from the
        # tarball name; fall back to the single dir left after the stale cleanup).
        backup_dir = self._resolve_restore_dir(b.name)
        if not backup_dir:
            print(color("✗ Could not find backup directory in archive", Colors.RED))
            self.runner.run(
                ["podman", "exec", "mongo", "rm", "-f", "/tmp/restore.tar.gz"],
                check=False,
            )
            return False

        # Run mongorestore
        mongo_password = self._get_mongo_password()
        if not mongo_password:
            print(color("✗ MONGO_ROOT_PASSWORD not found", Colors.RED))
            return False

        if not self._write_mongo_config(mongo_password, _MONGO_CONFIG_PATH):
            print(color("✗ Could not write mongorestore config file", Colors.RED))
            return False

        try:
            res = self.runner.run(
                [
                    "podman",
                    "exec",
                    "mongo",
                    "mongorestore",
                    f"--config={_MONGO_CONFIG_PATH}",
                    "--username=root",
                    "--authenticationDatabase=admin",
                    "--drop",
                    backup_dir,
                ],
                check=False,
            )
        finally:
            self._remove_mongo_config(_MONGO_CONFIG_PATH)

        # Cleanup
        self.runner.run(
            [
                "podman",
                "exec",
                "mongo",
                "rm",
                "-rf",
                "/tmp/restore.tar.gz",
                backup_dir,
            ],
            check=False,
        )

        if res.returncode != 0:
            print(color("✗ Restore failed", Colors.RED))
            return False

        print(color("✓ Restore complete", Colors.GREEN))
        return True
