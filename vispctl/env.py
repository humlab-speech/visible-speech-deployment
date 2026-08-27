"""Shared .env file parsing for VISP management tools."""

from pathlib import Path


def load_env_file(path: Path) -> dict[str, str]:
    """Parse a single .env file into a dict.

    Skips blank lines and comments (lines starting with #).
    Splits on the first '=' and strips whitespace and surrounding quotes from values.
    """
    env: dict[str, str] = {}
    if not path.is_file():
        return env
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" not in line:
                continue
            key, _, value = line.partition("=")
            env[key.strip()] = value.strip().strip('"').strip("'")
    return env


def load_all_env(project_dir: Path) -> dict[str, str]:
    """Load .env and .env.secrets, with .env.secrets overriding .env."""
    env: dict[str, str] = {}
    env.update(load_env_file(project_dir / ".env"))
    env.update(load_env_file(project_dir / ".env.secrets"))
    return env
