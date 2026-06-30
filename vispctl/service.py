"""Service dataclass, canonical service list, and service helpers."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from pathlib import Path


@dataclass
class Service:
    name: str
    type: Literal["container", "network"]
    file: str
    description: str = ""
    dev_only: bool = False


# Canonical service list — order matters for startup.
DEFAULT_SERVICES: list[Service] = [
    # Networks first
    Service("visp-net", "network", "visp-net.network"),
    Service("octra-net", "network", "octra-net.network"),
    # Then containers in dependency order
    Service("mongo", "container", "mongo.container"),
    Service("mongo-express", "container", "mongo-express.container", dev_only=True),
    Service("matomo-db", "container", "matomo-db.container"),
    Service("matomo", "container", "matomo.container"),
    Service("whisperx", "container", "whisperx.container"),
    Service("local-idp", "container", "local-idp.container", dev_only=True),
    Service("wsrng-server", "container", "wsrng-server.container"),
    Service("podman-socket-proxy", "container", "podman-socket-proxy.container"),
    Service("session-manager", "container", "session-manager.container"),
    Service("artic", "container", "artic.container"),
    Service("emu-webapp-server", "container", "emu-webapp-server.container"),
    Service("octra", "container", "octra.container"),
    Service("apache", "container", "apache.container"),
]

# Optional services can be disabled via .env flags (defaults to enabled).
# Key: service name, Value: .env variable that controls it.
OPTIONAL_SERVICE_ENV_FLAGS: dict[str, str] = {
    "whisperx": "WHISPERX_ENABLED",
    "local-idp": "LOCAL_IDP_ENABLED",
}


# ---------------------------------------------------------------------------
# Runtime service resolution helpers
# ---------------------------------------------------------------------------


def get_disabled_optional_services(project_dir: Path | None = None) -> dict[str, str]:
    """Return {service_name: env_var} for optional services that are disabled in .env."""
    from pathlib import Path

    from .runner import load_env_vars, parse_env_bool

    if project_dir is None:
        project_dir = Path(__file__).parent.parent
    env_vars = load_env_vars(Path(project_dir) / ".env")
    return {
        svc: var
        for svc, var in OPTIONAL_SERVICE_ENV_FLAGS.items()
        if not parse_env_bool(env_vars.get(var), default=True)
    }


def get_runtime_services(
    project_dir: Path | None = None,
    include_disabled: bool = False,
) -> list[Service]:
    """Return the filtered service list for runtime orchestration.

    Excludes dev-only services when not in dev mode, and disabled optional
    services (unless include_disabled=True).
    """
    from pathlib import Path

    from .quadlets import get_current_mode

    if project_dir is None:
        project_dir = Path(__file__).parent.parent
    mode = get_current_mode()
    services = [s for s in DEFAULT_SERVICES if not (s.dev_only and mode != "dev")]
    if include_disabled:
        return services
    disabled = get_disabled_optional_services(Path(project_dir))
    return [s for s in services if s.name not in disabled]


def resolve_services(
    service_arg: str,
    project_dir: Path | None = None,
    include_disabled: bool = False,
) -> list[Service]:
    """Resolve 'all' / service name → list[Service], raising on error."""
    from pathlib import Path

    from .exceptions import ServiceError

    if project_dir is None:
        project_dir = Path(__file__).parent.parent
    available = get_runtime_services(Path(project_dir), include_disabled=include_disabled)

    if service_arg == "all":
        return available

    svc = next((s for s in available if s.name == service_arg), None)
    if svc:
        return [svc]

    # Helpful message for optional services that are disabled in .env.
    if not include_disabled:
        disabled = get_disabled_optional_services(Path(project_dir))
        if service_arg in disabled:
            env_var = disabled[service_arg]
            raise ServiceError(
                f"Service '{service_arg}' is disabled ({env_var}=false in .env). "
                f"Enable it by setting {env_var}=true in .env"
            )

    raise ServiceError(
        f"Unknown service: {service_arg}. Available: {', '.join(s.name for s in available)}"
    )
