"""NetworkManager: netavark checks, configuration and network creation."""

from __future__ import annotations

from pathlib import Path
from typing import Tuple

from .runner import Runner, color, Colors


class NetworkManager:
    def __init__(self, runner: Runner, containers_conf: Path = None):
        self.runner = runner
        self.containers_conf = containers_conf or (
            Path.home() / ".config/containers/containers.conf"
        )

    def check_netavark(self) -> Tuple[bool, str]:
        rc, stdout, _ = self.runner.run_quiet(
            ["podman", "info", "--format", "{{.Host.NetworkBackend}}"]
        )
        if rc == 0:
            backend = stdout.strip()
            return (backend == "netavark", backend)
        return (False, "unknown")

    def ensure_networks_exist(self) -> bool:
        required_networks = [
            {"name": "systemd-visp-net", "internal": False},
            {"name": "systemd-whisper-net", "internal": True},
            {"name": "systemd-octra-net", "internal": True},
        ]

        rc, stdout, _ = self.runner.run_quiet(
            ["podman", "network", "ls", "--format", "{{.Name}}"]
        )
        if rc != 0:
            print(color("  ✗ Failed to list networks", Colors.RED))
            return False

        existing_networks = set(stdout.strip().split("\n")) if stdout.strip() else set()

        for net in required_networks:
            if net["name"] in existing_networks:
                print(f"  ○ {net['name']}: exists")
                continue

            print(f"  Creating {net['name']}...")
            cmd = ["podman", "network", "create", "--driver", "bridge"]
            if net["internal"]:
                cmd.append("--internal")
            cmd.append(net["name"])

            res = self.runner.run(cmd, check=False)
            if res.returncode == 0:
                print(color(f"  ✓ {net['name']}: created", Colors.GREEN))
            else:
                print(color(f"  ✗ {net['name']}: failed", Colors.RED))
                return False

        return True

    def configure_netavark(self) -> bool:
        """Configure Podman to use netavark backend."""
        print(color("Configuring netavark network backend...", Colors.CYAN))

        # Check if packages are installed
        rc, _, _ = self.runner.run_quiet(
            ["dpkg", "-l", "podman-netavark", "aardvark-dns"]
        )
        if rc != 0:
            print(color("  ✗ Required packages not installed", Colors.RED))
            print("  Please install: sudo apt install podman-netavark aardvark-dns")
            return False

        # Ensure containers config dir exists
        self.containers_conf.parent.mkdir(parents=True, exist_ok=True)

        config_lines = []
        network_section_exists = False

        if self.containers_conf.exists():
            with open(self.containers_conf, "r") as f:
                config_lines = f.readlines()
            for line in config_lines:
                if line.strip() == "[network]":
                    network_section_exists = True
                    break

        if not network_section_exists:
            if config_lines and not config_lines[-1].endswith("\n"):
                config_lines.append("\n")
            config_lines.append("\n[network]\n")
            config_lines.append('network_backend = "netavark"\n')
        else:
            in_network_section = False
            backend_set = False
            new_lines = []
            for line in config_lines:
                if line.strip() == "[network]":
                    in_network_section = True
                    new_lines.append(line)
                elif in_network_section and line.startswith("["):
                    if not backend_set:
                        new_lines.append('network_backend = "netavark"\n')
                    in_network_section = False
                    new_lines.append(line)
                elif in_network_section and "network_backend" in line:
                    new_lines.append('network_backend = "netavark"\n')
                    backend_set = True
                else:
                    new_lines.append(line)

            if in_network_section and not backend_set:
                new_lines.append('network_backend = "netavark"\n')
            config_lines = new_lines

        try:
            with open(self.containers_conf, "w") as f:
                f.writelines(config_lines)
            print(color(f"  ✓ Updated {self.containers_conf}", Colors.GREEN))
            return True
        except Exception as e:
            print(color(f"  ✗ Failed to write config: {e}", Colors.RED))
            return False

    def prompt_netavark_migration(self) -> bool:
        """Prompt user for netavark migration. Returns True if user wants to migrate."""
        print()
        print(color("=" * 70, Colors.YELLOW))
        print(color("NETAVARK MIGRATION REQUIRED", Colors.YELLOW))
        print(color("=" * 70, Colors.YELLOW))
        print()
        print("VISP requires the netavark network backend for proper DNS resolution.")
        print("Your system is currently using CNI, which has critical DNS issues.")
        print()
        print(color("What will happen:", Colors.CYAN))
        print("  1. Configure netavark in ~/.config/containers/containers.conf")
        print("  2. Run 'podman system reset' (removes all containers)")
        print("  3. Images are preserved (no need to rebuild)")
        print("  4. Networks will be recreated automatically")
        print()
        print(color("⚠️  WARNING: All running containers will be removed!", Colors.RED))
        print("  Make sure you have backups of important data.")
        print()

        while True:
            response = input("Proceed with migration? (yes/no): ").strip().lower()
            if response in ["yes", "y"]:
                return True
            elif response in ["no", "n"]:
                print()
                print("Migration cancelled. VISP may not work correctly with CNI.")
                print("You can migrate later by running: ./visp-podman.py install")
                return False
            else:
                print("Please answer 'yes' or 'no'")

    def migrate_to_netavark(self) -> bool:
        """Perform netavark migration: configure and podman system reset."""
        if not self.configure_netavark():
            return False

        print()
        print(color("Running podman system reset...", Colors.CYAN))
        print("  This will remove all containers but preserve images.")

        res = self.runner.run(["podman", "system", "reset", "-f"], check=False)
        if res.returncode != 0:
            print(color("  ✗ podman system reset failed", Colors.RED))
            return False
        print(color("  ✓ System reset complete", Colors.GREEN))
        return True
