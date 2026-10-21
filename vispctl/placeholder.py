"""Offline placeholder page: a static nginx container that stands in for Apache.

While VISP is down (e.g. waiting for an emergency security patch),
``./visp.py placeholder up`` serves mounts/placeholder/html/ on Apache's
published port(s), so the host proxy shows an explanation instead of a 502.
It runs no PHP, proxies nothing and mounts no VISP data. Every path answers
503 with the page (see mounts/placeholder/nginx/http.conf).

The quadlet is rendered from quadlets/placeholder/<mode>.container into the
systemd dir only while the page is up. It is deliberately not in the service
registry, so 'all' never includes it and remove_stale_quadlets() leaves it
alone. Whenever visp.py starts apache, ServiceManager.start() removes it
first: it holds apache's port, and its WantedBy= would race apache at the
next boot (Conflicts=apache.service only settles who runs *now*).
"""

from __future__ import annotations

import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Callable

from .exceptions import ConfigError, ServiceError
from .runner import Colors, Runner, color
from .service import DEFAULT_SERVICES
from .service_manager import autostart_dropin

PLACEHOLDER_NAME = "visp-placeholder"
PLACEHOLDER_FILE = f"{PLACEHOLDER_NAME}.container"
PLACEHOLDER_UNIT = f"{PLACEHOLDER_NAME}.service"

_APACHE = next(s for s in DEFAULT_SERVICES if s.name == "apache")


def placeholder_template(project_dir: Path, mode: str) -> Path:
    """Source quadlet for the given mode."""
    return project_dir / "quadlets" / "placeholder" / f"{mode}.container"


def is_installed(systemd_dir: Path) -> bool:
    return (systemd_dir / PLACEHOLDER_FILE).exists()


def _directive(content: str, key: str) -> list[str]:
    """Values of every 'Key=' line in a quadlet."""
    prefix = f"{key}="
    return [line.split("=", 1)[1].strip() for line in content.splitlines() if line.startswith(prefix)]


def http_port(content: str) -> str | None:
    """Host port published for the container's port 80 ('[ip:]host:80')."""
    for value in _directive(content, "PublishPort"):
        host, _, container = value.rpartition(":")
        if container == "80":
            return host.rpartition(":")[2]
    return None


def probe(port: str, timeout: float = 3.0) -> int | None:
    """HTTP status of GET / on the placeholder (503 when it is serving), or None."""
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=timeout) as resp:
            return resp.status
    except urllib.error.HTTPError as e:
        return e.code
    except (urllib.error.URLError, OSError):
        return None


def remove_placeholder(runner: Runner, systemd_dir: Path) -> bool:
    """Stop and uninstall the placeholder. Returns True if it was installed."""
    target = systemd_dir / PLACEHOLDER_FILE
    if not target.exists():
        return False
    runner.systemctl("stop", PLACEHOLDER_UNIT)
    target.unlink()
    runner.systemctl("daemon-reload")
    return True


def placeholder_up(
    runner: Runner,
    project_dir: Path,
    systemd_dir: Path,
    mode: str,
    render_fn: Callable[[str], str],
) -> None:
    """Install and start the placeholder in apache's place."""
    template = placeholder_template(project_dir, mode)
    if not template.exists():
        raise ConfigError(f"No placeholder quadlet for mode '{mode}': {template}")

    # Refuse unless VISP was taken down with 'down': a plain 'stop' leaves
    # apache enabled, and both units would then race for the port at boot.
    if runner.unit_is_active(_APACHE.name):
        raise ServiceError("apache is running. Take VISP down first: ./visp.py down")
    if (systemd_dir / _APACHE.file).exists() and not autostart_dropin(systemd_dir, _APACHE).exists():
        raise ServiceError(
            "apache is stopped but would start again at boot. Take VISP down first: ./visp.py down "
            "(stops and disables autostart)"
        )

    rendered = render_fn(template.read_text())
    if "@@" in rendered:
        unresolved = sorted({tok for tok in rendered.split("@@")[1::2]})
        raise ConfigError(f"Unresolved placeholder(s) in {template.name}: {', '.join(unresolved)} — check .env")
    port = http_port(rendered)

    print(color("=== Starting offline placeholder page ===", Colors.CYAN))
    for image in _directive(rendered, "Image"):
        print(f"Pulling {image}...")
        if runner.run(["podman", "pull", "--quiet", image], check=False).returncode != 0:
            raise ServiceError(f"Could not pull {image}")

    systemd_dir.mkdir(parents=True, exist_ok=True)
    (systemd_dir / PLACEHOLDER_FILE).write_text(rendered)
    runner.systemctl("daemon-reload")

    # restart, not start: a re-run (e.g. after a mode switch) picks up the new unit.
    res = runner.systemctl("restart", PLACEHOLDER_UNIT)
    if res.returncode != 0:
        raise ServiceError(
            f"{PLACEHOLDER_UNIT} failed to start: {res.stderr.strip()}\n"
            f"  See: journalctl --user -u {PLACEHOLDER_UNIT} -n 50"
        )

    status = None
    if port:
        for _ in range(10):
            status = probe(port)
            if status == 503:
                break
            time.sleep(1)

    if status == 503:
        print(color(f"  ✓ Serving the offline page on port {port} (every path → 503)", Colors.GREEN))
    else:
        print(color(f"  ⚠ Started, but GET http://127.0.0.1:{port}/ gave {status or 'no answer'}", Colors.YELLOW))
        print(f"    See: journalctl --user -u {PLACEHOLDER_UNIT} -n 50")
    print()
    print(f"  Page text:  {project_dir / 'mounts/placeholder/html/index.html'} (edits are live)")
    print("  Survives reboots. To bring VISP back: ./visp.py up  (removes the placeholder first)")


def placeholder_down(runner: Runner, systemd_dir: Path) -> None:
    if remove_placeholder(runner, systemd_dir):
        print(color("  ✓ Offline placeholder page stopped and removed", Colors.GREEN))
        print("    Nothing answers on apache's port now. To bring VISP back: ./visp.py up")
    else:
        print(color("  ○ Offline placeholder page is not installed", Colors.YELLOW))


def placeholder_status(runner: Runner, systemd_dir: Path) -> None:
    target = systemd_dir / PLACEHOLDER_FILE
    if not target.exists():
        print(color("  ○ Offline placeholder page: not installed", Colors.YELLOW))
        return
    active = runner.unit_is_active(PLACEHOLDER_NAME)
    port = http_port(target.read_text())
    status = probe(port) if (active and port) else None
    if active and status == 503:
        print(color(f"  ● Offline placeholder page: serving on port {port}", Colors.GREEN))
    elif active:
        print(
            color(f"  ⚠ Offline placeholder page: running, but port {port} gave {status or 'no answer'}", Colors.YELLOW)
        )
    else:
        print(color("  ✗ Offline placeholder page: installed but not running", Colors.RED))
        print(f"    See: journalctl --user -u {PLACEHOLDER_UNIT} -n 50")
