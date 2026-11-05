"""``./visp.py upgrade``: move a server from its installed VISP version to the checkout's.

The installed version is recorded in ``mounts/visp-state.json``; the target is
``release.json`` of the deployment repository as checked out (the operator runs
``git fetch && git checkout v1.2.0`` first). Every release between the two may
bring an upgrade module, ``upgrades/<x.y.z>.py``, with whatever special work
that release needs — a database migration, moving files, rewriting a config,
re-keying a secret, or a manual task for the operator. The template with the
full step API is ``upgrades/_template.py``.

An upgrade runs in this order; the steps of several pending releases are
interleaved per phase, oldest release first::

    preflight    read-only checks of every pending release — nothing has changed yet
                 when one fails (built-in first: the repositories, and everything
                 converge's install would refuse: podman's network backend, and
                 networks that do not match their spec)
    pre_apply    the old version is still installed and running (built-in first:
                 a MongoDB backup)
    ── apply: check out the pinned components, refresh rebuilt base images,
       build every image, pull the third-party images
    post_build   the new images exist, the old containers still run
    ── converge: install the new quadlets / secrets / env files and restart
       what changed (``./visp.py install``)
    post_apply   the new version runs

Each completed step is recorded in the state file, so after a failure the
operator fixes the cause and runs ``./visp.py upgrade`` again: it resumes after
the last completed step. Several releases are applied with a single build and
converge, so a step must not rely on an intermediate release's images being
installed. When that cannot work, the release sets ``MIN_FROM`` and the
operator upgrades in two hops.
"""

from __future__ import annotations

import importlib.util
import sys
import traceback
from dataclasses import dataclass, field
from pathlib import Path
from types import ModuleType
from typing import Callable

from .exceptions import ConfigError, VispError
from .release import InstallState, Manifest, _now, check_manifest, deploy_commit, is_version, parse_version
from .runner import Colors, color

PHASES = ("preflight", "pre_apply", "post_build", "post_apply")
UPGRADES_DIR = "upgrades"


class UpgradeError(VispError):
    """A step failed or refused; the upgrade stops and can be resumed."""


# ── steps and release modules ─────────────────────────────────────────────────────


@dataclass
class Step:
    """One piece of work in a release's upgrade.

    Exactly one of ``run`` and ``manual`` is given:

    run
        ``run(ctx)`` does the work. Raise (or call ``ctx.fail(...)``) to stop the
        upgrade. It should be safe to run again after it failed halfway.
    manual
        Instructions for the operator. The upgrade prints them and waits until
        the operator confirms they are done — for work that cannot be scripted.

    done
        Optional ``done(ctx) -> bool``. When it returns True the step is skipped
        as already done (e.g. the data was migrated by hand, or the server never
        had the thing being migrated).
    confirm
        Ask the operator before running the step (``--yes`` answers for them).
    modes
        Run only in these deployment modes.
    """

    id: str
    description: str
    run: Callable[[UpgradeContext], None] | None = None
    phase: str = "pre_apply"
    manual: str | None = None
    done: Callable[[UpgradeContext], bool] | None = None
    confirm: bool = False
    modes: tuple[str, ...] = ("dev", "prod")

    def validate(self, where: str) -> None:
        if self.phase not in PHASES:
            raise ConfigError(f"{where}: step {self.id!r} has unknown phase {self.phase!r} (one of {PHASES})")
        if (self.run is None) == (self.manual is None):
            raise ConfigError(f"{where}: step {self.id!r} needs exactly one of run= and manual=")
        if not self.id or ":" in self.id:
            raise ConfigError(f"{where}: step id {self.id!r} must be non-empty and contain no ':'")


@dataclass
class ReleaseUpgrade:
    """The contents of one ``upgrades/<version>.py``."""

    version: str
    path: Path
    summary: str
    steps: list[Step]
    min_from: str | None = None


def _load_module(path: Path) -> ModuleType:
    name = "visp_upgrade_" + path.stem.replace(".", "_")
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ConfigError(f"Cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    try:
        spec.loader.exec_module(module)
    except Exception as e:
        raise ConfigError(f"{path}: failed to import: {e}") from e
    return module


def load_release_upgrade(path: Path) -> ReleaseUpgrade:
    module = _load_module(path)
    version = path.stem
    steps = list(getattr(module, "STEPS", []))
    seen: set[str] = set()
    for step in steps:
        if not isinstance(step, Step):
            raise ConfigError(f"{path}: STEPS must contain Step objects, got {type(step).__name__}")
        step.validate(str(path))
        if step.id in seen:
            raise ConfigError(f"{path}: duplicate step id {step.id!r}")
        seen.add(step.id)
    min_from = getattr(module, "MIN_FROM", None)
    if min_from is not None:
        parse_version(min_from)
    summary = getattr(module, "SUMMARY", None) or (module.__doc__ or "").strip().splitlines()[0:1]
    if isinstance(summary, list):
        summary = summary[0] if summary else ""
    return ReleaseUpgrade(version, path, str(summary), steps, min_from)


def discover_upgrades(project_dir: Path) -> list[ReleaseUpgrade]:
    """Every ``upgrades/<x.y.z>.py``, oldest first (other files, like ``_template.py``, are ignored)."""
    directory = Path(project_dir) / UPGRADES_DIR
    if not directory.is_dir():
        return []
    paths = [p for p in directory.glob("*.py") if is_version(p.stem)]
    return [load_release_upgrade(p) for p in sorted(paths, key=lambda p: parse_version(p.stem))]


def pending_upgrades(upgrades: list[ReleaseUpgrade], installed: str, target: str) -> list[ReleaseUpgrade]:
    lo, hi = parse_version(installed), parse_version(target)
    return [u for u in upgrades if lo < parse_version(u.version) <= hi]


# ── the context handed to every step ─────────────────────────────────────────────


class UpgradeContext:
    """What a step can use. Everything else in ``vispctl`` is importable too."""

    def __init__(
        self,
        project_dir: Path,
        runner,
        mode: str,
        state: InstallState,
        manifest: Manifest,
        from_version: str,
        assume_yes: bool = False,
        dry_run: bool = False,
        input_fn: Callable[[str], str] = input,
    ):
        self.project_dir = Path(project_dir)
        self.runner = runner
        self.mode = mode
        self.state = state
        self.manifest = manifest
        self.from_version = from_version
        self.to_version = manifest.version
        self.assume_yes = assume_yes
        self.dry_run = dry_run
        self.input_fn = input_fn
        self.release = from_version  # version of the release whose step is running
        self._env: dict[str, str] | None = None

    # -- data that survives a resume ---------------------------------------------

    @property
    def data(self) -> dict:
        """Per-release scratch space, saved in the state file (e.g. a backup path for a later step)."""
        progress = self.state.data.setdefault("in_progress", {})
        return progress.setdefault("data", {}).setdefault(self.release, {})

    def save(self) -> None:
        if not self.dry_run:
            self.state.save()

    # -- output and control -------------------------------------------------------

    def info(self, msg: str) -> None:
        print(f"    {msg}")

    def warn(self, msg: str) -> None:
        print(color(f"    ⚠ {msg}", Colors.YELLOW))

    def fail(self, msg: str):
        raise UpgradeError(msg)

    def confirm(self, question: str, default: bool = False) -> bool:
        if self.assume_yes:
            return True
        suffix = " [Y/n] " if default else " [y/N] "
        answer = self.input_fn(f"    {question}{suffix}").strip().lower()
        return default if not answer else answer in ("y", "yes")

    # -- configuration ------------------------------------------------------------

    @property
    def env(self) -> dict[str, str]:
        """``.env`` merged with ``.env.secrets``."""
        if self._env is None:
            from .env import load_env_file

            self._env = {}
            for name in (".env", ".env.secrets"):
                path = self.project_dir / name
                if path.exists():
                    self._env.update(load_env_file(path))
        return self._env

    # -- commands -----------------------------------------------------------------

    def run(self, cmd: list[str], check: bool = True, capture: bool = False, **kwargs):
        """Run a command; raises UpgradeError on failure when *check*."""
        result = self.runner.run(cmd, capture=capture, check=False, **kwargs)
        if check and result.returncode != 0:
            detail = f": {result.stderr.strip()}" if capture and result.stderr else ""
            raise UpgradeError(f"'{' '.join(cmd)}' exited with {result.returncode}{detail}")
        return result

    def podman(self, *args: str, check: bool = True, capture: bool = False):
        return self.run(["podman", *args], check=check, capture=capture)

    def exec(self, container: str, *cmd: str, check: bool = True, capture: bool = False):
        """``podman exec <container> <cmd…>``."""
        return self.podman("exec", container, *cmd, check=check, capture=capture)

    def is_running(self, service: str) -> bool:
        return self.runner.unit_is_active(service)

    def _services(self):
        from .service import get_runtime_services
        from .service_manager import ServiceManager

        return ServiceManager(self.runner, get_runtime_services(self.project_dir, include_disabled=True))

    def stop(self, *services: str) -> None:
        self._services().stop(list(services))

    def start(self, *services: str) -> None:
        self._services().start(list(services))

    def restart(self, *services: str) -> None:
        sm = self._services()
        sm.stop(list(services))
        sm.start(list(services))

    def backup_mongo(self, label: str = "upgrade") -> Path:
        """Dump MongoDB to ``backups/`` and return the archive path."""
        from datetime import datetime

        from .backup import BackupManager

        out_dir = self.project_dir / "backups"
        out_dir.mkdir(exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        out = out_dir / f"visp_{label}_{self.from_version}_{stamp}.tar.gz"
        path = BackupManager(self.runner, self.project_dir).backup(output=out)
        if path is None:
            raise UpgradeError("MongoDB backup failed")
        return Path(path)


# ── built-in steps ─────────────────────────────────────────────────────────────────


def _check_repos(ctx: UpgradeContext) -> None:
    from .git_repo import GitRepository

    problems = [f"release.json: {e}" for e in check_manifest(ctx.manifest, ctx.project_dir)]
    for name in ctx.manifest.components:
        repo = GitRepository(str(ctx.project_dir / "external" / name))
        if repo.is_git_repo() and repo.is_dirty():
            problems.append(f"external/{name} has uncommitted changes (commit, stash or discard them)")
    dirty = ctx.run(["git", "status", "--porcelain", "--untracked-files=no"], capture=True, cwd=ctx.project_dir)
    if dirty.stdout.strip():
        problems.append("the deployment repository has uncommitted changes to tracked files:\n" + dirty.stdout)
    if problems:
        ctx.fail("Cannot upgrade:\n      - " + "\n      - ".join(problems))
    ctx.info("release.json is complete, no repository has local changes")


def _check_webclient_config(ctx: UpgradeContext) -> None:
    from .build import validate_build_config

    config = ctx.env.get("WEBCLIENT_BUILD_CONFIG")
    if not config:
        if ctx.mode == "prod":
            ctx.fail(
                "WEBCLIENT_BUILD_CONFIG is not set in .env. The webclient is built per site — set it to this"
                " server's Angular configuration (e.g. 'visp' or 'visp-demo') so the upgrade can rebuild it."
            )
        ctx.info("WEBCLIENT_BUILD_CONFIG not set — dev mode uses the default build configuration")
        return
    validate_build_config(config)
    ctx.info(f"webclient will be built with --config {config}")


def _check_network_backend(ctx: UpgradeContext) -> None:
    """Converge runs install, which stops to offer a netavark migration; find out now."""
    from .network import NetworkManager

    is_netavark, backend = NetworkManager(ctx.runner).check_netavark()
    if is_netavark:
        ctx.info("podman uses the netavark network backend")
        return
    if backend == "unknown":
        ctx.fail("Cannot tell podman's network backend ('podman info' failed). Is podman working?")
    ctx.fail(
        f"Podman uses the '{backend}' network backend; VISP requires netavark (CNI's DNS stalls for ~20 s).\n"
        "      Switching runs 'podman system reset', which deletes every container, image and network\n"
        "      (mounts/ is kept), so it is never done inside an upgrade. Back up (./visp.py backup),\n"
        "      migrate deliberately ('./visp.py install' offers it), then run the upgrade again."
    )


def _check_networks(ctx: UpgradeContext) -> None:
    """Converge runs install, which refuses a network that does not match its spec; find out now."""
    from .network import NetworkManager

    problems = NetworkManager(ctx.runner).network_problems()
    if not problems:
        ctx.info("every existing VISP network matches its spec (missing ones are created by install)")
        return
    names = [p.split(":", 1)[0] for p in problems if p.startswith("systemd-")]
    fix = ""
    if names:
        rm = " ".join(names)
        fix = (
            "\n    Recreate them before upgrading (VISP is down meanwhile):\n"
            f"      ./visp.py down && podman network rm {rm} && ./visp.py network ensure && ./visp.py up"
        )
    ctx.fail("A VISP network does not match its spec:\n      - " + "\n      - ".join(problems) + fix)


def _backup(ctx: UpgradeContext) -> None:
    if not ctx.is_running("mongo"):
        ctx.fail("mongo is not running, so it cannot be backed up. Start it, or pass --no-backup.")
    path = ctx.backup_mongo("pre-upgrade")
    ctx.data["backup"] = str(path)
    ctx.info(f"backup: {path}")


def _checkout(ctx: UpgradeContext) -> None:
    from .deploy import DeployManager

    if not DeployManager(str(ctx.project_dir), runner=ctx.runner).update_components(release=True):
        ctx.fail("Checking out the pinned components failed (see above)")


def _buildable(ctx: UpgradeContext) -> list[str]:
    from .build import BUILD_CONFIGS, NODE_BUILD_CONFIGS, resolve_build_order
    from .service import get_disabled_optional_services

    disabled = set(get_disabled_optional_services(ctx.project_dir))
    requested = [n for n in list(BUILD_CONFIGS) + list(NODE_BUILD_CONFIGS) if n not in disabled]
    ordered, _ = resolve_build_order(requested, BUILD_CONFIGS, NODE_BUILD_CONFIGS)
    return ordered


def _build(ctx: UpgradeContext) -> None:
    from .build import BUILD_CONFIGS, NODE_BUILD_CONFIGS, BuildManager, refresh_base_images

    ordered = _buildable(ctx)
    # Like './visp.py build': pull the bases the registry has rebuilt (security
    # fixes under the same tag) or that are missing, or the images re-bake stale ones.
    ctx.info("refreshing base images")
    refresh_base_images(ctx.runner, BUILD_CONFIGS, ordered)
    ctx.info(f"building: {', '.join(ordered)}")
    bm = BuildManager(ctx.runner, build_configs=BUILD_CONFIGS, node_configs=NODE_BUILD_CONFIGS)
    # The webclient picks up WEBCLIENT_BUILD_CONFIG from .env by itself.
    results = bm.run_builds(ordered)
    if results["failed"] or results["skipped"]:
        ctx.fail(f"Build failed: {', '.join(results['failed'] + results['skipped'])}")


def _pull(ctx: UpgradeContext) -> None:
    from .service import get_runtime_services

    wanted = {s.name for s in get_runtime_services(ctx.project_dir)}
    for service, ref in sorted(ctx.manifest.images.items()):
        if service not in wanted:
            continue
        ctx.info(f"pulling {ref}")
        ctx.podman("pull", ref)


def _converge(ctx: UpgradeContext) -> None:
    from .config import get_config
    from .install import run_install
    from .quadlets import render_quadlet_template
    from .service import DEFAULT_SERVICES, get_disabled_optional_services, resolve_services

    cfg = get_config()
    run_install(
        project_dir=ctx.project_dir,
        systemd_dir=cfg.systemd_dir,
        runner=ctx.runner,
        mode=ctx.mode,
        service_arg="all",
        services=resolve_services("all", ctx.project_dir),
        all_services=DEFAULT_SERVICES,
        disabled_optional=get_disabled_optional_services(ctx.project_dir),
        render_fn=render_quadlet_template,
        force=False,
    )
    # install restarts what was running; a step may have stopped services (e.g.
    # for a database migration), and after an upgrade every enabled service runs.
    from .service import get_runtime_services
    from .service_manager import ServiceManager

    ServiceManager(ctx.runner, get_runtime_services(ctx.project_dir)).start("all")


@dataclass
class PlannedStep:
    key: str  # unique in the state file: "<release>:<id>", or "upgrade:<id>" for built-ins
    release: str
    step: Step
    builtin: bool = False


@dataclass
class Plan:
    from_version: str
    to_version: str
    releases: list[ReleaseUpgrade]
    steps: list[PlannedStep] = field(default_factory=list)


def build_plan(
    installed: str,
    manifest: Manifest,
    upgrades: list[ReleaseUpgrade],
    mode: str,
    backup: bool = True,
    build: bool = True,
) -> Plan:
    """Order every step of the upgrade from *installed* to the manifest's version."""
    target = manifest.version
    releases = pending_upgrades(upgrades, installed, target)
    for rel in releases:
        if rel.min_from and parse_version(installed) < parse_version(rel.min_from):
            raise UpgradeError(
                f"{rel.version} can only be upgraded to from {rel.min_from} or later (installed: {installed}).\n"
                f"  Upgrade in two hops: git checkout v{rel.min_from} && ./visp.py upgrade, then come back"
                f" to this version and run ./visp.py upgrade again."
            )

    plan = Plan(installed, target, releases)
    up = "upgrade"

    def builtin(id_: str, description: str, run, phase: str) -> None:
        plan.steps.append(PlannedStep(f"{up}:{id_}", up, Step(id_, description, run=run, phase=phase), True))

    def module_steps(phase: str) -> None:
        for rel in releases:
            for step in rel.steps:
                if step.phase == phase and mode in step.modes:
                    plan.steps.append(PlannedStep(f"{rel.version}:{step.id}", rel.version, step))

    builtin("check-repos", "release.json is complete and no repository has local changes", _check_repos, "preflight")
    builtin("check-network-backend", "podman uses the netavark network backend", _check_network_backend, "preflight")
    builtin("check-networks", "existing VISP networks match their spec", _check_networks, "preflight")
    if build:
        builtin("check-webclient-config", "the webclient build configuration is known", _check_webclient_config,
                "preflight")  # fmt: skip
    module_steps("preflight")
    if backup:
        builtin("backup", "back up MongoDB", _backup, "pre_apply")
    module_steps("pre_apply")
    builtin("checkout", "check out the components pinned in release.json", _checkout, "apply")
    if build:
        builtin("build", "build every VISP image", _build, "apply")
    builtin("pull", "pull the third-party images in release.json", _pull, "apply")
    module_steps("post_build")
    builtin(
        "converge",
        "install quadlets, secrets and env files; restart what changed, start the rest",
        _converge,
        "converge",
    )
    module_steps("post_apply")
    return plan


# ── running an upgrade ─────────────────────────────────────────────────────────────


def print_plan(plan: Plan, done: set[str]) -> None:
    print(color(f"=== VISP upgrade {plan.from_version} → {plan.to_version} ===", Colors.CYAN))
    if plan.releases:
        for rel in plan.releases:
            print(f"  {color(rel.version, Colors.GREEN)}  {rel.summary}")
    else:
        print("  No release in between brings upgrade steps — this rebuilds and restarts only.")
    print()
    phase = None
    for i, ps in enumerate(plan.steps, 1):
        if ps.step.phase != phase:
            phase = ps.step.phase
            print(color(f"  {phase}", Colors.MAGENTA))
        mark = color("✓", Colors.GREEN) if ps.key in done else " "
        kind = " (manual)" if ps.step.manual else ""
        print(f"   {mark} {i:2}. [{ps.release}] {ps.step.id}{kind} — {ps.step.description}")
    print()


def _run_manual(ctx: UpgradeContext, step: Step) -> None:
    print(color("    Manual step — do this, then confirm:", Colors.YELLOW))
    for line in step.manual.strip().splitlines():
        print(f"      {line}")
    if not sys.stdin.isatty() and ctx.input_fn is input:
        ctx.fail("A manual step needs an interactive terminal")
    while True:
        answer = ctx.input_fn("    Type 'done' when finished, or 'abort': ").strip().lower()
        if answer == "done":
            return
        if answer == "abort":
            ctx.fail("Aborted by the operator at a manual step")


def execute(plan: Plan, ctx: UpgradeContext) -> None:
    """Run every step not yet recorded as done; record each as it completes."""
    state = ctx.state
    progress = state.data.get("in_progress") or {}
    if not ctx.dry_run:
        progress.setdefault("from", plan.from_version)
        progress.setdefault("started", _now())
        progress["to"] = plan.to_version
        progress.setdefault("done", [])
        progress.setdefault("data", {})
        state.data["in_progress"] = progress
        state.save()
    done: list[str] = progress.get("done", [])

    for i, ps in enumerate(plan.steps, 1):
        step = ps.step
        if ps.key in done:
            continue
        if ctx.dry_run and step.phase != "preflight":
            continue
        ctx.release = ps.release
        print(color(f"── {i}/{len(plan.steps)} [{ps.release}] {step.id}: {step.description}", Colors.CYAN))
        if step.done is not None and step.done(ctx):
            ctx.info("already done — skipped")
        elif step.manual:
            _run_manual(ctx, step)
        else:
            if step.confirm and not ctx.confirm("Run this step?"):
                ctx.fail(f"Step {ps.key} was declined")
            step.run(ctx)
        if not ctx.dry_run:
            done.append(ps.key)
            state.save()
        print()


def run_upgrade(
    project_dir: Path,
    runner,
    mode: str,
    assume_yes: bool = False,
    dry_run: bool = False,
    backup: bool = True,
    build: bool = True,
    input_fn: Callable[[str], str] = input,
) -> int:
    manifest = Manifest.load(project_dir)
    state = InstallState.load(project_dir)
    target = manifest.version
    parse_version(target)

    if not state.version:
        from .release import has_existing_data

        print(color("This server has no recorded VISP version (mounts/visp-state.json).", Colors.YELLOW))
        if has_existing_data(project_dir):
            print("  It holds data, so it was installed before versions were recorded. Record which")
            print("  release it corresponds to (no upgrade steps run), then run upgrade again:")
            print("    ./visp.py version mark <x.y.z>")
        else:
            print("  It looks like a fresh install — './visp.py install' records the version.")
        return 1

    installed = state.version
    resuming = state.in_progress
    if parse_version(installed) > parse_version(target):
        print(color(f"Installed {installed} is newer than this checkout ({target}).", Colors.RED))
        print("  Downgrades are not supported: check out the installed release, or restore a backup")
        print("  taken before the upgrade (mounts/ and the MongoDB dump).")
        return 1
    if installed == target and not resuming:
        print(color(f"VISP {installed} is installed — nothing to upgrade.", Colors.GREEN))
        return 0

    upgrades = discover_upgrades(project_dir)
    plan = build_plan(installed, manifest, upgrades, mode, backup=backup, build=build)
    done = set((resuming or {}).get("done", []))
    if resuming:
        print(
            color(f"Resuming the upgrade started {resuming.get('started')} ({len(done)} step(s) done).", Colors.YELLOW)
        )
        print()
    print_plan(plan, done)

    ctx = UpgradeContext(project_dir, runner, mode, state, manifest, installed, assume_yes, dry_run, input_fn)
    if dry_run:
        print(color("Dry run: running the preflight checks only.", Colors.YELLOW))
        print()
    elif not ctx.confirm(f"Upgrade VISP {installed} → {target} ({mode} mode)?"):
        print("Cancelled.")
        return 1
    print()

    try:
        execute(plan, ctx)
    except (UpgradeError, VispError) as e:
        _report_failure(ctx, str(e), dry_run)
        return 1
    except KeyboardInterrupt:
        _report_failure(ctx, "Interrupted", dry_run)
        raise
    except Exception:
        _report_failure(ctx, traceback.format_exc(), dry_run)
        return 1

    if dry_run:
        print(color("Preflight checks passed. Run without --dry-run to upgrade.", Colors.GREEN))
        return 0
    state.record(target, "upgrade", deploy_commit(project_dir))
    print(color(f"✓ VISP {target} is installed.", Colors.GREEN))
    return 0


def _report_failure(ctx: UpgradeContext, message: str, dry_run: bool) -> None:
    print()
    print(color(f"✗ {message.rstrip()}", Colors.RED))
    print()
    if dry_run:
        return
    ctx.state.save()
    print("The upgrade stopped; the steps already completed are recorded in mounts/visp-state.json.")
    print("Fix the cause and run './visp.py upgrade' again to resume.")
    backup = ((ctx.state.in_progress or {}).get("data", {}).get("upgrade", {}) or {}).get("backup")
    if backup:
        print(f"Pre-upgrade MongoDB backup: {backup}")
