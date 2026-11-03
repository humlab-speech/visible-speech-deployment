"""Session container cleanup utilities for VISP."""

from vispctl.runner import Runner


def cleanup_containers(mode: str = "stopped", yes: bool = False):
    """Stop/remove session containers by name pattern."""
    if mode not in ("all", "stopped", "running"):
        raise ValueError("mode must be one of all, stopped, running")

    runner = Runner()
    prefix_filters = ["hsapp-session-", "visp-session-"]
    containers: list[tuple[str, str]] = []  # (id, name)

    if mode == "running":
        base_cmd = ["podman", "ps", "--format", "{{.ID}} {{.Names}}"]
    else:
        base_cmd = ["podman", "ps", "-a", "--format", "{{.ID}} {{.Names}}"]

    if mode == "stopped":
        base_cmd.extend(["--filter", "status=exited"])

    for prefix in prefix_filters:
        # podman's name filter is an UNANCHORED regex: 'visp-session-' also
        # matches notvisp-session-... and my-visp-session-backup. Ask for an
        # anchored regex AND re-check the literal prefix in Python — deletion
        # must never hinge on regex semantics.
        result = runner.run(base_cmd + ["--filter", f"name=^{prefix}"], capture=True, check=False)
        if result.returncode != 0:
            return {
                "status": "error",
                "message": f"podman ps failed ({result.returncode}); nothing was removed.",
                "removed": 0,
            }
        output = result.stdout.strip()
        seen_ids = {c[0] for c in containers}
        for line in output.splitlines():
            if not line.strip():
                continue
            parts = line.split(maxsplit=1)
            if len(parts) < 2:
                continue
            cid, name = parts
            if not name.startswith(prefix):
                continue
            # The guard is about the infrastructure container, whose name is
            # exactly session-manager — a substring check would also spare
            # (or target) user sessions by accident.
            if name == "session-manager":
                continue
            if cid not in seen_ids:
                seen_ids.add(cid)
                containers.append((cid, name))

    if not containers:
        return {
            "status": "ok",
            "message": "No session containers found to clean up.",
            "removed": 0,
        }

    if not yes:
        print(f"Would remove {len(containers)} session container(s):")
        for cid, name in containers:
            print(f"  - {name} ({cid[:12]})")
        try:
            choice = input("Proceed with cleanup? (y/N): ").strip().lower()
        except EOFError:
            return {
                "status": "cancelled",
                "message": "No confirmation received (non-interactive). Re-run with -y to confirm.",
                "removed": 0,
            }
        if choice not in ("y", "yes"):
            return {"status": "cancelled", "message": "No containers removed", "removed": 0}

    removed = 0
    failures = []
    skipped_running = []
    for cid, name in containers:
        # A '-proxy' sidecar of a session whose main container is RUNNING must
        # survive: the session is alive even if the sidecar itself is exited,
        # and removing it silently breaks the session's uploads (round-2 F4).
        if name.endswith("-proxy"):
            main = name[: -len("-proxy")]
            ps = runner.run(
                ["podman", "ps", "--filter", f"name=^{main}$", "--quiet"],
                capture=True,
                check=False,
            )
            if getattr(ps, "stdout", "").strip():
                skipped_running.append(name)
                continue
        # stop if running
        if runner.run(["podman", "ps", "--filter", f"id={cid}", "--quiet"], capture=True, check=False).stdout.strip():
            runner.run(["podman", "stop", cid], check=False)
        rm = runner.run(["podman", "rm", cid], check=False)
        rc = getattr(rm, "returncode", 0)
        if rc == 0:
            removed += 1
        else:
            failures.append(name)

    if skipped_running:
        print("  Skipped proxy sidecars of running sessions: " + ", ".join(skipped_running))

    if failures:
        return {
            "status": "partial",
            "message": (f"Removed {removed} of {len(containers)}; failed to remove: " + ", ".join(failures)),
            "removed": removed,
        }

    return {
        "status": "ok",
        "message": f"Removed {removed} session container(s)",
        "removed": removed,
    }


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Cleanup VISP session containers")
    parser.add_argument("--mode", choices=["all", "stopped", "running"], default="stopped")
    parser.add_argument("-y", "--yes", action="store_true", help="Do not prompt confirmation")
    args = parser.parse_args()

    result = cleanup_containers(mode=args.mode, yes=args.yes)
    print(result)
