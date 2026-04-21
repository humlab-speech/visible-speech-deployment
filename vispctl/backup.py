"""BackupManager: handles MongoDB backup and restore tasks."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import List, Optional

from .runner import Colors, Runner, color
from .secrets import SecretManager


class BackupManager:
    def __init__(self, runner: Runner, project_dir: Path | None = None):
        self.runner = runner
        self.project_dir = Path(project_dir) if project_dir else Path(__file__).parent.parent
        self.sm = SecretManager(self.runner, project_dir=self.project_dir)

    def _detect_mongo_version(self) -> str:
        rc, out, _ = self.runner.run_quiet(["podman", "exec", "mongo", "mongod", "--version"])
        if rc == 0 and out:
            for line in out.splitlines():
                if "version" in line.lower():
                    if "v" in line:
                        return line.split("v")[-1].split()[0].split("-")[0]
        return "unknown"

    def list_backups(self, directory: Optional[Path] = None) -> List[Path]:
        d = Path(directory) if directory else Path(".")
        if not d.exists():
            return []
        return sorted([p for p in d.glob("*.tar.gz") if p.is_file()])

    def backup(self, output: Optional[Path] = None, dry_run: bool = False) -> Optional[Path]:
        """Perform a MongoDB backup and return the path to the created archive.

        If dry_run is True, print planned actions and return a suggested path without making changes.
        """
        env = self.sm.load_all()
        mongo_password = env.get("MONGO_ROOT_PASSWORD")
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
                f"--username=root --password=*** "
                f"--authenticationDatabase=admin --out={backup_dir}"
            )
            print(f"  Would run: podman exec mongo tar -czf {archive_in_container} -C /tmp {backup_name}")
            print(f"  Would run: podman cp mongo:{archive_in_container} {output_path}")
            return output_path

        # Run mongodump inside container
        print("Running mongodump...")
        res = self.runner.run(
            [
                "podman",
                "exec",
                "mongo",
                "mongodump",
                "--username=root",
                f"--password={mongo_password}",
                "--authenticationDatabase=admin",
                f"--out={backup_dir}",
            ]
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
            ]
        )
        if res.returncode != 0:
            print(color("✗ Compression failed", Colors.RED))
            return None

        # Copy backup out of container
        print(f"\nCopying to {output_path}...")
        res = self.runner.run(["podman", "cp", f"mongo:{archive_in_container}", str(output_path)])
        if res.returncode != 0:
            print(color("✗ Copy failed", Colors.RED))
            return None

        # Cleanup inside container
        self.runner.run(["podman", "exec", "mongo", "rm", "-rf", backup_dir, archive_in_container])

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
            resp = input("This will restore the database and overwrite data. Continue? (yes/no): ")
            if resp.strip().lower() not in ("yes", "y"):
                print("Restore cancelled.")
                return False

        # Copy file into container
        res = self.runner.run(["podman", "cp", str(b), "mongo:/tmp/restore.tar.gz"])  # reusing /tmp
        if res.returncode != 0:
            print(color("✗ Failed to copy backup into container", Colors.RED))
            return False

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
            ]
        )
        if res.returncode != 0:
            print(color("✗ Failed to extract backup inside container", Colors.RED))
            return False

        # Find the extracted directory (it starts with visp_mongodb_)
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
        if rc != 0 or not out.strip():
            print(color("✗ Could not find backup directory in archive", Colors.RED))
            self.runner.run(["podman", "exec", "mongo", "rm", "-f", "/tmp/restore.tar.gz"])
            return False

        backup_dir = out.strip().splitlines()[0]

        # Run mongorestore
        env = self.sm.load_all()
        mongo_password = env.get("MONGO_ROOT_PASSWORD")
        if not mongo_password:
            print(color("✗ MONGO_ROOT_PASSWORD not found", Colors.RED))
            return False

        res = self.runner.run(
            [
                "podman",
                "exec",
                "mongo",
                "mongorestore",
                "--username=root",
                f"--password={mongo_password}",
                "--authenticationDatabase=admin",
                "--drop",
                backup_dir,
            ]
        )

        # Cleanup
        self.runner.run(["podman", "exec", "mongo", "rm", "-rf", "/tmp/restore.tar.gz", backup_dir])

        if res.returncode != 0:
            print(color("✗ Restore failed", Colors.RED))
            return False

        print(color("✓ Restore complete", Colors.GREEN))
        return True
