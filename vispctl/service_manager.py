"""ServiceManager - orchestrates service lifecycle using Runner."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Iterable, List

from .runner import Colors, Runner, color
from .service import Service


def autostart_dropin(systemd_dir: Path, svc: Service) -> Path:
    """Path of the visp autostart drop-in for a service unit."""
    return systemd_dir / f"{svc.file}.d" / "90-visp-autostart.conf"


def remove_autostart_dropin(systemd_dir: Path, svc: Service) -> bool:
    """Remove the visp autostart drop-in (and its dir if left empty).

    Returns True if the drop-in existed and was removed.
    """
    dropin = autostart_dropin(systemd_dir, svc)
    if not dropin.exists():
        return False
    dropin.unlink()
    if not any(dropin.parent.iterdir()):
        dropin.parent.rmdir()
    return True


class ServiceManager:
    def __init__(self, runner: Runner, services: Iterable[Service], systemd_dir: Path | None = None):
        self.runner = runner
        self.services: List[Service] = list(services)
        self.systemd_dir = systemd_dir or Path.home() / ".config/containers/systemd"

    def _svc_name(self, svc: Service) -> str:
        return f"{svc.name}.service"

    def _reload_systemd(self) -> None:
        print("Reloading systemd daemon...")
        res = self.runner.systemctl("daemon-reload")
        if res.returncode != 0:
            print(color(f"  Failed: {res.stderr}", Colors.RED))
        else:
            print(color("  Reloaded", Colors.GREEN))

    def _resolve_targets(self, names: Iterable[str] | str, reverse: bool = False) -> list[Service]:
        """Resolve service names to a list of Service objects."""
        if names == "all":
            targets = list(self.services)
        else:
            if isinstance(names, str):
                names = [names]
            targets = [s for s in self.services if s.name in names]
        return list(reversed(targets)) if reverse else targets

    @staticmethod
    def _note_skip_network(svc: Service) -> None:
        # Network quadlets generate '<name>-network.service' units that are pulled
        # up via Requires= from the containers — no lifecycle action is needed.
        print(color(f"  ○ {svc.name}: network — skipped (comes up via Requires= from containers)", Colors.DIM))

    def _is_image_stale(self, svc: Service) -> bool:
        """True if the running container was started from an older image than :latest."""
        if svc.type != "container":
            return False
        from .images import ImageManager

        im = ImageManager(self.runner)
        return any(s.name == svc.name for s in im.get_stale_containers([svc]))

    def start(self, names: Iterable[str] | str = "all") -> None:
        targets = self._resolve_targets(names)
        for svc in targets:
            if svc.type == "network":
                self._note_skip_network(svc)
                continue
            print(f"Starting {self._svc_name(svc)}...")
            if self.runner.unit_is_active(svc.name):
                print(color("  Already running (start is a no-op)", Colors.DIM))
                if self._is_image_stale(svc):
                    print(
                        color(
                            f"  ⚠ running an older image than :latest — './visp.py apply {svc.name}' to go live",
                            Colors.YELLOW,
                        )
                    )
                continue
            res = self.runner.systemctl("start", self._svc_name(svc))
            if res.returncode != 0:
                print(color(f"  Failed: {res.stderr}", Colors.RED))
            else:
                print(color("  Started", Colors.GREEN))

    def enable(self, names: Iterable[str] | str = "all") -> None:
        targets = self._resolve_targets(names)
        changed = False
        for svc in targets:
            if svc.type == "network":
                self._note_skip_network(svc)
                continue
            print(f"Enabling autostart for {self._svc_name(svc)}...")
            source = self.systemd_dir / svc.file
            if not source.exists():
                print(color(f"  Failed: {source} is not installed", Colors.RED))
                continue

            removed_dropin = remove_autostart_dropin(self.systemd_dir, svc)
            if removed_dropin:
                changed = True

            # Cross-check the actual systemd state: a manual
            # 'systemctl --user disable' leaves no drop-in behind, so
            # drop-in absence alone is not proof the unit starts at boot.
            # is-enabled exits non-zero for disabled/static/masked units, so
            # trust stdout; it is empty only when the unit is not loaded yet
            # (no daemon-reload after install).
            state_res = self.runner.systemctl("is-enabled", self._svc_name(svc))
            state = state_res.stdout.strip()
            if not state:
                print(color("  Not loaded yet — run ./visp.py reload if newly installed", Colors.YELLOW))
                continue

            if state in ("enabled", "generated", "indirect"):
                if removed_dropin:
                    print(color("  Enabled", Colors.GREEN))
                else:
                    print("  Already enabled")
            elif state == "static":
                print(color("  Static unit — no [Install] section, nothing to enable", Colors.YELLOW))
            else:
                res = self.runner.systemctl("enable", self._svc_name(svc))
                if res.returncode != 0:
                    print(color(f"  Failed: {res.stderr.strip()}", Colors.RED))
                else:
                    print(color("  Enabled", Colors.GREEN))

        if changed:
            self._reload_systemd()

    def stop(self, names: Iterable[str] | str = "all") -> None:
        targets = self._resolve_targets(names, reverse=True)

        stopped: list[Service] = []
        for svc in targets:
            if svc.type == "network":
                self._note_skip_network(svc)
                continue
            print(f"Stopping {self._svc_name(svc)}...")
            res = self.runner.systemctl("stop", self._svc_name(svc))
            if res.returncode != 0:
                print(color(f"  Failed: {res.stderr}", Colors.RED))
            else:
                print(color("  Stopped", Colors.GREEN))
                stopped.append(svc)

        # Restart=always + Requires= chains can pull a stopped unit back up via
        # a restarting dependent — verify it actually stayed down. A pull-up is
        # usually still 'activating' at check time, so match both states.
        if stopped:
            time.sleep(3)
            for svc in stopped:
                if self.runner.unit_is_active(svc.name):
                    print(
                        color(
                            f"  ⚠ {svc.name} came back up — a dependent service or Restart= pulled it "
                            "back. Check 'systemctl --user status "
                            f"{svc.name}' for the cause; to keep it off, stop the dependent "
                            f"or use './visp.py down {svc.name}' (also disables autostart)",
                            Colors.YELLOW,
                        )
                    )

    def disable(self, names: Iterable[str] | str = "all") -> None:
        targets = self._resolve_targets(names, reverse=True)
        changed = False
        for svc in targets:
            if svc.type == "network":
                self._note_skip_network(svc)
                continue
            print(f"Disabling autostart for {self._svc_name(svc)}...")
            source = self.systemd_dir / svc.file
            if not source.exists():
                print(color(f"  Failed: {source} is not installed", Colors.RED))
                continue

            dropin = autostart_dropin(self.systemd_dir, svc)
            content = "# Created by visp.py down. Remove this file or run visp.py up to restore autostart.\n"
            content += "[Install]\nWantedBy=\nRequiredBy=\nUpheldBy=\nAlias=\n"
            if dropin.exists() and dropin.read_text() == content:
                print("  Already disabled")
                continue

            dropin.parent.mkdir(parents=True, exist_ok=True)
            dropin.write_text(content)
            changed = True
            print(color("  Disabled", Colors.GREEN))

        if changed:
            self._reload_systemd()

    def status(self) -> None:
        print(color("=== VISP Service Status ===", Colors.CYAN))
        for svc in self.services:
            if svc.type == "network":
                # For networks, check Podman network existence
                rc, _, _ = self.runner.run_quiet(["podman", "network", "exists", f"systemd-{svc.name}"])
                status = "active" if rc == 0 else "not found"
                sym = color("●", Colors.GREEN) if status == "active" else color("○", Colors.YELLOW)
                stat_col = color(status, Colors.GREEN if status == "active" else Colors.YELLOW)
                print(f"  {sym} {svc.name}: {stat_col}")
            else:
                rc, out, _ = self.runner.run_quiet(["systemctl", "--user", "is-active", f"{svc.name}.service"])
                status = out if rc == 0 else "inactive"
                sym = color("●", Colors.GREEN) if status == "active" else color("○", Colors.YELLOW)
                stat_col = color(status, Colors.GREEN if status == "active" else Colors.YELLOW)
                print(f"  {sym} {svc.name}: {stat_col}")
