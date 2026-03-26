"""Shared MongoDB helpers for VISP management tools."""

import json
import subprocess
import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).parent.parent
_ENV_FILE = _PROJECT_ROOT / ".env"
_ENV_SECRETS_FILE = _PROJECT_ROOT / ".env.secrets"

DATABASE = "visp"
MONGO_CONTAINER = "mongo"


def load_env() -> dict:
    """Load environment variables from .env and .env.secrets."""
    env = {}
    for env_file in [_ENV_FILE, _ENV_SECRETS_FILE]:
        if env_file.exists():
            with open(env_file) as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith("#") and "=" in line:
                        k, _, v = line.partition("=")
                        env[k.strip()] = v.strip().strip('"').strip("'")
    return env


def get_mongo_password() -> str:
    """Get MongoDB root password from environment files."""
    env = load_env()
    password = env.get("MONGO_ROOT_PASSWORD") or env.get("MONGO_INITDB_ROOT_PASSWORD")
    if not password:
        print("Error: MONGO_ROOT_PASSWORD not found in .env or .env.secrets", file=sys.stderr)
        sys.exit(1)
    return password


def find_mongo_container() -> str:
    """Find the running MongoDB container name."""
    for name in [MONGO_CONTAINER, f"systemd-{MONGO_CONTAINER}", "visp-mongo"]:
        result = subprocess.run(
            ["podman", "inspect", name, "--format", "{{.State.Running}}"],
            capture_output=True,
            text=True,
        )
        if result.returncode == 0 and result.stdout.strip() == "true":
            return name
    print("Error: MongoDB container not running", file=sys.stderr)
    print("Start it with: ./visp-podman.py start mongo", file=sys.stderr)
    sys.exit(1)


def mongosh_json(js_command: str, database: str = DATABASE) -> list | dict | None:
    """Execute a MongoDB command via mongosh and return parsed JSON result."""
    password = get_mongo_password()
    container = find_mongo_container()
    result = subprocess.run(
        [
            "podman",
            "exec",
            container,
            "mongosh",
            "-u",
            "root",
            "-p",
            password,
            "--authenticationDatabase",
            "admin",
            database,
            "--eval",
            f"JSON.stringify({js_command})",
        ],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        print(f"MongoDB error: {result.stderr}", file=sys.stderr)
        sys.exit(1)
    for line in result.stdout.strip().split("\n"):
        line = line.strip()
        if line.startswith("[") or line.startswith("{") or line == "null":
            try:
                return json.loads(line)
            except json.JSONDecodeError:
                continue
    try:
        return json.loads(result.stdout.strip())
    except json.JSONDecodeError:
        return None
