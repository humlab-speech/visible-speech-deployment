"""Display helpers for 'visp.py status' — quadlet link table, container list, network list."""

from __future__ import annotations

from pathlib import Path
from typing import Callable

from .runner import Colors, Runner, color
from .service import Service


def show_quadlet_table(
    runtime_services: list[Service],
    current_mode: str,
    runner: Runner,
    systemd_dir: Path,
    render_fn: Callable[[str], str],
) -> None:
    """Print the quadlet link table showing each service's install state."""
    from .quadlets import get_quadlet_drift, get_quadlets_dir

    print(color("=== Quadlet Links ===", Colors.CYAN))
    quadlets_dir = get_quadlets_dir(current_mode)
    print(f"  Mode: {color(current_mode, Colors.MAGENTA)}")
    print()

    drifted_svcs, not_installed_svcs = get_quadlet_drift(runtime_services, quadlets_dir, systemd_dir, render_fn)
    drifted_files = {svc.file for svc in drifted_svcs}
    not_installed_files = {svc.file for svc in not_installed_svcs}

    for svc in runtime_services:
        link_path = systemd_dir / svc.file
        target_path = quadlets_dir / svc.file

        if link_path.is_symlink():
            actual_target = link_path.resolve()
            if actual_target == target_path.resolve():
                symbol = color("✓", Colors.GREEN)
                status = color("linked", Colors.GREEN)
            elif actual_target.parent.name in ("dev", "prod"):
                symbol = color("!", Colors.YELLOW)
                linked_mode = actual_target.parent.name
                status = color(f"linked ({linked_mode} mode)", Colors.YELLOW)
            elif actual_target.parent.name == "quadlets":
                symbol = color("!", Colors.YELLOW)
                status = color("linked (legacy, run install --force)", Colors.YELLOW)
            else:
                symbol = color("!", Colors.YELLOW)
                status = color(f"linked (unknown: {actual_target})", Colors.YELLOW)
        elif svc.file in not_installed_files:
            symbol = color("○", Colors.RED)
            status = color("not installed", Colors.RED)
        elif svc.file in drifted_files:
            symbol = color("!", Colors.YELLOW)
            status = color("installed (out of date — run apply or install --force)", Colors.YELLOW)
        elif link_path.exists():
            symbol = color("✓", Colors.GREEN)
            status = color("installed", Colors.GREEN)
        else:
            symbol = color("○", Colors.RED)
            status = color("not installed", Colors.RED)

        print(f"  {symbol} {svc.file}: {status}")

    if drifted_svcs:
        print()
        print(
            color(
                f"  ⚠ {len(drifted_svcs)} quadlet(s) differ from templates. Run './visp.py apply' to update.",
                Colors.YELLOW,
            )
        )


def show_container_list(runner: Runner) -> None:
    """Print live container listing via podman ps."""
    print(color("=== Container Status ===", Colors.CYAN))
    runner.run(
        ["podman", "ps", "-a", "--format", "table {{.Names}}\t{{.Status}}\t{{.Ports}}"],
        check=False,
    )


def show_network_list(runner: Runner) -> None:
    """Print Podman network list."""
    print(color("=== Network Status ===", Colors.CYAN))
    runner.run(["podman", "network", "ls"], check=False)
