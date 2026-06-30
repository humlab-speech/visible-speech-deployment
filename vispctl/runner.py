"""Runner abstraction: centralized subprocess helpers."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import List, Tuple

from .env import load_env_file as _load_env_file


class Colors:
    RED = "\033[0;31m"
    GREEN = "\033[0;32m"
    YELLOW = "\033[1;33m"
    BLUE = "\033[0;34m"
    CYAN = "\033[0;36m"
    MAGENTA = "\033[0;35m"
    DIM = "\033[2m"
    NC = "\033[0m"
    BOLD = "\033[1m"


def color(text: str, c: str) -> str:
    return f"{c}{text}{Colors.NC}"


class Runner:
    """Simple wrapper around subprocess calls. Tests can mock Runner methods easily.

    This intentionally mirrors the small API used in `visp.py`.
    """

    def run(self, cmd: List[str], capture: bool = False, check: bool = True, **kwargs) -> subprocess.CompletedProcess:
        """Run a command. Accepts additional subprocess.run kwargs like 'input'."""
        if capture:
            return subprocess.run(cmd, capture_output=True, text=True, check=check, **kwargs)
        return subprocess.run(cmd, check=check, **kwargs)

    def run_quiet(self, cmd: List[str]) -> Tuple[int, str, str]:
        res = subprocess.run(cmd, capture_output=True, text=True)
        return res.returncode, res.stdout.strip(), res.stderr.strip()

    # convenience wrappers
    def systemctl(self, *args, check: bool = False) -> subprocess.CompletedProcess:
        return self.run(["systemctl", "--user", *args], capture=True, check=check)

    def journalctl(self, *args) -> subprocess.CompletedProcess:
        return subprocess.run(["journalctl", "--user", *args])


def load_env_vars(env_file_path: Path) -> dict:
    """Load environment variables from a .env file.

    Deprecated: use vispctl.env.load_env_file() instead.
    Kept for backward compatibility.
    """
    return _load_env_file(env_file_path)


def parse_env_bool(value: str | None, default: bool = True) -> bool:
    """Parse boolean-like environment values with sane defaults."""
    if value is None:
        return default
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    return default


def check_systemd_user_bus() -> None:
    """Warn early if the systemd user bus is unavailable."""
    import os
    import sys
    from pathlib import Path

    xdg = os.environ.get("XDG_RUNTIME_DIR", "")
    uid = os.getuid()
    expected = f"/run/user/{uid}"

    if not xdg:
        print(
            f"\033[33m⚠️  WARNING: XDG_RUNTIME_DIR is not set.\033[0m\n"
            f"   systemctl --user and podman.socket will not work.\n"
            f"   Fix for this session:\n"
            f"     export XDG_RUNTIME_DIR={expected}\n"
            f"     export DBUS_SESSION_BUS_ADDRESS=unix:path={expected}/bus\n"
            f"   Add those lines to ~/.bashrc to make it permanent.\n",
            file=sys.stderr,
        )
        return

    bus_path = Path(xdg) / "bus"
    if not bus_path.exists():
        print(
            f"\033[33m⚠️  WARNING: systemd user bus not found at {bus_path}.\033[0m\n"
            f"   XDG_RUNTIME_DIR={xdg} is set but the bus socket is missing.\n"
            f"   This usually means the systemd user session is not running.\n"
            f"   Try: loginctl enable-linger {os.environ.get('USER', 'your-user')}\n"
            f"   Then re-login or run: systemctl --user start dbus.socket\n",
            file=sys.stderr,
        )

    podman_sock = Path(xdg) / "podman" / "podman.sock"
    if not podman_sock.exists():
        print(
            f"\033[33m⚠️  WARNING: Podman socket not found at {podman_sock}.\033[0m\n"
            f"   session-manager will fail to start containers.\n"
            f"   Fix:\n"
            f"     systemctl --user enable --now podman.service\n",
            file=sys.stderr,
        )
