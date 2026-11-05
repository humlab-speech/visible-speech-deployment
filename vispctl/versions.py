"""Pinned versions of the external VISP components, stored in ``release.json``.

``release.json`` is the release manifest (see ``vispctl/release.py``). Its
``components`` section pins every repository under ``external/`` to a git
commit::

    "components": {
        "session-manager": {"ref": "03caa07…"},
        …
    }

This module is the part of the manifest that ``deploy`` works with: where each
repository comes from and how it is built live here, in
``DEFAULT_VERSIONS_CONFIG``; *which commit* goes into the release lives in the
file.

In memory every component is a dict with the defaults below plus ``version``
(the pinned SHA, or ``"latest"`` when unpinned) and ``locked_version`` (the pin,
or None). Those two keys are what the ``deploy`` code reads.
"""

import json
import os
from typing import Optional

# Where each external component comes from and how it is built.
DEFAULT_VERSIONS_CONFIG = {
    "webclient": {
        "url": "https://github.com/humlab-speech/webclient.git",
        "npm_install": True,
        "npm_build": True,
    },
    "container-agent": {
        "url": "https://github.com/humlab-speech/container-agent.git",
        "npm_install": True,
        "npm_build": False,
    },
    "wsrng-server": {
        "url": "https://github.com/humlab-speech/wsrng-server.git",
        "npm_install": True,
        "npm_build": False,
    },
    "session-manager": {
        "url": "https://github.com/humlab-speech/session-manager.git",
        "npm_install": True,
        "npm_build": False,
    },
    "emu-webapp-server": {
        "url": "https://github.com/humlab-speech/emu-webapp-server.git",
        "npm_install": True,
        "npm_build": False,
    },
    "artic": {
        "url": "https://github.com/humlab-speech/artic.git",
        "npm_install": True,
        "npm_build": True,
    },
    "WhisperVault": {
        "url": "https://github.com/humlab-speech/WhisperVault",
        "submodules": True,
    },
}

RELEASE_FILE = "release.json"


class ComponentConfig:
    """The component pins of ``release.json``, with the defaults merged in."""

    def __init__(self, filepath: str = RELEASE_FILE, defaults: Optional[dict] = None):
        self.filepath = filepath
        self.defaults = defaults or DEFAULT_VERSIONS_CONFIG
        self.config = self._load()

    def _read_file(self) -> dict:
        if not os.path.exists(self.filepath):
            return {}
        try:
            with open(self.filepath) as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError) as e:
            print(f"⚠  Error loading {self.filepath}: {e}")
            print("   Treating every component as unpinned")
            return {}

    def _load(self) -> dict:
        pins = self._read_file().get("components", {})
        config: dict[str, dict] = {}
        for name in list(self.defaults) + [n for n in pins if n not in self.defaults]:
            entry = dict(self.defaults.get(name, {}))
            ref = (pins.get(name) or {}).get("ref")
            entry["version"] = ref or "latest"
            entry["locked_version"] = ref
            config[name] = entry
        return config

    def save(self) -> None:
        """Write the pins back into ``release.json``, leaving its other sections as they are."""
        data = self._read_file()
        data["components"] = {
            name: {"ref": entry["version"] if entry.get("version") not in (None, "latest") else None}
            for name, entry in self.config.items()
        }
        with open(self.filepath, "w") as f:
            json.dump(data, f, indent=2)
            f.write("\n")

    def get_components(self) -> list[tuple[str, dict]]:
        """Get all components as (name, data) tuples."""
        return list(self.config.items())

    def get_component(self, name: str) -> Optional[dict]:
        """Get a specific component's configuration."""
        return self.config.get(name)

    def get_version(self, name: str) -> str:
        """The pinned SHA, or ``"latest"`` when the component is not pinned."""
        return self.config.get(name, {}).get("version", "latest")

    def get_locked_version(self, name: str) -> Optional[str]:
        """The pinned SHA, or None."""
        return self.config.get(name, {}).get("locked_version")

    def set_version(self, name: str, version: str) -> None:
        if name in self.config:
            self.config[name]["version"] = version

    def lock(self, name: str, commit_sha: str) -> bool:
        """Pin a component to *commit_sha*."""
        if name in self.config:
            self.config[name]["version"] = commit_sha
            self.config[name]["locked_version"] = commit_sha
            return True
        return False

    def unlock(self, name: str) -> bool:
        """Remove a component's pin (a release cannot be cut until it is pinned again)."""
        if name in self.config:
            self.config[name]["version"] = "latest"
            return True
        return False

    def rollback(self, name: str) -> bool:
        """Point ``version`` back at the pin (after the repository was checked out to it)."""
        if name in self.config:
            locked = self.config[name].get("locked_version")
            if locked:
                self.config[name]["version"] = locked
                return True
        return False

    def is_locked(self, name: str) -> bool:
        """True if the component is pinned."""
        return self.get_version(name) != "latest"
