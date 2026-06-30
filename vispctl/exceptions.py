"""VISP exception hierarchy.

All VISP-specific errors derive from VispError, caught by visp.py main()
for structured CLI output instead of raw tracebacks.
"""

__all__ = [
    "BuildError",
    "ConfigError",
    "DeployError",
    "InstallationError",
    "MongoError",
    "ServiceError",
    "UserError",
    "VispError",
]


class VispError(Exception):
    """Base exception for all VISP errors."""


class ConfigError(VispError):
    """Missing or invalid configuration (env files, secrets, .env flags)."""


class MongoError(VispError):
    """MongoDB connection, authentication, or query failure."""


class ServiceError(VispError):
    """Unknown, disabled, or unresolved service."""


class UserError(VispError):
    """User lookup failure or invalid user input (privilege, username)."""


class BuildError(VispError):
    """Image or Node.js project build failure."""


class DeployError(VispError):
    """Deploy update, lock, unlock, or rollback failure."""


class InstallationError(VispError):
    """Install-phase failure (netavark, networks, quadlets)."""
