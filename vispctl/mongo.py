"""Shared MongoDB helpers for VISP management tools."""

import json
import re
import subprocess

from .config import get_config
from .env import load_all_env as _load_all_env
from .exceptions import MongoError

DATABASE = "visp"
MONGO_CONTAINER = "mongo"


def js_escape(s: str) -> str:
    """Escape a string for safe embedding in a JavaScript single-quoted string.

    Prevents injection when user-supplied values are interpolated into
    ``mongosh --eval`` commands.
    """
    s = s.replace("\\", "\\\\")
    s = s.replace("'", "\\'")
    s = s.replace("`", "\\`")
    s = s.replace("\n", "\\n")
    s = s.replace("\r", "\\r")
    s = s.replace("\t", "\\t")
    return s


def load_env() -> dict:
    """Load environment variables from .env and .env.secrets."""
    return _load_all_env(get_config().project_dir)


def get_mongo_password() -> str:
    """Get MongoDB root password from environment files."""
    env = load_env()
    password = env.get("MONGO_ROOT_PASSWORD") or env.get("MONGO_INITDB_ROOT_PASSWORD")
    if not password:
        raise MongoError("MONGO_ROOT_PASSWORD not found in .env or .env.secrets")
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
    raise MongoError("MongoDB container not running. Start it with: ./visp.py start mongo")


def mongosh_json(js_command: str, database: str = DATABASE) -> list | dict | None:
    """Execute a MongoDB command via mongosh and return parsed JSON result.

    The password is passed via stdin (``-p`` without a value) instead of the
    command line, so it is not visible in ``ps``. The "Enter password:" prompt
    goes to stderr, keeping stdout clean JSON.
    """
    if not re.fullmatch(r"[A-Za-z0-9_]+", database):
        raise MongoError(f"Invalid database name: {database!r}")
    password = get_mongo_password()
    container = find_mongo_container()
    result = subprocess.run(
        [
            "podman",
            "exec",
            "-i",
            container,
            "mongosh",
            "-u",
            "root",
            "-p",
            "--authenticationDatabase",
            "admin",
            database,
            "--eval",
            f"JSON.stringify({js_command})",
        ],
        input=f"{password}\n",
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise MongoError(f"mongosh failed (exit {result.returncode}): {result.stderr.strip()}")
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
