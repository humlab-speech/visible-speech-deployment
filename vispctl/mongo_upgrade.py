"""Move MongoDB's data directory up one major version at a time.

MongoDB upgrades only one major at a time, and only from a data directory whose
featureCompatibilityVersion (FCV) is the previous major: 6.0 → 7.0 needs FCV
6.0, then FCV 7.0 before 8.0. An upgrade step does each hop in a throwaway
container of that version on ``mounts/mongo/data`` — with no network and no
auth, while the ``mongo`` service is stopped — so a server can skip VISP
releases without ever having had the intermediate image installed.

Used by ``upgrades/*.py``; the live instance (``mongo`` service) is read with
``vispctl.mongo.mongosh_json``.
"""

from __future__ import annotations

import time
from pathlib import Path

HOP_CONTAINER = "visp-mongo-upgrade"
READY_TIMEOUT = 180


def major(version: str) -> tuple[int, int]:
    """``"7.0.45"`` / ``"7.0"`` → ``(7, 0)``."""
    parts = str(version).split(".")
    return int(parts[0]), int(parts[1]) if len(parts) > 1 else 0


def live_versions() -> tuple[str, str]:
    """``(server version, FCV)`` of the running ``mongo`` service."""
    from .mongo import mongosh_json

    info = mongosh_json(
        "{version: db.version(), fcv: db.adminCommand({getParameter: 1, featureCompatibilityVersion: 1})"
        ".featureCompatibilityVersion.version}",
        database="admin",
    )
    if not isinstance(info, dict) or "fcv" not in info:
        raise RuntimeError(f"Could not read the MongoDB version: {info!r}")
    return str(info["version"]), str(info["fcv"])


def set_live_fcv(fcv: str) -> None:
    """Set the FCV on the running ``mongo`` service (for the hop *from* it)."""
    from .mongo import mongosh_json

    confirm = ", confirm: true" if major(fcv) >= (7, 0) else ""
    result = mongosh_json(f"db.adminCommand({{setFeatureCompatibilityVersion: '{fcv}'{confirm}}})", database="admin")
    if not isinstance(result, dict) or result.get("ok") != 1:
        raise RuntimeError(f"setFeatureCompatibilityVersion {fcv} failed: {result!r}")


def _eval(ctx, js: str, check: bool = True):
    return ctx.podman("exec", HOP_CONTAINER, "mongosh", "--quiet", "--eval", js, check=check, capture=True)


def _remove(ctx) -> None:
    ctx.podman("rm", "-f", "-t", "60", HOP_CONTAINER, check=False, capture=True)


def hop(ctx, image: str, data_dir: Path) -> str:
    """Start *image* on *data_dir*, raise the FCV to its major, shut it down cleanly.

    Returns the FCV the data directory has afterwards. Safe to repeat: the FCV
    is only raised when it is lower.
    """
    target = ".".join(map(str, major(image.rsplit(":", 1)[1])))
    _remove(ctx)
    ctx.info(f"starting {image} on {data_dir} (no network, no auth)")
    ctx.podman(
        "run", "-d", "--name", HOP_CONTAINER, "--network=none",
        "-v", f"{data_dir}:/data/db:Z",
        image, "mongod", "--bind_ip", "127.0.0.1",
        capture=True,
    )  # fmt: skip
    try:
        deadline = time.monotonic() + READY_TIMEOUT
        while _eval(ctx, "db.adminCommand({ping: 1}).ok", check=False).stdout.strip() != "1":
            state = ctx.podman("inspect", "-f", "{{.State.Running}}", HOP_CONTAINER, check=False, capture=True)
            if state.stdout.strip() != "true" or time.monotonic() > deadline:
                logs = ctx.podman("logs", "--tail", "30", HOP_CONTAINER, check=False, capture=True)
                ctx.fail(f"{image} did not come up on the data directory:\n{logs.stdout}{logs.stderr}")
            time.sleep(2)

        fcv_js = (
            "db.adminCommand({getParameter: 1, featureCompatibilityVersion: 1}).featureCompatibilityVersion.version"
        )
        current = _eval(ctx, fcv_js).stdout.strip()
        if major(current) < major(target):
            confirm = ", confirm: true" if major(target) >= (7, 0) else ""
            ctx.info(f"featureCompatibilityVersion {current} → {target}")
            out = _eval(ctx, f"db.adminCommand({{setFeatureCompatibilityVersion: '{target}'{confirm}}}).ok")
            if out.stdout.strip() != "1":
                ctx.fail(f"setFeatureCompatibilityVersion {target} failed: {out.stdout}{out.stderr}")
            current = _eval(ctx, fcv_js).stdout.strip()
        else:
            ctx.info(f"featureCompatibilityVersion is already {current}")
        # Record before shutting down: a rerun must not start an older binary
        # on data this hop has already raised.
        ctx.data["mongo_fcv"] = current
        ctx.save()
        ctx.info("shutting down cleanly")
        ctx.podman("stop", "-t", "120", HOP_CONTAINER, capture=True)
        return current
    finally:
        _remove(ctx)
