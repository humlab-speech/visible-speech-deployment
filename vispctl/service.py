"""Service dataclass and helpers."""

from __future__ import annotations
from dataclasses import dataclass
from typing import Literal


@dataclass
class Service:
    name: str
    type: Literal["container", "network"]
    file: str
    description: str = ""


def load_default_services() -> list[Service]:
    # Minimal subset; main script keeps canonical list until full migration
    return [
        Service("visp-net", "network", "visp-net.network"),
        Service("mongo", "container", "mongo.container"),
        Service("traefik", "container", "traefik.container"),
        Service("session-manager", "container", "session-manager.container"),
    ]
