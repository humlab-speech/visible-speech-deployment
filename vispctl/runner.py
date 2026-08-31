"""Runner abstraction: centralized subprocess helpers."""

from __future__ import annotations

import os
import subprocess
import sys
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

    @staticmethod
    def _flush_stdio() -> None:
        # Ensure buffered Python output reaches the pipe before a non-captured
        # child writes to it, keeping header/label ordering when piped.
        sys.stdout.flush()
        sys.stderr.flush()

    def run(self, cmd: List[str], capture: bool = False, check: bool = True, **kwargs) -> subprocess.CompletedProcess:
        """Run a command. Accepts additional subprocess.run kwargs like 'input'."""
        if capture:
            return subprocess.run(cmd, capture_output=True, text=True, check=check, **kwargs)
        self._flush_stdio()
        return subprocess.run(cmd, check=check, **kwargs)

    def run_quiet(self, cmd: List[str]) -> Tuple[int, str, str]:
        try:
            res = subprocess.run(cmd, capture_output=True, text=True)
        except OSError as e:
            # e.g. podman/systemctl binary missing — surface as a clean non-zero rc
            return 127, "", str(e)
        return res.returncode, res.stdout.strip(), res.stderr.strip()

    # convenience wrappers
    def systemctl(self, *args, check: bool = False) -> subprocess.CompletedProcess:
        return self.run(["systemctl", "--user", *args], capture=True, check=check)

    def unit_is_active(self, unit: str) -> bool:
        """True if the user unit is active or activating."""
        _, out, _ = self.run_quiet(["systemctl", "--user", "is-active", f"{unit}.service"])
        return out.strip() in ("active", "activating")

    def journalctl(self, *args) -> subprocess.CompletedProcess:
        self._flush_stdio()
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
    xdg = os.environ.get("XDG_RUNTIME_DIR", "")
    uid = os.getuid()
    expected = f"/run/user/{uid}"

    if not xdg:
        print(
            color("⚠ WARNING: XDG_RUNTIME_DIR is not set.", Colors.YELLOW) + "\n"
            "   systemctl --user and podman.socket will not work.\n"
            "   Fix for this session:\n"
            f"     export XDG_RUNTIME_DIR={expected}\n"
            f"     export DBUS_SESSION_BUS_ADDRESS=unix:path={expected}/bus\n"
            "   Add those lines to ~/.bashrc to make it permanent.\n",
            file=sys.stderr,
        )
        return

    bus_path = Path(xdg) / "bus"
    if not bus_path.exists():
        print(
            color(
                f"⚠ WARNING: systemd user bus not found at {bus_path}.",
                Colors.YELLOW,
            )
            + "\n"
            f"   XDG_RUNTIME_DIR={xdg} is set but the bus socket is missing.\n"
            "   This usually means the systemd user session is not running.\n"
            f"   Try: loginctl enable-linger {os.environ.get('USER', 'your-user')}\n"
            "   Then re-login or run: systemctl --user start dbus.socket\n",
            file=sys.stderr,
        )

    podman_sock = Path(xdg) / "podman" / "podman.sock"
    if not podman_sock.exists():
        print(
            color(
                f"⚠ WARNING: Podman socket not found at {podman_sock}.",
                Colors.YELLOW,
            )
            + "\n"
            "   session-manager will fail to start containers.\n"
            "   Fix:\n"
            "     systemctl --user enable --now podman.service\n",
            file=sys.stderr,
        )
