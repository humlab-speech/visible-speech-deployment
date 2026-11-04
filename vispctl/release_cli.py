"""CLI for VISP versions: ``version``, ``release`` and ``upgrade``.

./visp.py version                 installed version, checkout version, what is pending
./visp.py version --manifest      … plus everything release.json pins
./visp.py version mark 1.0.0      record the installed version without running anything

./visp.py release create [1.1.0]  pin everything as checked out, commit, tag — the usual way
./visp.py release pin --all [--remote]   pin external repos (HEAD, or origin's default branch)
./visp.py release sync            copy image / vendored pins from quadlets and Dockerfiles
./visp.py release new 1.1.0 [--steps]    start the next release (and its upgrade module)
./visp.py release check           is release.json complete and consistent?

./visp.py upgrade [--dry-run] [-y]       installed version → checkout version
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .config import get_config
from .exceptions import ConfigError
from .release import (
    InstallState,
    Manifest,
    check_manifest,
    deploy_commit,
    has_existing_data,
    parse_version,
    sync_manifest,
)
from .runner import Colors, color
from .upgrade import UPGRADES_DIR, discover_upgrades, pending_upgrades

TEMPLATE = Path(UPGRADES_DIR) / "_template.py"


# ── version ─────────────────────────────────────────────────────────────────────


def cmd_version(args) -> None:
    if getattr(args, "version_command", None) == "mark":
        sys.exit(_cmd_version_mark(args))

    project_dir = get_config().project_dir
    manifest = Manifest.load(project_dir)
    state = InstallState.load(project_dir)

    print(f"Checkout:   VISP {color(manifest.version, Colors.GREEN)}  (release.json)")
    if state.version:
        since = state.data.get("since", "?")
        print(f"Installed:  VISP {color(state.version, Colors.GREEN)}  (since {since})")
    elif has_existing_data(project_dir):
        print(color("Installed:  unknown — record it with './visp.py version mark <x.y.z>'", Colors.YELLOW))
    else:
        print("Installed:  nothing yet (fresh server)")

    if state.in_progress:
        p = state.in_progress
        print(color(f"⚠  Upgrade {p.get('from')} → {p.get('to')} did not finish — run './visp.py upgrade'", Colors.RED))
    elif state.version and state.version != manifest.version:
        if parse_version(state.version) < parse_version(manifest.version):
            pending = pending_upgrades(discover_upgrades(project_dir), state.version, manifest.version)
            note = f", {len(pending)} with upgrade steps" if pending else ""
            print(color(f"→  Upgrade pending{note}: './visp.py upgrade --dry-run' shows the plan", Colors.YELLOW))
        else:
            print(color("⚠  The checkout is older than the installed version", Colors.RED))

    if getattr(args, "manifest", False):
        _print_manifest(manifest)


def _print_manifest(manifest: Manifest) -> None:
    print()
    print(color("Components (external/)", Colors.CYAN))
    for name, entry in manifest.components.items():
        print(f"  {name:20} {(entry or {}).get('ref') or color('not pinned', Colors.RED)}")
    print(color("Third-party images", Colors.CYAN))
    for service, ref in sorted(manifest.images.items()):
        print(f"  {service:20} {ref}")
    if manifest.vendored:
        print(color("Vendored", Colors.CYAN))
        for name, entry in sorted(manifest.vendored.items()):
            print(f"  {name:20} {entry.get('ref')}  ({entry.get('repo')}, pinned in {entry.get('pinned_in')})")


def _cmd_version_mark(args) -> int:
    project_dir = get_config().project_dir
    parse_version(args.version)
    state = InstallState.load(project_dir)
    if state.in_progress and not args.yes:
        print(color("An upgrade is in progress — marking now discards its record of completed steps.", Colors.RED))
    print(f"Record VISP {args.version} as installed on this server (currently: {state.version or 'unrecorded'}).")
    print("No upgrade steps run: use this only when the server already is at that release,")
    print("e.g. a server installed before versions were recorded.")
    if not args.yes and input("Continue? [y/N] ").strip().lower() not in ("y", "yes"):
        print("Cancelled.")
        return 1
    state.record(args.version, "mark", deploy_commit(project_dir))
    print(color(f"✓ Recorded VISP {args.version} in {state.path}", Colors.GREEN))
    return 0


# ── release ─────────────────────────────────────────────────────────────────────


def cmd_release(args) -> None:
    handler = {
        "pin": _cmd_release_pin,
        "sync": _cmd_release_sync,
        "new": _cmd_release_new,
        "check": _cmd_release_check,
        "create": _cmd_release_create,
    }[args.release_command]
    sys.exit(handler(args))


def _cmd_release_pin(args) -> int:
    from .deploy import DeployManager

    cfg = get_config()
    dm = DeployManager(str(cfg.project_dir), runner=cfg.runner)
    return 0 if dm.lock_components(args.components, lock_all=args.all, remote=args.remote) else 1


def _cmd_release_sync(args) -> int:  # noqa: ARG001
    project_dir = get_config().project_dir
    manifest = Manifest.load(project_dir)
    changes = sync_manifest(manifest, project_dir)
    manifest.save()
    if changes:
        for change in changes:
            print(f"  {change}")
        print(color(f"✓ Updated {len(changes)} pin(s) in release.json", Colors.GREEN))
    else:
        print(color("release.json already matches the quadlets and Dockerfiles.", Colors.GREEN))
    return 0


def _cmd_release_new(args) -> int:
    project_dir = get_config().project_dir
    manifest = Manifest.load(project_dir)
    if parse_version(args.version) <= parse_version(manifest.version):
        raise ConfigError(f"{args.version} is not newer than the current release {manifest.version}")
    previous = manifest.version
    manifest.version = args.version
    sync_manifest(manifest, project_dir)
    manifest.save()
    print(f"release.json: {previous} → {color(args.version, Colors.GREEN)}")

    if args.steps:
        target = project_dir / UPGRADES_DIR / f"{args.version}.py"
        if target.exists():
            print(f"{target.relative_to(project_dir)} already exists")
        else:
            text = (project_dir / TEMPLATE).read_text()
            target.write_text(text.replace("x.y.z", args.version))
            print(f"Created {target.relative_to(project_dir)} — add the release's upgrade steps there.")
    print()
    print("Next: pin the components that change ('./visp.py release pin <name>'), then")
    print("'./visp.py release check', commit, and tag the commit: git tag -a v" + args.version)
    return 0


def _cmd_release_create(args) -> int:
    from .release_create import create_release

    return create_release(
        get_config().project_dir,
        version=args.version,
        remote=args.remote,
        allow_unpushed=args.allow_unpushed,
        commit=not args.no_commit,
        assume_yes=args.yes,
    )


def _cmd_release_check(args) -> int:  # noqa: ARG001
    project_dir = get_config().project_dir
    manifest = Manifest.load(project_dir)
    errors = check_manifest(manifest, project_dir)
    try:
        upgrades = discover_upgrades(project_dir)
    except ConfigError as e:
        errors.append(str(e))
        upgrades = []
    for rel in upgrades:
        if parse_version(rel.version) > parse_version(manifest.version):
            errors.append(f"{rel.path.relative_to(project_dir)} is newer than release.json ({manifest.version})")
    if errors:
        print(color(f"✗ release.json ({manifest.version}) is not releasable:", Colors.RED))
        for e in errors:
            print(f"  - {e}")
        return 1
    print(color(f"✓ VISP {manifest.version}: release.json is complete and consistent", Colors.GREEN))
    print(f"  {len(upgrades)} upgrade module(s) load cleanly")
    return 0


# ── upgrade ─────────────────────────────────────────────────────────────────────


def cmd_upgrade(args) -> None:
    from .quadlets import get_current_mode
    from .upgrade import run_upgrade

    cfg = get_config()
    rc = run_upgrade(
        cfg.project_dir,
        cfg.runner,
        get_current_mode(),
        assume_yes=args.yes,
        dry_run=args.dry_run,
        backup=not args.no_backup,
        build=not args.no_build,
    )
    sys.exit(rc)


# ── parsers ─────────────────────────────────────────────────────────────────────


def add_release_parsers(subparsers: argparse._SubParsersAction) -> None:
    p = subparsers.add_parser("version", aliases=["v"], help="Show the installed and checked-out VISP version")
    p.set_defaults(func=cmd_version)
    p.add_argument("--manifest", action="store_true", help="Also list everything release.json pins")
    sub = p.add_subparsers(dest="version_command")
    mark = sub.add_parser("mark", help="Record the installed version without running upgrade steps")
    mark.add_argument("version", help="x.y.z")
    mark.add_argument("-y", "--yes", action="store_true", help="Do not ask for confirmation")
    mark.set_defaults(func=cmd_version)

    p = subparsers.add_parser("release", help="Maintain release.json: pin, sync, new, check")
    p.set_defaults(func=cmd_release)
    sub = p.add_subparsers(dest="release_command", required=True)
    create = sub.add_parser(
        "create",
        help="Create a release from what is checked out now: pin, sync, check, commit, tag",
        description="Pin every external repo to its checked-out commit, copy the image pins from the quadlets,"
        " validate, show what changed since the last release, then commit release.json and tag v<version>."
        " Nothing is pushed.",
    )
    create.add_argument("version", nargs="?", help="x.y.z (default: asked, suggesting the next patch version)")
    create.add_argument(
        "--remote", action="store_true", help="Pin origin's default-branch tips instead of the checkouts"
    )
    create.add_argument(
        "--allow-unpushed", action="store_true", help="Allow pinning commits that are on no remote branch yet"
    )
    create.add_argument("--no-commit", action="store_true", help="Only write release.json; do not commit or tag")
    create.add_argument(
        "-y", "--yes", action="store_true", help="Do not ask (uses the suggested version if none given)"
    )
    pin = sub.add_parser("pin", help="Pin external components to a commit")
    pin.add_argument("components", nargs="*", help="Components to pin (or --all)")
    pin.add_argument("--all", action="store_true", help="Pin every component")
    pin.add_argument(
        "--remote", action="store_true", help="Pin the tip of origin's default branch instead of the local HEAD"
    )
    sub.add_parser("sync", help="Copy image and vendored pins from quadlets/ and Dockerfiles into release.json")
    new = sub.add_parser("new", help="Start the next release: bump the version, sync, optionally scaffold steps")
    new.add_argument("version", help="x.y.z")
    new.add_argument("--steps", action="store_true", help=f"Create {UPGRADES_DIR}/<version>.py from the template")
    sub.add_parser("check", help="Validate release.json and the upgrade modules")

    p = subparsers.add_parser("upgrade", help="Upgrade this server from its installed version to the checkout's")
    p.set_defaults(func=cmd_upgrade)
    p.add_argument("--dry-run", action="store_true", help="Show the plan and run only the read-only preflight checks")
    p.add_argument("-y", "--yes", action="store_true", help="Do not ask for confirmation (manual steps still ask)")
    p.add_argument("--no-backup", action="store_true", help="Skip the built-in MongoDB backup")
    p.add_argument("--no-build", action="store_true", help="Skip building images (they must already be current)")
