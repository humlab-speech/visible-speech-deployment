"""``./visp.py images update <service>``: move a pulled third-party image to another tag.

The steps run in an order where a failure leaves nothing half-done:

1. ask the registry which tags exist, and let the operator pick one,
2. pull it — if that fails, nothing has been changed yet,
3. rewrite ``Image=`` in the service's quadlet for every mode (dev and prod),
4. converge like ``apply``: install the changed quadlet, daemon-reload, restart.

Picking the current tag again (it was rebuilt upstream, see ``images``) skips
step 3 and only restarts.
"""

from __future__ import annotations

import argparse
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .exceptions import ConfigError, ServiceError
from .images import ImageManager
from .placeholder import placeholder_template
from .quadlets import get_current_mode
from .registry import ImageRef, RegistryClient, RegistryError, is_older, major_of, newest_by_major
from .runner import Colors, color

MODES = ("dev", "prod")

# What an image does not do by itself after a version change, by repository.
POST_UPDATE_HINTS = {
    "library/matomo": (
        "Matomo upgrades its database schema only when asked:\n"
        "  podman exec matomo php /var/www/html/console core:update --yes"
    ),
}


@dataclass
class ImagePin:
    """One ``Image=`` line: which mode's quadlet it is in and what it pins."""

    mode: str
    path: Path
    ref: str


@dataclass
class Choice:
    tag: str
    label: str


def find_image_pins(project_dir: Path, service: str) -> list[ImagePin]:
    """Return the ``Image=`` pins of *service* in each mode's quadlet that has one."""
    pins = []
    for mode in MODES:
        if service == "placeholder":
            path = placeholder_template(project_dir, mode)
        else:
            path = project_dir / "quadlets" / mode / f"{service}.container"
        if not path.exists():
            continue
        for line in path.read_text().splitlines():
            if line.startswith("Image="):
                pins.append(ImagePin(mode, path, line.split("=", 1)[1].strip()))
    return pins


def retag(ref: str, tag: str) -> str:
    """Return *ref* with its tag (and any digest) replaced by *tag*."""
    name = ref.partition("@")[0]
    old = ImageRef.parse(ref).tag
    if old and name.endswith(f":{old}"):
        name = name[: -len(old) - 1]
    return f"{name}:{tag}"


def set_image_pin(pin: ImagePin, new_ref: str) -> None:
    text = pin.path.read_text()
    new_text, n = re.subn(rf"^Image={re.escape(pin.ref)}[ \t]*$", f"Image={new_ref}", text, count=1, flags=re.M)
    if n != 1:
        raise ConfigError(f"{pin.path}: no 'Image={pin.ref}' line to replace")
    pin.path.write_text(new_text)


def build_choices(current_tag: str | None, tags: list[str], rebuilt: bool, pulled: bool) -> list[Choice]:
    """Offer the newest tag of the current major and of each later major.

    Each later major is listed on its own, so a database can be moved one
    major at a time. The current tag is offered when re-pulling it would
    change something.
    """
    current_major = major_of(current_tag)
    choices = []
    for major, tag in newest_by_major(current_tag, tags).items():
        label = f"newest {major}.x" if major == current_major else f"newest {major}.x — major upgrade"
        choices.append(Choice(tag, label))
    if current_tag and (rebuilt or not pulled):
        reason = "rebuilt upstream" if rebuilt else "not pulled here yet"
        choices.append(Choice(current_tag, f"current tag, pull again ({reason})"))
    return choices


def prompt_for_tag(choices: list[Choice], tags: list[str], input_fn: Callable[[str], str] = input) -> str | None:
    """Ask for a choice number or any registry tag; None if the answer is empty."""
    for i, choice in enumerate(choices, 1):
        print(f"  {i}) {choice.tag:24} {choice.label}")
    print("  …or type any tag the registry has. An empty answer cancels.")
    known = set(tags)
    while True:
        try:
            answer = input_fn("Version: ").strip()
        except EOFError:
            raise ConfigError("No answer (non-interactive). Pass the tag with --to.") from None
        if not answer:
            return None
        if answer.isdigit() and 1 <= int(answer) <= len(choices):
            return choices[int(answer) - 1].tag
        if answer in known:
            return answer
        print(color(f"  No tag '{answer}' in the registry — pick a number or an existing tag.", Colors.YELLOW))


def _warnings(old_tag: str | None, new_tag: str) -> list[str]:
    old_major, new_major = major_of(old_tag), major_of(new_tag)
    if is_older(new_tag, old_tag):
        return [
            f"{new_tag} is OLDER than {old_tag}. A database usually cannot read data written by a newer version."
        ]
    if old_major is None or new_major is None or new_major == old_major:
        return []
    msg = (
        f"{old_tag} → {new_tag} is a major upgrade. Read the release notes first. Databases change their"
        " data format between majors: back up first (./visp.py backup for mongo), and MongoDB must move"
        " one major at a time, raising featureCompatibilityVersion after each step."
    )
    if new_major - old_major > 1:
        msg += f" This skips {new_major - old_major - 1} major version(s)."
    return [msg]


def _confirm(question: str, input_fn: Callable[[str], str]) -> bool:
    try:
        return input_fn(f"{question} [y/N]: ").strip().lower() in ("y", "yes")
    except EOFError:
        raise ConfigError("No answer (non-interactive). Pass --yes to confirm.") from None


def run_image_update(
    args,
    cfg,
    client: RegistryClient | None = None,
    input_fn: Callable[[str], str] = input,
    apply_fn: Callable | None = None,
    restart_fn: Callable | None = None,
) -> int:
    """Pick, pull and roll out a new tag for one service's third-party image."""
    service = args.service
    project_dir = cfg.project_dir
    pins = find_image_pins(project_dir, service)
    if not pins:
        raise ConfigError(f"No quadlet with an Image= line for '{service}'.")
    if any(p.ref.startswith("localhost/") for p in pins):
        raise ConfigError(f"'{service}' runs our own image — rebuild it with: ./visp.py build {service}")

    mode = get_current_mode()
    current = next((p for p in pins if p.mode == mode), pins[0])
    ref = ImageRef.parse(current.ref)
    if ref.digest:
        raise ConfigError(f"{current.ref} is pinned by digest; change it by hand in {current.path}.")
    repos = {ImageRef.parse(p.ref).repository for p in pins}
    if len(repos) > 1:
        raise ConfigError(f"'{service}' uses different images per mode ({', '.join(sorted(repos))}); edit by hand.")

    client = client or RegistryClient()
    try:
        tags = client.list_tags(ref)
        remote_digest = client.tag_digest(ref)
    except RegistryError as e:
        raise ServiceError(f"Registry check failed: {e}") from e

    local = next(
        (i for i in ImageManager(cfg.runner).get_third_party_images(check_remote=False) if i.ref == current.ref),
        None,
    )
    pulled = bool(local and local.local_id)
    rebuilt = pulled and remote_digest not in local.local_digests

    print(color(f"=== Update {service} ===", Colors.CYAN))
    print(f"  Pinned:  {current.ref}")
    for pin in pins:
        note = "" if pin.ref == current.ref else color(f"  (pins {pin.ref})", Colors.YELLOW)
        print(f"           {pin.path.relative_to(project_dir)}{note}")
    if not pulled:
        print("  Local:   not pulled")
    elif rebuilt:
        print(color(f"  Local:   '{ref.tag}' was rebuilt upstream since it was pulled", Colors.YELLOW))
    else:
        print(color("  Local:   same digest as the registry", Colors.GREEN))
    print()

    if args.to:
        if args.to not in tags:
            raise ConfigError(f"The registry has no tag '{args.to}' for {ref.repository}.")
        new_tag = args.to
    else:
        choices = build_choices(ref.tag, tags, rebuilt, pulled)
        if not choices:
            print(color(f"{ref.tag} is the newest version and matches the registry.", Colors.GREEN))
        new_tag = prompt_for_tag(choices, tags, input_fn)
        if new_tag is None:
            print("Cancelled.")
            return 1

    new_ref = retag(current.ref, new_tag)
    retagging = any(p.ref != retag(p.ref, new_tag) for p in pins)

    print()
    for warning in _warnings(ref.tag, new_tag):
        print(color(f"⚠ {warning}", Colors.YELLOW))
        print()
    if retagging:
        print(f"{current.ref} → {color(new_ref, Colors.GREEN)}")
        question = f"Pull {new_tag}, update {len(pins)} quadlet(s) and restart {service}?"
    else:
        print(f"Pull {new_ref} again (same tag, new digest).")
        question = f"Pull and restart {service}?"
    if not args.yes and not _confirm(question, input_fn):
        print("Cancelled.")
        return 1

    print()
    print(color(f"Pulling {new_ref}...", Colors.CYAN))
    if cfg.runner.run(["podman", "pull", new_ref], check=False).returncode != 0:
        raise ServiceError(f"Pulling {new_ref} failed. Nothing was changed.")

    changed = []
    if retagging:
        for pin in pins:
            set_image_pin(pin, retag(pin.ref, new_tag))
            changed.append(str(pin.path.relative_to(project_dir)))
            print(f"  updated {changed[-1]}")
    print()

    if service == "placeholder":
        print("The placeholder uses the new image the next time it is brought up (./visp.py placeholder up).")
    elif retagging:
        (apply_fn or _apply)(service, cfg)
    else:
        (restart_fn or _restart)(service, cfg)

    print()
    hint = POST_UPDATE_HINTS.get(ref.repository)
    if hint and retagging:
        print(color(hint, Colors.YELLOW))
        print()
    if changed:
        print("Commit the quadlet change, so the next git pull does not conflict with it:")
        print(f"  git commit {' '.join(changed)} -m 'chore(quadlets): {service} {ref.tag} → {new_tag}'")
    return 0


def _apply(service: str, cfg) -> None:
    """Install the changed quadlet, daemon-reload and restart — what ``./visp.py apply <service>`` does."""
    from .quadlets import cmd_apply
    from .service import resolve_services

    try:
        resolve_services(service, cfg.project_dir)
    except ServiceError as e:  # e.g. an optional service that is disabled in .env
        print(color(f"Quadlets updated; {service} is not restarted: {e}", Colors.YELLOW))
        return
    cmd_apply(
        argparse.Namespace(service=service),
        project_dir=cfg.project_dir,
        systemd_dir=cfg.systemd_dir,
        runner=cfg.runner,
        build_configs=cfg.build_configs,
        network_services=cfg.network_services,
    )


def _restart(service: str, cfg) -> None:
    from .service import get_runtime_services
    from .service_manager import ServiceManager

    sm = ServiceManager(cfg.runner, get_runtime_services(cfg.project_dir, include_disabled=True))
    sm.stop([service])
    sm.start([service])
