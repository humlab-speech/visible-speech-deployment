"""Version management for VISP components using versions.json."""

import json
import os
import shutil
from datetime import datetime
from typing import Optional

# Default configuration for components
DEFAULT_VERSIONS_CONFIG = {
    "webclient": {
        "version": "latest",
        "url": "https://github.com/humlab-speech/webclient.git",
        "npm_install": True,
        "npm_build": True,
    },
    "container-agent": {
        "version": "latest",
        "url": "https://github.com/humlab-speech/container-agent.git",
        "npm_install": True,
        "npm_build": False,
    },
    "wsrng-server": {
        "version": "latest",
        "url": "https://github.com/humlab-speech/wsrng-server.git",
        "npm_install": True,
        "npm_build": False,
    },
    "session-manager": {
        "version": "latest",
        "url": "https://github.com/humlab-speech/session-manager.git",
        "npm_install": True,
        "npm_build": False,
    },
    "emu-webapp-server": {
        "version": "latest",
        "url": "https://github.com/humlab-speech/emu-webapp-server.git",
        "npm_install": True,
        "npm_build": False,
    },
    "EMU-webApp": {
        "version": "latest",
        "url": "https://github.com/humlab-speech/EMU-webApp.git",
        "npm_install": True,
        "npm_build": True,
    },
    "WhisperVault": {
        "version": "latest",
        "url": "https://github.com/humlab-speech/WhisperVault",
        "submodules": True,
    },
}


class ComponentConfig:
    """
    Manages the versions.json configuration file.
    Encapsulates loading, saving, and manipulation of component versions.
    """

    def __init__(self, filepath: str = "versions.json", defaults: Optional[dict] = None):
        """
        Initialize configuration manager.

        Args:
            filepath: Path to versions.json file
            defaults: Default configuration dict (uses DEFAULT_VERSIONS_CONFIG if None)
        """
        self.filepath = filepath
        self.defaults = defaults or DEFAULT_VERSIONS_CONFIG
        self.config = self._load()

    def _load(self) -> dict:
        """Load configuration from file, falling back to defaults."""
        if os.path.exists(self.filepath):
            try:
                with open(self.filepath, "r") as f:
                    data = json.load(f)

                # Extract components from the wrapper structure
                config = data.get("components", data)

                # Merge with defaults to ensure all required fields exist
                for component, default_data in self.defaults.items():
                    if component not in config:
                        config[component] = default_data.copy()
                    else:
                        # Ensure all default fields exist
                        for key, value in default_data.items():
                            if key not in config[component]:
                                config[component][key] = value
                return config
            except (json.JSONDecodeError, IOError) as e:
                print(f"⚠️  Error loading {self.filepath}: {e}")
                print("   Using default configuration")
                return self.defaults.copy()
        return self.defaults.copy()

    def save(self) -> None:
        """
        Save configuration to file with backup.
        Creates a timestamped backup before overwriting.
        """
        # Create backup if file exists
        if os.path.exists(self.filepath):
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            backup_path = f"{self.filepath}.backup_{timestamp}"
            shutil.copy2(self.filepath, backup_path)

        # Write new config with wrapper structure
        output = {
            "_comment": (
                "Version can be: 'latest' (tracks main/master), "
                "a git commit SHA, or a git tag. "
                "Use 'locked_version' to record current stable version for rollback."
            ),
            "components": self.config,
        }
        with open(self.filepath, "w") as f:
            json.dump(output, f, indent=2)

    def get_components(self) -> list[tuple[str, dict]]:
        """Get all components as (name, data) tuples."""
        return list(self.config.items())

    def get_component(self, name: str) -> Optional[dict]:
        """Get a specific component's configuration."""
        return self.config.get(name)

    def get_version(self, name: str) -> str:
        """Get the active version for a component."""
        component = self.config.get(name, {})
        return component.get("version", "latest")

    def get_locked_version(self, name: str) -> Optional[str]:
        """Get the locked version for a component."""
        component = self.config.get(name, {})
        return component.get("locked_version")

    def set_version(self, name: str, version: str) -> None:
        """Set the active version for a component."""
        if name in self.config:
            self.config[name]["version"] = version

    def set_locked_version(self, name: str, version: str) -> None:
        """Set the locked version for a component."""
        if name in self.config:
            self.config[name]["locked_version"] = version

    def lock(self, name: str, commit_sha: str) -> bool:
        """
        Lock a component to a specific commit.
        Sets both version and locked_version to the commit SHA.
        """
        if name in self.config:
            self.config[name]["version"] = commit_sha
            self.config[name]["locked_version"] = commit_sha
            return True
        return False

    def unlock(self, name: str) -> bool:
        """
        Unlock a component to track latest.
        Sets version to 'latest' but preserves locked_version for rollback.
        """
        if name in self.config:
            self.config[name]["version"] = "latest"
            # Preserve locked_version for rollback
            return True
        return False

    def rollback(self, name: str) -> bool:
        """
        Rollback a component to its locked version.
        Sets version to match locked_version.
        """
        if name in self.config:
            locked = self.config[name].get("locked_version")
            if locked:
                self.config[name]["version"] = locked
                return True
        return False

    def is_locked(self, name: str) -> bool:
        """Check if a component is locked (version != 'latest')."""
        return self.get_version(name) != "latest"
