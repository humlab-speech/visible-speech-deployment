"""``./visp.py release create``: make a release out of what is checked out right now.

One command instead of new → pin → sync → check → commit → tag:

1. pin every repository under ``external/`` to its checked-out commit (or, with
   ``--remote``, to the tip of origin's default branch),
2. copy the image and vendored pins from ``quadlets/`` and the Dockerfiles,
3. refuse anything a server could not reproduce: uncommitted changes, commits
   that are on no remote branch, a missing repository, a version that is not
   newer than the last release tag,
4. show what changed since the previous release and ask,
5. commit ``release.json`` (and ``upgrades/``) and tag ``v<version>``.

Nothing is pushed — the closing message says what to push.
"""

from __future__ import annotations

import copy
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .exceptions import ConfigError
from .git_repo import GitRepository
from .release import Manifest, check_manifest, is_version, parse_version, sync_manifest
from .runner import Colors, color
from .upgrade import UPGRADES_DIR, discover_upgrades
from .versions import DEFAULT_VERSIONS_CONFIG


@dataclass
class ComponentHead:
    name: str
    sha: str | None = None
    ref: str = "HEAD"
    branch: str | None = None
    subject: str = ""
    problem: str | None = None
    unpushed: bool = False


def _git(project_dir: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=project_dir, capture_output=True, text=True, check=check)


def release_tags(project_dir: Path) -> list[str]:
    """Versions of the ``v<x.y.z>`` tags in the deployment repository, oldest first."""
    out = _git(project_dir, "tag", "--list", "v*", check=False).stdout.split()
    return sorted((t[1:] for t in out if is_version(t[1:])), key=parse_version)


def component_heads(external_dir: Path, remote: bool = False, fetch: bool = True) -> list[ComponentHead]:
    """What each external repository would be pinned to, and why it can't be."""
    heads = []
    for name, defaults in DEFAULT_VERSIONS_CONFIG.items():
        head = ComponentHead(name)
        heads.append(head)
        repo = GitRepository(str(external_dir / name), defaults.get("url"))
        if not repo.is_git_repo():
            head.problem = f"external/{name} is not cloned (./visp.py deploy update)"
            continue
        if fetch:
            try:
                repo.fetch(quiet=True)
            except subprocess.CalledProcessError:
                head.problem = f"git fetch failed in external/{name}"
                continue
        if remote:
            branch = repo.get_default_branch() or next(
                (b for b in ("main", "master") if repo.has_remote_branch(b)), None
            )
            if branch is None:
                head.problem = "cannot determine origin's default branch"
                continue
            head.ref, head.branch = f"origin/{branch}", branch
        else:
            head.branch = repo.get_current_branch()
            if head.branch == "HEAD":
                head.branch = None
            if repo.is_dirty():
                head.problem = "has uncommitted changes — commit them first (a release pins commits)"
        info = repo.get_commit_info(head.ref)
        if not info:
            head.problem = head.problem or f"cannot read {head.ref}"
            continue
        head.sha, head.subject = info["sha"], info["subject"]
        contains = repo.run_git(["branch", "-r", "--contains", head.sha], check=False)
        head.unpushed = not contains.stdout.strip()
    return heads


def _deploy_repo_problems(project_dir: Path) -> list[str]:
    """Tracked changes outside release.json and upgrades/ would not be part of the release."""
    out = _git(project_dir, "status", "--porcelain", "--untracked-files=no").stdout.splitlines()
    stray = [line[3:] for line in out if not (line[3:] == "release.json" or line[3:].startswith(f"{UPGRADES_DIR}/"))]
    if stray:
        return [
            "the deployment repository has uncommitted changes (commit or stash them):\n      " + "\n      ".join(stray)
        ]
    return []


def _next_patch(version: str) -> str:
    major, minor, patch = parse_version(version)
    return f"{major}.{minor}.{patch + 1}"


def _prompt(input_fn: Callable[[str], str], question: str) -> str | None:
    """Ask; None when there is no one to answer (EOF)."""
    try:
        return input_fn(question).strip()
    except EOFError:
        print()
        return None


def _ask(input_fn: Callable[[str], str], question: str) -> bool:
    return (_prompt(input_fn, f"{question} [y/N] ") or "").lower() in ("y", "yes")


def create_release(
    project_dir: Path,
    version: str | None = None,
    remote: bool = False,
    allow_unpushed: bool = False,
    commit: bool = True,
    assume_yes: bool = False,
    fetch: bool = True,
    input_fn: Callable[[str], str] = input,
) -> int:
    project_dir = Path(project_dir)
    manifest = Manifest.load(project_dir)
    previous = Manifest(manifest.path, copy.deepcopy(manifest.data))
    tags = release_tags(project_dir)
    last_tag = tags[-1] if tags else None

    # ── which version ───────────────────────────────────────────────────────────
    if version is None:
        # The manifest's version is still free if it was never tagged (e.g. the 1.0.0 baseline).
        free = manifest.version if is_version(manifest.version) and manifest.version not in tags else None
        suggestion = free or _next_patch(last_tag or manifest.version)
        if assume_yes:
            version = suggestion
        else:
            answer = _prompt(input_fn, f"Version for this release [{suggestion}]: ")
            if answer is None:
                print("Cancelled — no version given (pass it as an argument, or -y for the suggestion).")
                return 1
            version = answer.lstrip("v") or suggestion
    parse_version(version)
    if version in tags:
        raise ConfigError(f"v{version} is already tagged — pick a newer version")
    if last_tag and parse_version(version) <= parse_version(last_tag):
        raise ConfigError(f"{version} is not newer than the latest release v{last_tag}")

    # ── gather and validate ───────────────────────────────────────────────────────
    print(color(f"Reading {'origin default branches' if remote else 'the checked-out commits'}...", Colors.CYAN))
    heads = component_heads(project_dir / "external", remote=remote, fetch=fetch)
    problems = _deploy_repo_problems(project_dir) if commit else []
    problems += [f"{h.name}: {h.problem}" for h in heads if h.problem]
    unpushed = [h for h in heads if h.sha and h.unpushed]
    if unpushed and not allow_unpushed:
        problems += [
            f"{h.name}: {h.sha[:8]} is on no remote branch — push it first (servers fetch from origin),"
            " or pass --allow-unpushed"
            for h in unpushed
        ]

    manifest.version = version
    for h in heads:
        if h.sha:
            manifest.components[h.name] = {"ref": h.sha}
    try:
        sync_manifest(manifest, project_dir)
    except ConfigError as e:
        problems.append(str(e))
    problems += check_manifest(manifest, project_dir) if not problems else []
    try:
        upgrades = discover_upgrades(project_dir)
    except ConfigError as e:
        problems.append(str(e))
        upgrades = []
    problems += [
        f"{u.path.relative_to(project_dir)} is newer than {version}"
        for u in upgrades
        if parse_version(u.version) > parse_version(version)
    ]

    if problems:
        print(color(f"✗ Cannot create VISP {version}:", Colors.RED))
        for p in problems:
            print(f"  - {p}")
        return 1

    # ── show and confirm ──────────────────────────────────────────────────────────
    _print_summary(project_dir, previous, manifest, heads, upgrades, last_tag)
    if unpushed:
        print(
            color(
                f"⚠ Unpushed commits pinned ({', '.join(h.name for h in unpushed)}) — push before deploying.",
                Colors.YELLOW,
            )
        )
    branch = _git(project_dir, "rev-parse", "--abbrev-ref", "HEAD", check=False).stdout.strip()
    if commit and branch not in ("master", "main"):
        print(
            color(
                f"⚠ The deployment repository is on '{branch}', not master — the tag will point there.", Colors.YELLOW
            )
        )
    action = f"Commit release.json and tag v{version}" if commit else "Write release.json (no commit, no tag)"
    if not assume_yes and not _ask(input_fn, f"{action}?"):
        print("Cancelled — nothing was changed.")
        return 1

    # ── write, commit, tag ─────────────────────────────────────────────────────────
    manifest.save()
    if not commit:
        print(color(f"✓ release.json is now VISP {version}. Commit it and tag v{version} when ready.", Colors.GREEN))
        return 0

    _git(project_dir, "add", "release.json")
    if (project_dir / UPGRADES_DIR).is_dir():
        _git(project_dir, "add", UPGRADES_DIR)
    if _git(project_dir, "diff", "--cached", "--quiet", check=False).returncode != 0:
        body = "\n".join(f"- {h.name} {h.sha[:12]} {h.subject[:60]}" for h in heads if h.sha)
        result = subprocess.run(
            ["git", "commit", "-q", "-m", f"chore(release): VISP {version}", "-m", body], cwd=project_dir
        )
        if result.returncode != 0:
            print(color("✗ git commit failed (see above). release.json is written but not committed.", Colors.RED))
            return 1
    _git(project_dir, "tag", "-a", f"v{version}", "-m", f"VISP {version}")
    sha = _git(project_dir, "rev-parse", "--short", "HEAD").stdout.strip()
    print(color(f"✓ VISP {version} created: tag v{version} → {sha}", Colors.GREEN))
    print("  Nothing was pushed. To publish it:")
    print(f"    git push origin {branch} v{version}")
    return 0


def _print_summary(
    project_dir: Path,
    previous: Manifest,
    manifest: Manifest,
    heads: list[ComponentHead],
    upgrades: list,
    last_tag: str | None,
) -> None:
    since = f"v{last_tag}" if last_tag else f"release.json {previous.version}"
    print()
    print(color(f"=== VISP {manifest.version}  (changes since {since}) ===", Colors.CYAN))
    print(color("Components", Colors.MAGENTA))
    for h in heads:
        old = (previous.components.get(h.name) or {}).get("ref")
        where = h.branch or "detached"
        if old == h.sha:
            print(f"  {h.name:20} {h.sha[:8]}  unchanged")
        else:
            change = f"{old[:8] if old else '(none)'} → {color(h.sha[:8], Colors.GREEN)}"
            print(f"  {h.name:20} {change}  [{where}] {h.subject[:50]}")
    image_changes = [
        (s, previous.images.get(s), manifest.images.get(s))
        for s in sorted(set(previous.images) | set(manifest.images))
        if previous.images.get(s) != manifest.images.get(s)
    ]
    print(color("Third-party images", Colors.MAGENTA))
    for service, old, new in image_changes:
        print(f"  {service:20} {old or '(none)'} → {color(new or '(removed)', Colors.GREEN)}")
    if not image_changes:
        print("  unchanged")
    for name, entry in manifest.vendored.items():
        if previous.vendored.get(name) != entry:
            old = (previous.vendored.get(name) or {}).get("ref", "(none)")
            print(color("Vendored", Colors.MAGENTA))
            print(f"  {name:20} {old[:8]} → {color(entry['ref'][:8], Colors.GREEN)}")
    module = next((u for u in upgrades if u.version == manifest.version), None)
    print(color("Upgrade steps", Colors.MAGENTA))
    if module:
        print(f"  {module.path.relative_to(project_dir)}: {len(module.steps)} step(s)")
    else:
        print(f"  none (servers rebuild and restart only; add {UPGRADES_DIR}/{manifest.version}.py for special work)")
    print()
