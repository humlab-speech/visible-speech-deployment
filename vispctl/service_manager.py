"""ServiceManager - orchestrates service lifecycle using Runner."""

from __future__ import annotations
from typing import Iterable, List

from .runner import Runner, color, Colors
from .service import Service


class ServiceManager:
    def __init__(self, runner: Runner, services: Iterable[Service]):
        self.runner = runner
        self.services: List[Service] = list(services)

    def _svc_name(self, svc: Service) -> str:
        return f"{svc.name}.service"

    def start(self, names: Iterable[str] | str = "all") -> None:
        if names == "all":
            targets = [s for s in self.services if s.type == "container"]
        else:
            if isinstance(names, str):
                names = [names]
            targets = [s for s in self.services if s.name in names]

        for svc in targets:
            print(f"Starting {self._svc_name(svc)}...")
            res = self.runner.systemctl("start", self._svc_name(svc))
            if res.returncode != 0:
                print(color(f"  Failed: {res.stderr}", Colors.RED))
            else:
                print(color("  Started", Colors.GREEN))

    def stop(self, names: Iterable[str] | str = "all") -> None:
        if names == "all":
            targets = [s for s in reversed(self.services) if s.type == "container"]
        else:
            if isinstance(names, str):
                names = [names]
            targets = [s for s in reversed(self.services) if s.name in names]

        for svc in targets:
            print(f"Stopping {self._svc_name(svc)}...")
            res = self.runner.systemctl("stop", self._svc_name(svc))
            if res.returncode != 0:
                print(color(f"  Failed: {res.stderr}", Colors.RED))
            else:
                print(color("  Stopped", Colors.GREEN))

    def status(self) -> None:
        print(color("=== VISP Service Status (PoC) ===", Colors.CYAN))
        for svc in self.services:
            if svc.type == "network":
                # For networks, check Podman network existence
                rc, _, _ = self.runner.run_quiet(
                    ["podman", "network", "exists", f"systemd-{svc.name}"]
                )
                status = "active" if rc == 0 else "not found"
                sym = (
                    color("●", Colors.GREEN)
                    if status == "active"
                    else color("○", Colors.YELLOW)
                )
                stat_col = color(
                    status, Colors.GREEN if status == "active" else Colors.YELLOW
                )
                print(f"  {sym} {svc.name}: {stat_col}")
            else:
                rc, out, _ = self.runner.run_quiet(
                    ["systemctl", "--user", "is-active", f"{svc.name}.service"]
                )
                status = out if rc == 0 else "inactive"
                sym = (
                    color("●", Colors.GREEN)
                    if status == "active"
                    else color("○", Colors.YELLOW)
                )
                stat_col = color(
                    status, Colors.GREEN if status == "active" else Colors.YELLOW
                )
                print(f"  {sym} {svc.name}: {stat_col}")
