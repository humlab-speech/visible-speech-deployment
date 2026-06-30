"""VispConfig — centralized configuration singleton for VISP deployment tool.

Consolidates project_dir, systemd_dir, runner, build configs, and derived
paths that were previously duplicated via Path(__file__).parent.parent in
every module.

Usage:
    from vispctl.config import get_config, init_config

    # In visp.py main():
    init_config(runner=runner)

    # In any vispctl module:
    cfg = get_config()
    project_dir = cfg.project_dir
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .runner import Runner
    from .service import Service


__all__ = ["VispConfig", "get_config", "init_config"]


@dataclass(frozen=True)
class VispConfig:
    """Immutable configuration for the VISP deployment tool.

    Attributes:
        project_dir: Root of the visible-speech-deployment repository.
        systemd_dir: User-level systemd directory for Podman Quadlets.
        runner: Shared command runner instance.
        build_configs: Container image build configuration dict.
        node_configs: Node.js project build configuration dict.
        all_buildable: Combined list of all buildable service names.
        network_services: List of network-type Service objects.
    """

    project_dir: Path
    systemd_dir: Path
    runner: "Runner | None" = None
    build_configs: dict[str, dict] = field(default_factory=dict)
    node_configs: dict[str, dict] = field(default_factory=dict)
    all_buildable: list[str] = field(default_factory=list)
    network_services: list["Service"] = field(default_factory=list)


_config: VispConfig | None = None


def _default_project_dir() -> Path:
    """Derive project directory from this module's location."""
    return Path(__file__).parent.parent.resolve()


def _default_systemd_dir() -> Path:
    """Derive default systemd Quadlets directory."""
    return Path.home() / ".config" / "containers" / "systemd"


def init_config(
    runner: "Runner | None" = None,
    project_dir: Path | None = None,
    systemd_dir: Path | None = None,
    build_configs: dict[str, dict] | None = None,
    node_configs: dict[str, dict] | None = None,
    all_buildable: list[str] | None = None,
    network_services: list["Service"] | None = None,
) -> VispConfig:
    """Initialize (or re-initialize) the global VispConfig singleton.

    Call once at startup from visp.py main().  Subsequent calls replace
    the singleton, allowing tests to override configuration.

    Args:
        runner: Shared command runner (optional for CLI entry point).
        project_dir: Repository root directory.
        systemd_dir: Systemd user directory for Quadlet units.
        build_configs: Container image build configuration.
        node_configs: Node.js project build configuration.
        all_buildable: All buildable service names.
        network_services: Network-type services.

    Returns:
        The initialized VispConfig instance.
    """
    global _config

    _config = VispConfig(
        project_dir=project_dir or _default_project_dir(),
        systemd_dir=systemd_dir or _default_systemd_dir(),
        runner=runner,
        build_configs=build_configs or {},
        node_configs=node_configs or {},
        all_buildable=all_buildable or [],
        network_services=network_services or [],
    )
    return _config


def get_config() -> VispConfig:
    """Return the current VispConfig singleton.

    If init_config() has not been called yet, returns a default instance
    with project_dir and systemd_dir auto-derived.

    Returns:
        The VispConfig singleton.
    """
    global _config
    if _config is None:
        return init_config()
    return _config
