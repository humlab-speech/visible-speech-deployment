"""VISP 1.2.0 — emu-webapp-server is renamed annotation-server and also serves TRATT.

The server Artic talks to (the EMU-webApp websocket protocol) now also serves
TRATT, so it is named for what it does. Its service, container, image, quadlet,
external/ checkout and mounts/ directory all change name.

The old checkout and mounts directory are moved, not recreated, so local
branches and the service's .env (and prod logs) carry over. The converge then
removes the old quadlet (it is not in the service registry any more) and
installs annotation-server in its place.
"""

from vispctl.upgrade import Step

OLD = "emu-webapp-server"
NEW = "annotation-server"


def _pairs(ctx):
    """(old, new) paths of everything named after the service on disk."""
    return [(ctx.project_dir / base / OLD, ctx.project_dir / base / NEW) for base in ("external", "mounts")]


def _check_rename(ctx):
    for old, new in _pairs(ctx):
        if old.exists() and new.exists():
            ctx.fail(
                f"both {old} and {new} exist. Keep the one that holds this server's state "
                f"(normally {old.name}), remove the other, and run the upgrade again"
            )
        if old.exists():
            ctx.info(f"{old.relative_to(ctx.project_dir)} → {new.relative_to(ctx.project_dir)}")


def _rename(ctx):
    # The registry no longer knows the old unit (ctx.stop would skip it), and it
    # must not hold the checkout, the .env or, in dev, port 17890 while they move.
    ctx.runner.systemctl("stop", f"{OLD}.service")
    for old, new in _pairs(ctx):
        if old.exists() and not new.exists():
            old.rename(new)
            ctx.info(f"moved {old.relative_to(ctx.project_dir)} → {new.relative_to(ctx.project_dir)}")

    checkout = ctx.project_dir / "external" / NEW
    if (checkout / ".git").exists():
        url = ctx.run(["git", "-C", str(checkout), "remote", "get-url", "origin"], capture=True, check=False)
        current = url.stdout.strip() if url.returncode == 0 else ""
        if OLD in current:
            ctx.run(["git", "-C", str(checkout), "remote", "set-url", "origin", current.replace(OLD, NEW)])
            ctx.info(f"origin of external/{NEW} now points at {current.replace(OLD, NEW)}")


def _renamed(ctx):
    return not any(old.exists() for old, _ in _pairs(ctx))


STEPS = [
    Step(
        "check-annotation-server-rename",
        f"{OLD} can be renamed {NEW} (no directory of either name is in the way)",
        run=_check_rename,
        phase="preflight",
    ),
    Step(
        "rename-annotation-server",
        f"stop {OLD}, move external/{OLD} and mounts/{OLD} to {NEW}",
        run=_rename,
        done=_renamed,
    ),
]
