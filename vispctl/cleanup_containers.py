"""Session container cleanup utilities for VISP."""

from vispctl.runner import Runner


def cleanup_containers(mode: str = "stopped", yes: bool = False):
    """Stop/remove session containers by name pattern."""
    if mode not in ("all", "stopped", "running"):
        raise ValueError("mode must be one of all, stopped, running")

    runner = Runner()
    prefix_filters = ["hsapp-session-", "visp-session-"]
    container_ids = []

    if mode == "running":
        base_cmd = ["podman", "ps", "--format", "{{.ID}} {{.Names}}"]
    else:
        base_cmd = ["podman", "ps", "-a", "--format", "{{.ID}} {{.Names}}"]

    if mode == "stopped":
        base_cmd.extend(["--filter", "status=exited"])

    for prefix in prefix_filters:
        result = runner.run(base_cmd + ["--filter", f"name={prefix}"], capture=True, check=False)
        output = result.stdout.strip()
        for line in output.splitlines():
            if not line.strip():
                continue
            parts = line.split(maxsplit=1)
            if len(parts) < 2:
                continue
            cid, name = parts
            if "session-manager" in name:
                continue
            if cid not in container_ids:
                container_ids.append(cid)

    if not container_ids:
        return {
            "status": "ok",
            "message": "No session containers found to clean up.",
            "removed": 0,
        }

    if not yes:
        choice = input("Proceed with cleanup of session containers? (y/N): ").strip().lower()
        if choice not in ("y", "yes"):
            return {"status": "cancelled", "message": "No containers removed", "removed": 0}

    removed = 0
    for cid in container_ids:
        # stop if running
        if runner.run(["podman", "ps", "--filter", f"id={cid}", "--quiet"], capture=True, check=False).stdout.strip():
            runner.run(["podman", "stop", cid], check=False)
        runner.run(["podman", "rm", cid], check=False)
        removed += 1

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
