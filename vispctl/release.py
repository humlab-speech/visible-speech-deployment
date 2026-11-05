"""VISP releases: the version manifest and the installed-version record.

A VISP release is a version number (``x.y.z``) plus everything that goes into
it. The deployment repository at a release tag (``v1.2.0``) already fixes the
quadlets, our own Dockerfiles and their base images; ``release.json`` adds what
the repository alone does not:

``visp``
    The release version.
``components``
    The git commit of every repository under ``external/``. ``deploy update``
    checks these out in prod mode.
``images``
    The third-party image of every service, as its quadlets pin it. Kept here
    so the manifest is readable on its own; ``release check`` (and the test
    suite) fail when it disagrees with ``quadlets/``.
``vendored``
    Software pinned somewhere else, e.g. TRATT inside ``docker/tratt/Dockerfile``,
    with the same consistency check.

What a server has installed is recorded in ``mounts/visp-state.json`` — next
to the data it describes, so restoring a backup of ``mounts/`` restores the
matching version too. ``./visp.py upgrade`` (``vispctl/upgrade.py``) moves a
server from the version recorded there to the version in ``release.json``.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from .exceptions import ConfigError

RELEASE_FILE = "release.json"
STATE_FILE = Path("mounts") / "visp-state.json"
MODES = ("dev", "prod")

_VERSION_RE = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$")
_SHA_RE = re.compile(r"^[0-9a-f]{40}$")

MANIFEST_COMMENT = (
    "VISP release manifest. 'components' pins each external repository to a commit "
    "('./visp.py release pin'); 'images' and 'vendored' mirror the pins in quadlets/ and "
    "Dockerfiles ('./visp.py release sync'). './visp.py release check' validates it."
)


# ── version numbers ─────────────────────────────────────────────────────────────


def parse_version(text: str) -> tuple[int, int, int]:
    """Parse ``x.y.z`` into a comparable tuple; raise ConfigError otherwise."""
    m = _VERSION_RE.match(str(text).strip())
    if not m:
        raise ConfigError(f"Not a VISP version (expected x.y.z): {text!r}")
    return int(m[1]), int(m[2]), int(m[3])


def is_version(text: str) -> bool:
    return bool(_VERSION_RE.match(str(text).strip()))


# ── the manifest ────────────────────────────────────────────────────────────────


@dataclass
class Manifest:
    path: Path
    data: dict

    @classmethod
    def load(cls, project_dir: Path) -> Manifest:
        path = Path(project_dir) / RELEASE_FILE
        if not path.exists():
            raise ConfigError(f"{path} not found — this checkout has no release manifest.")
        try:
            data = json.loads(path.read_text())
        except json.JSONDecodeError as e:
            raise ConfigError(f"{path}: invalid JSON: {e}") from e
        return cls(path, data)

    @property
    def version(self) -> str:
        return str(self.data.get("visp", ""))

    @version.setter
    def version(self, value: str) -> None:
        parse_version(value)
        self.data["visp"] = value

    @property
    def components(self) -> dict[str, dict]:
        return self.data.setdefault("components", {})

    @property
    def images(self) -> dict[str, str]:
        return self.data.setdefault("images", {})

    @property
    def vendored(self) -> dict[str, dict]:
        return self.data.setdefault("vendored", {})

    def save(self) -> None:
        ordered = {"_comment": self.data.get("_comment", MANIFEST_COMMENT), "visp": self.version}
        for key in ("components", "images", "vendored"):
            ordered[key] = self.data.get(key, {})
        for key, value in self.data.items():
            ordered.setdefault(key, value)
        self.path.write_text(json.dumps(ordered, indent=2) + "\n")


# ── what the repository itself pins ──────────────────────────────────────────────


def quadlet_images(project_dir: Path) -> tuple[dict[str, str], list[str]]:
    """Third-party ``Image=`` pins per service, across all modes.

    Returns ``(images, problems)``; a problem is a service whose modes pin
    different images (the manifest can only describe one).
    """
    from .image_update import find_image_pins

    project_dir = Path(project_dir)
    services: set[str] = set()
    for mode in MODES:
        services.update(p.stem for p in (project_dir / "quadlets" / mode).glob("*.container"))
    if (project_dir / "quadlets" / "placeholder").is_dir():
        services.add("placeholder")

    images: dict[str, str] = {}
    problems: list[str] = []
    for service in sorted(services):
        refs = {pin.ref for pin in find_image_pins(project_dir, service)}
        refs = {r for r in refs if not r.startswith("localhost/")}
        if len(refs) > 1:
            problems.append(f"{service}: modes pin different images ({', '.join(sorted(refs))})")
        elif refs:
            images[service] = refs.pop()
    return images, problems


# Software that a Dockerfile clones and checks out at a fixed commit.
VENDORED_SOURCES = {
    "tratt": "docker/tratt/Dockerfile",
}

_CLONE_RE = re.compile(r"git clone\b[^\n]*?\s(https://\S+?)(?:\s|$)")
_CHECKOUT_RE = re.compile(r"git checkout\s+([0-9a-f]{40})\b")


def vendored_pins(project_dir: Path) -> tuple[dict[str, dict], list[str]]:
    """Read the vendored pins out of the Dockerfiles in ``VENDORED_SOURCES``."""
    pins: dict[str, dict] = {}
    problems: list[str] = []
    for name, rel in VENDORED_SOURCES.items():
        path = Path(project_dir) / rel
        if not path.exists():
            problems.append(f"{name}: {rel} not found")
            continue
        text = path.read_text()
        url, sha = _CLONE_RE.search(text), _CHECKOUT_RE.search(text)
        if not (url and sha):
            problems.append(f"{name}: no 'git clone <url>' + 'git checkout <40-char sha>' in {rel}")
            continue
        pins[name] = {"repo": url[1], "ref": sha[1], "pinned_in": rel}
    return pins, problems


def sync_manifest(manifest: Manifest, project_dir: Path) -> list[str]:
    """Copy the image and vendored pins from the repository into *manifest*.

    Returns a list of human-readable changes (empty when already in sync).
    """
    changes: list[str] = []
    images, problems = quadlet_images(project_dir)
    vendored, vproblems = vendored_pins(project_dir)
    if problems or vproblems:
        raise ConfigError("Cannot sync release.json:\n  " + "\n  ".join(problems + vproblems))
    for key, new in (("images", images), ("vendored", vendored)):
        old = manifest.data.get(key, {})
        for name in sorted(set(old) | set(new)):
            if old.get(name) != new.get(name):
                changes.append(f"{key}.{name}: {_short(old.get(name))} → {_short(new.get(name))}")
        manifest.data[key] = new
    return changes


def _short(value) -> str:
    if value is None:
        return "(none)"
    if isinstance(value, dict):
        return str(value.get("ref", ""))[:12]
    return str(value)


def check_manifest(manifest: Manifest, project_dir: Path) -> list[str]:
    """Everything that makes *manifest* unfit to be released; empty when it is fine."""
    from .versions import DEFAULT_VERSIONS_CONFIG

    errors: list[str] = []
    if not is_version(manifest.version):
        errors.append(f"visp: {manifest.version!r} is not a version (x.y.z)")

    for name in DEFAULT_VERSIONS_CONFIG:
        ref = (manifest.components.get(name) or {}).get("ref")
        if not ref:
            errors.append(f"components.{name}: not pinned (./visp.py release pin {name})")
        elif not _SHA_RE.match(ref):
            errors.append(f"components.{name}: {ref!r} is not a full 40-character commit SHA")
    for name in manifest.components:
        if name not in DEFAULT_VERSIONS_CONFIG:
            errors.append(f"components.{name}: unknown component (not in vispctl/versions.py)")

    images, problems = quadlet_images(project_dir)
    errors.extend(f"quadlets: {p}" for p in problems)
    for service in sorted(set(images) | set(manifest.images)):
        want, have = images.get(service), manifest.images.get(service)
        if want != have:
            errors.append(
                f"images.{service}: release.json says {have or '(none)'}, quadlets say {want or '(none)'}"
                " (./visp.py release sync)"
            )

    vendored, vproblems = vendored_pins(project_dir)
    errors.extend(f"vendored: {p}" for p in vproblems)
    for name in sorted(set(vendored) | set(manifest.vendored)):
        if vendored.get(name) != manifest.vendored.get(name):
            errors.append(f"vendored.{name}: release.json does not match {VENDORED_SOURCES.get(name)}")
    return errors


# ── the installed-version record ──────────────────────────────────────────────────


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


@dataclass
class InstallState:
    """``mounts/visp-state.json``: the VISP version this server runs.

    ``in_progress`` is set while an upgrade runs and records every step that
    completed, so a failed upgrade resumes where it stopped::

        {"from": "1.0.0", "to": "1.1.0", "started": "…",
         "done": ["1.1.0:backup-mongo", …], "data": {"1.1.0": {…}}}
    """

    path: Path
    data: dict

    @classmethod
    def load(cls, project_dir: Path) -> InstallState:
        path = Path(project_dir) / STATE_FILE
        data: dict = {}
        if path.exists():
            try:
                data = json.loads(path.read_text())
            except json.JSONDecodeError as e:
                raise ConfigError(f"{path}: invalid JSON: {e}") from e
        return cls(path, data)

    @property
    def exists(self) -> bool:
        return self.path.exists()

    @property
    def version(self) -> str | None:
        return self.data.get("version")

    @property
    def in_progress(self) -> dict | None:
        return self.data.get("in_progress")

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.data, indent=2) + "\n")
        tmp.replace(self.path)

    def record(self, version: str, how: str, deploy_commit: str | None = None) -> None:
        """Record that *version* is now installed (``how``: install / upgrade / mark)."""
        parse_version(version)
        previous = self.version
        self.data["version"] = version
        self.data["since"] = _now()
        self.data.setdefault("history", []).append(
            {"at": _now(), "how": how, "from": previous, "to": version, "deploy_commit": deploy_commit}
        )
        self.data.pop("in_progress", None)
        self.save()


def deploy_commit(project_dir: Path) -> str | None:
    """The deployment repository's HEAD commit, or None outside git."""
    import subprocess

    r = subprocess.run(["git", "rev-parse", "HEAD"], cwd=project_dir, capture_output=True, text=True, check=False)
    return (r.stdout.strip() or None) if r.returncode == 0 else None


def has_existing_data(project_dir: Path) -> bool:
    """True if this server already holds VISP data (so it is not a fresh install)."""
    import subprocess

    mongo = Path(project_dir) / "mounts" / "mongo" / "data"
    if not mongo.is_dir():
        return False
    try:
        return any(mongo.iterdir())
    except PermissionError:
        # Owned by the container's user once mongo has run: look from inside the user namespace.
        try:
            r = subprocess.run(["podman", "unshare", "ls", "-A", str(mongo)], capture_output=True, text=True)
        except OSError:
            return True
        return r.returncode != 0 or bool(r.stdout.strip())
