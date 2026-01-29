"""Runner abstraction: centralized subprocess helpers."""

from __future__ import annotations

import subprocess
from typing import Tuple, List


class Colors:
    RED = "\033[0;31m"
    GREEN = "\033[0;32m"
    YELLOW = "\033[1;33m"
    CYAN = "\033[0;36m"
    NC = "\033[0m"


def color(text: str, c: str) -> str:
    return f"{c}{text}{Colors.NC}"


class Runner:
    """Simple wrapper around subprocess calls. Tests can mock Runner methods easily.

    This intentionally mirrors the small API used in `visp-podman.py`.
    """

    def run(
        self, cmd: List[str], capture: bool = False, check: bool = True, **kwargs
    ) -> subprocess.CompletedProcess:
        """Run a command. Accepts additional subprocess.run kwargs like 'input'."""
        if capture:
            return subprocess.run(
                cmd, capture_output=True, text=True, check=check, **kwargs
            )
        return subprocess.run(cmd, check=check, **kwargs)

    def run_quiet(self, cmd: List[str]) -> Tuple[int, str, str]:
        res = subprocess.run(cmd, capture_output=True, text=True)
        return res.returncode, res.stdout.strip(), res.stderr.strip()

    # convenience wrappers
    def systemctl(self, *args, check: bool = False) -> subprocess.CompletedProcess:
        return self.run(["systemctl", "--user", *args], capture=True, check=check)

    def journalctl(self, *args) -> subprocess.CompletedProcess:
        return subprocess.run(["journalctl", "--user", *args])
