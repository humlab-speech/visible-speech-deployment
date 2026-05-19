"""Runner abstraction: centralized subprocess helpers."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import List, Tuple


class Colors:
    RED = "\033[0;31m"
    GREEN = "\033[0;32m"
    YELLOW = "\033[1;33m"
    BLUE = "\033[0;34m"
    CYAN = "\033[0;36m"
    MAGENTA = "\033[0;35m"
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
    """Load environment variables from a .env file."""
    env_vars: dict[str, str] = {}
    if not env_file_path.exists():
        return env_vars
    with open(env_file_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" in line:
                key, value = line.split("=", 1)
                env_vars[key.strip()] = value.strip()
    return env_vars


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
