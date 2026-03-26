#!/usr/bin/env python3
"""
Deprecated — use './visp-podman.py users <command>' instead.

This script is kept as a convenience wrapper and will be removed in a future release.
"""

import subprocess
import sys
from pathlib import Path

if __name__ == "__main__":
    print(
        "⚠  visp-users.py is deprecated. Use: ./visp-podman.py users <command>",
        file=sys.stderr,
    )
    result = subprocess.run([sys.executable, str(Path(__file__).parent / "visp-podman.py"), "users"] + sys.argv[1:])
    sys.exit(result.returncode)
