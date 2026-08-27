"""Log viewing helpers for VISP services.

Provides tail_container_logs, stream_podman_logs, show_debug_info, and
view_logs — extracted from visp.py so they can be tested independently.
"""

from __future__ import annotations

import subprocess
import threading
from pathlib import Path
from typing import Callable

from .runner import Colors, Runner, color
from .service import Service

# Container-internal log files that are NOT visible in journalctl.
# These are files inside the container that must be read via `podman exec`.
# Format: service_name -> list of (label, container_path) tuples.
CONTAINER_LOG_FILES: dict[str, list[tuple[str, str]]] = {
    "apache": [
        ("api", "/var/log/api/webapi.log"),
        ("api-debug", "/var/log/api/webapi.debug.log"),
        ("php-errors", "/var/log/api/php_error.log"),
        ("apache-error", "/var/log/apache2/visp.local-error.log"),
        ("octra-error", "/var/log/apache2/octra-error.log"),
        ("artic-error", "/var/log/apache2/artic-error.log"),
        ("shibboleth", "/var/log/shibboleth/shibd.log"),
        ("shibboleth-warn", "/var/log/shibboleth/shibd_warn.log"),
    ],
}


def tail_container_logs(
    service: str,
    container_log_files: dict[str, list[tuple[str, str]]],
    runner: Runner,
    lines: int = 50,
    follow: bool = False,
    stop_event: threading.Event | None = None,
) -> None:
    """Tail container-internal log files via podman exec.

    For services that write to log files inside the container (not stdout),
    this reads those files so they appear alongside journalctl output.
    """
    log_files = container_log_files.get(service)
    if not log_files:
        return

    container = service

    # Check if container is running
    rc, _, _ = runner.run_quiet(["podman", "inspect", "--format", "{{.State.Status}}", container])
    if rc != 0:
        print(color(f"  Container {container} not running — skipping app logs", Colors.YELLOW))
        return

    if follow:
        # Follow mode: spawn tail -f processes and stream output with prefixes
        processes = []
        for label, path in log_files:
            cmd = ["podman", "exec", container, "tail", "-n", "0", "-f", path]
            try:
                proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
                processes.append((label, proc))
            except OSError:
                pass

        def _stream_output(lbl: str, proc: subprocess.Popen) -> None:
            try:
                for line in proc.stdout:
                    if stop_event and stop_event.is_set():
                        break
                    print(f"{color(f'[{lbl}]', Colors.MAGENTA)} {line}", end="")
            except (OSError, ValueError):
                pass

        threads = []
        for label, proc in processes:
            t = threading.Thread(target=_stream_output, args=(label, proc), daemon=True)
            t.start()
            threads.append(t)

        try:
            if stop_event:
                stop_event.wait()
        except KeyboardInterrupt:
            pass
        finally:
            for _, proc in processes:
                proc.terminate()
            for _, proc in processes:
                proc.wait()
    else:
        # Snapshot mode: show last N lines from each log file
        for label, path in log_files:
            cmd = ["podman", "exec", container, "tail", "-n", str(lines), path]
            rc, stdout, stderr = runner.run_quiet(cmd)
            if rc == 0 and stdout.strip():
                print(color(f"\n--- {label} ({path}) ---", Colors.MAGENTA))
                print(stdout)
            elif rc != 0 and "No such file" not in stderr:
                pass  # File doesn't exist yet — skip silently


def stream_podman_logs(
    service: str,
    *,
    follow: bool,
    lines: int | None = None,
    since: str | None = None,
) -> None:
    """Stream container logs directly from podman.

    Preserves the original byte stream (including ANSI color escapes)
    and mirrors native `podman logs` behavior.
    """
    cmd = ["podman", "logs"]
    if follow:
        cmd.append("-f")
    if lines is not None:
        cmd.extend(["--tail", str(lines)])
    if since:
        cmd.extend(["--since", since])
    cmd.append(service)

    try:
        subprocess.run(cmd, check=False)
    except KeyboardInterrupt:
        pass


def show_debug_info(
    service: str,
    runner: Runner,
    all_services: list[Service],
    systemd_dir: Path,
) -> None:
    """Show diagnostic info for a service: systemd status, container state, quadlet file."""
    service_unit = f"{service}.service"

    print(color("Service Status:", Colors.YELLOW))
    runner.systemctl("status", service_unit)
    print()

    print(color("Container Info:", Colors.YELLOW))
    rc, _, _ = runner.run_quiet(["podman", "inspect", service])
    if rc == 0:
        runner.run(
            [
                "podman",
                "inspect",
                service,
                "--format",
                "Name: {{.Name}}\nState: {{.State.Status}}\nStarted: {{.State.StartedAt}}\nImage: {{.Image}}",
            ],
            check=False,
        )
    else:
        print(color(f"Container not found: {service}", Colors.RED))
    print()

    print(color("Quadlet File:", Colors.YELLOW))
    svc_info = next((s for s in all_services if s.name == service), None)
    if svc_info:
        link_path = systemd_dir / svc_info.file
        if link_path.is_symlink():
            print(color(f"  {link_path} -> {link_path.resolve()} (legacy symlink)", Colors.YELLOW))
        elif link_path.exists():
            print(f"  {link_path} (rendered template)")
        else:
            print(color(f"  {link_path} does not exist", Colors.RED))
    print()


def view_logs(
    args,
    runner: Runner,
    container_log_files: dict[str, list[tuple[str, str]]],
    systemd_dir: Path,
    get_runtime_services: Callable[[], list[Service]],
    get_all_services: Callable[[], list[Service]],
    resolve_services: Callable[[str], list[Service]],
    container_services: Callable[[list[Service]], list[Service]],
) -> None:
    """View logs from services (and optionally debug diagnostics).

    Parameters
    ----------
    args:
        Parsed argparse namespace with service, follow, lines, since, priority,
        journal_only, debug, no_follow attributes.
    runner:
        Runner instance for subprocess calls.
    container_log_files:
        Dict mapping service name to list of (label, path) tuples.
    systemd_dir:
        Path to the systemd quadlets directory (for quadlet file display in debug mode).
    get_runtime_services:
        Callable returning the list of active Services.
    get_all_services:
        Callable returning ALL services including disabled ones (for debug info).
    resolve_services:
        Callable that validates/resolves a service name string, raising ServiceError on error.
    container_services:
        Callable that filters a service list to container-type services only.
    """
    extra_args: list[str] = []
    journal_only = getattr(args, "journal_only", False)
    debug = getattr(args, "debug", False)
    no_follow = getattr(args, "no_follow", False)

    runtime_services = get_runtime_services()
    service_name = getattr(args, "service", None)
    if service_name not in (None, "all"):
        resolve_services(service_name)

    service_info = next((s for s in runtime_services if s.name == service_name), None)
    is_container_service = service_info is not None and service_info.type == "container"

    use_podman_logs = (
        service_name not in (None, "all")
        and is_container_service
        and not debug
        and not journal_only
        and not getattr(args, "priority", None)
    )

    if use_podman_logs:
        follow = args.follow or not no_follow
        stream_podman_logs(
            service_name,
            follow=follow,
            lines=args.lines,
            since=getattr(args, "since", None),
        )
        return

    if args.follow:
        extra_args.append("-f")
    if args.lines:
        extra_args.extend(["-n", str(args.lines)])
    elif not args.follow:
        extra_args.extend(["-n", "100"])
    if hasattr(args, "since") and args.since:
        extra_args.extend(["--since", args.since])
    if hasattr(args, "priority") and args.priority:
        extra_args.extend(["-p", args.priority])

    if args.service == "all" or not args.service:
        units: list[str] = []
        for svc in container_services(runtime_services):
            units.extend(["-u", f"{svc.name}.service"])
        print(color("=== Viewing logs for all VISP services ===", Colors.CYAN))
        runner.journalctl(*units, "--no-pager", *extra_args)
    else:
        service = args.service
        has_app_logs = service in container_log_files

        if debug:
            print(color(f"=== Debug info for {service} ===", Colors.CYAN))
            print()
            show_debug_info(service, runner, get_all_services(), systemd_dir)

        if args.follow:
            print(color(f"=== Following logs for {service} ===", Colors.CYAN))
            if has_app_logs and not journal_only:
                print(color("  (including container app logs — use --journal-only to hide)", Colors.CYAN))

            stop_event = threading.Event()

            if has_app_logs and not journal_only:
                threading.Thread(
                    target=tail_container_logs,
                    args=(service, container_log_files, runner),
                    kwargs={"follow": True, "stop_event": stop_event},
                    daemon=True,
                ).start()

            try:
                runner.journalctl("-u", f"{service}.service", *extra_args)
            except KeyboardInterrupt:
                pass
            finally:
                stop_event.set()
        else:
            print(color(f"=== Viewing logs for {service} ===", Colors.CYAN))
            runner.journalctl("-u", f"{service}.service", "--no-pager", *extra_args)

            if has_app_logs and not journal_only:
                n = args.lines if args.lines else 30
                print(color(f"\n=== Container app logs (last {n} lines each) ===", Colors.CYAN))
                tail_container_logs(service, container_log_files, runner, lines=n)
