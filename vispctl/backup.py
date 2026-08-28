"""BackupManager: handles MongoDB backup and restore tasks."""

from __future__ import annotations

import json
import os
import tarfile
import tempfile
from datetime import datetime
from pathlib import Path

from .config import get_config
from .runner import Colors, Runner, color
from .secrets import SecretManager

_MONGO_CONFIG_PATH = "/tmp/.visp_mongo_conf"
_RESTORE_EXTRACT_DIR = "/tmp/visp_restore_extract"


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
        return sorted(p for p in d.glob("visp_mongodb_*.tar.gz") if p.is_file())

    def _get_mongo_password(self) -> str | None:
        """Load MongoDB root password from secrets."""
        env = self.sm.load_all()
        return env.get("MONGO_ROOT_PASSWORD")

    def _write_mongo_config(self, mongo_password: str, container_path: str) -> bool:
        """Write a mongodump/mongorestore `--config` YAML file into the container.

        The file holds only the password; all other options stay on the CLI. This
        avoids exposing the password via the process command line (visible in `ps`).
        """
        # Wipe any leftover from a crashed run before writing fresh credentials.
        self._remove_mongo_config(container_path)
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

    def _prepare_extract_dir(self) -> None:
        """Wipe and recreate the dedicated restore-extract dir in the container.

        Extracting into a fresh, dedicated dir confines any archive misbehavior
        and replaces the old stale-``visp_mongodb_*``-dir cleanup.
        """
        self.runner.run(
            ["podman", "exec", "mongo", "rm", "-rf", _RESTORE_EXTRACT_DIR],
            check=False,
        )
        self.runner.run(
            ["podman", "exec", "mongo", "mkdir", "-p", _RESTORE_EXTRACT_DIR],
            check=False,
        )

    def _validate_archive_members(self, archive: Path) -> bool | None:
        """Check archive members before the archive is extracted in the container.

        The archive is extracted as root inside the mongo container. Reject
        members that could write outside the extract dir ('..' components,
        absolute paths) or that are not plain files/dirs (symlinks, hardlinks,
        devices, FIFOs — a FIFO named like a .bson file would hang
        mongorestore). GNU tar itself rejects '..' members and strips leading
        '/', so this is defense in depth, not the only line of defense.

        Validates the local file — the exact artifact that gets copied in.
        Returns True if all members are safe, False if any member is unsafe,
        None if the file could not be read as a tar.gz at all.
        """
        try:
            with tarfile.open(archive, "r:gz") as tf:
                for m in tf.getmembers():
                    if not (m.isfile() or m.isdir()):
                        return False
                    if m.name.startswith("/") or ".." in m.name.split("/"):
                        return False
        except (tarfile.TarError, OSError, EOFError):
            return None
        return True

    def _resolve_restore_dir(self, tarball_name: str) -> str | None:
        """Determine the extracted backup dir inside the restore-extract dir.

        Prefers the dir derived from the tarball name — a ``visp.py backup``
        archive is named ``visp_mongodb_<version>_<timestamp>.tar.gz`` and its
        top-level dir is the same name without the ``.tar.gz``. Falls back to
        the single ``visp_mongodb_*`` dir in the (freshly wiped) extract dir.
        Returns None if the dir cannot be determined unambiguously.
        """
        stem = tarball_name.removesuffix(".tar.gz").removesuffix(".tgz")
        if stem.startswith("visp_mongodb_"):
            candidate = f"{_RESTORE_EXTRACT_DIR}/{stem}"
            rc, _, _ = self.runner.run_quiet(["podman", "exec", "mongo", "test", "-d", candidate])
            if rc == 0:
                return candidate

        rc, out, _ = self.runner.run_quiet(
            [
                "podman",
                "exec",
                "mongo",
                "find",
                _RESTORE_EXTRACT_DIR,
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

    def restore(self, backup_file: Path, force: bool = False, drop: bool = False) -> bool:
        """Restore MongoDB from backup file. If force is False, prompt the user.

        With drop=False (default) existing collections are kept unless the backup
        contains the same collection (which is then overwritten); with drop=True
        every restored collection is dropped first (a full replacement).
        """
        b = Path(backup_file)
        if not b.exists():
            print(color(f"✗ Backup file not found: {b}", Colors.RED))
            return False

        if not force:
            drop_note = (
                "existing collections will be DROPPED and replaced"
                if drop
                else "existing collections are kept (use --drop to replace them)"
            )
            print("This will restore the database from the backup.")
            print(f"  - {drop_note}")
            print("  - Stop the services that write to MongoDB first (e.g. './visp.py stop session-manager')")
            print("  - No automatic backup of the current database is taken.")
            try:
                resp = input("Continue? (yes/no): ")
            except EOFError:
                print("No confirmation received (non-interactive). Re-run with --force.")
                return False
            if resp.strip().lower() not in ("yes", "y"):
                print("Restore cancelled.")
                return False

        # Reject unsafe members before copying into the container.
        safe = self._validate_archive_members(b)
        if safe is None:
            print(color("✗ Backup archive is not a readable tar.gz", Colors.RED))
            return False
        if not safe:
            print(color("✗ Backup archive contains unsafe path or link members", Colors.RED))
            return False

        # Copy file into container
        res = self.runner.run(["podman", "cp", str(b), "mongo:/tmp/restore.tar.gz"], check=False)
        if res.returncode != 0:
            print(color("✗ Failed to copy backup into container", Colors.RED))
            return False

        try:
            # Wipe the dedicated extract dir so leftovers from an interrupted
            # restore can't be mistaken for the archive we are about to extract.
            self._prepare_extract_dir()

            # Extract archive inside container (confined to the extract dir)
            res = self.runner.run(
                [
                    "podman",
                    "exec",
                    "mongo",
                    "tar",
                    "-xzf",
                    "/tmp/restore.tar.gz",
                    "-C",
                    _RESTORE_EXTRACT_DIR,
                ],
                check=False,
            )
            if res.returncode != 0:
                print(color("✗ Failed to extract backup inside container", Colors.RED))
                return False

            # Determine the extracted directory deterministically (derive it from the
            # tarball name; fall back to the single dir left in the extract dir).
            backup_dir = self._resolve_restore_dir(b.name)
            if not backup_dir:
                print(color("✗ Could not find backup directory in archive", Colors.RED))
                return False

            # Run mongorestore
            mongo_password = self._get_mongo_password()
            if not mongo_password:
                print(color("✗ MONGO_ROOT_PASSWORD not found", Colors.RED))
                return False

            if not self._write_mongo_config(mongo_password, _MONGO_CONFIG_PATH):
                print(color("✗ Could not write mongorestore config file", Colors.RED))
                return False

            restore_cmd = [
                "podman",
                "exec",
                "mongo",
                "mongorestore",
                f"--config={_MONGO_CONFIG_PATH}",
                "--username=root",
                "--authenticationDatabase=admin",
            ]
            if drop:
                restore_cmd.append("--drop")
            restore_cmd.append(backup_dir)

            try:
                res = self.runner.run(restore_cmd, check=False)
            finally:
                self._remove_mongo_config(_MONGO_CONFIG_PATH)
        finally:
            # Best-effort cleanup: the tarball holds full DB contents and must
            # not linger in the running container on any failure path.
            self.runner.run(
                ["podman", "exec", "mongo", "rm", "-rf", "/tmp/restore.tar.gz", _RESTORE_EXTRACT_DIR],
                check=False,
            )

        if res.returncode != 0:
            print(color("✗ Restore failed", Colors.RED))
            return False

        print(color("✓ Restore complete", Colors.GREEN))
        return True
