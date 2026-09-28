#!/usr/bin/env python3
"""Move a legacy Docker Compose VISP install onto rootless Podman quadlets.

The old install keeps running from (and is never modified in) its own directory;
the new one is a fresh checkout owned by a dedicated service user. Data is copied,
not moved, so the old tree stays a working rollback until you delete it.

Run as root, one phase at a time, in this order (``--dry-run`` prints what a
phase would do without doing it):

  preflight    read-only checks: old install, disk, filesystem, ports
  host         apt packages, service user (home on the data volume), subuid/subgid,
               lingering, user podman socket, rootless smoke test
  checkout     clone this repo for the service user, then 'deploy update'
  config       .env / .env.secrets for the new tree, from the old .env
  sync-data    copy repositories, certs, Matomo config, unimported audio and
               WhisperVault models (rsync — re-runnable; run once early for the
               bulk copy, and again after 'backup-old' for the delta)
  backup-old   start the old mongo + matomo-db alone, dump both, stop them again
  install      './visp.py install --mode prod'
  build        './visp.py build all --config <webclient config>'
  migrate      restore Mongo, migrate-permissions, migrate-meta-json, grant
               sys_admin, import Matomo (re-runnable: restores with --drop)
  start        start everything, update the Matomo schema
  edge         point the host TLS proxy (nginx) at the new Apache
  verify       status, users, Shibboleth key, HTTP probes

Rollback until the old tree is deleted: './visp.py stop all' as the service user,
'docker compose up -d' in the old directory, and restore the proxy config saved
as '<proxy.conf>.pre-podman' by 'edge'.

Example:
  sudo ./scripts/migrate-from-compose.py preflight --old-dir /srv/old-visp
  sudo ./scripts/migrate-from-compose.py migrate --old-dir /srv/old-visp \\
       --sysadmin some_user_at_example_dot_org --sysadmin other_user_at_example_dot_org
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import pwd
import re
import secrets
import shlex
import shutil
import string
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from vispctl.env import load_env_file  # noqa: E402

REPO_URL = "https://github.com/humlab-speech/visible-speech-deployment"
MONGO_VERSION = "6.0.27"  # must match quadlets/prod/mongo.container and the old install
NEW_MATOMO_DB_NAME = "matomo_db"  # hard-coded in quadlets/prod/matomo*.container
SUBID_COUNT = 65536
STATE_FILE = ".migrate-from-compose.json"

# Keys that belong in .env.secrets (mirrors password_vars in vispctl/passwords.py).
SECRET_KEYS = [
    "MONGO_ROOT_PASSWORD",
    "MONGO_EXPRESS_PASSWORD",
    "MATOMO_DB_USER",
    "MATOMO_DB_PASSWORD",
    "MATOMO_DB_ROOT_PASSWORD",
    "VISP_API_ACCESS_TOKEN",
    "TEST_USER_LOGIN_KEY",
    "POSTGRES_PASSWORD",
    "ELASTIC_AGENT_FLEET_ENROLLMENT_TOKEN",
    "SSP_ADMIN_PASSWORD",
    "SSP_SALT",
]
# Secrets the existing databases were created with: generating new ones would
# lock the new install out of the restored data.
REQUIRED_OLD_SECRETS = ["MONGO_ROOT_PASSWORD", "MATOMO_DB_USER", "MATOMO_DB_PASSWORD", "MATOMO_DB_ROOT_PASSWORD"]

APT_PACKAGES = ["podman", "uidmap", "slirp4netns", "passt", "netavark", "aardvark-dns", "git", "rsync", "curl"]

C_RED, C_GREEN, C_YELLOW, C_CYAN, C_BOLD, C_NC = (
    "\033[0;31m",
    "\033[0;32m",
    "\033[1;33m",
    "\033[0;36m",
    "\033[1m",
    "\033[0m",
)

DRY_RUN = False


class MigrationError(Exception):
    pass


def say(msg: str, c: str = "") -> None:
    print(f"{c}{msg}{C_NC}" if c else msg, flush=True)


def heading(msg: str) -> None:
    say(f"\n=== {msg} ===", C_CYAN)


def warn(msg: str) -> None:
    say(f"⚠  {msg}", C_YELLOW)


# ── pure helpers (unit-tested) ────────────────────────────────────────────────


def parse_subid_file(text: str) -> list[tuple[str, int, int]]:
    """Parse /etc/subuid or /etc/subgid into (name, start, count) entries."""
    entries = []
    for line in text.splitlines():
        parts = line.strip().split(":")
        if len(parts) != 3 or not parts[1].isdigit() or not parts[2].isdigit():
            continue
        entries.append((parts[0], int(parts[1]), int(parts[2])))
    return entries


def next_subid_start(entries: list[tuple[str, int, int]], minimum: int = 100000) -> int:
    """First id after every existing range (never below ``minimum``)."""
    return max([minimum] + [start + count for _, start, count in entries])


def generate_secret(length: int = 32) -> str:
    alphabet = string.ascii_letters + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(length))


def plan_env(
    old: dict[str, str],
    example: dict[str, str],
    overrides: dict[str, str],
) -> tuple[dict[str, str], dict[str, str], list[str], list[str]]:
    """Work out the new .env and .env.secrets from the old .env.

    .env gets every non-secret key of the new .env-example: an override wins,
    then the old value, then the example default. Secrets are carried over from
    the old .env; missing optional ones are generated, missing required ones
    raise. Returns ``(env, secrets, dropped_old_keys, generated_secret_keys)``.
    """
    missing = [k for k in REQUIRED_OLD_SECRETS if not old.get(k)]
    if missing:
        raise MigrationError(f"old .env lacks {', '.join(missing)} — the existing databases need them")

    env = {}
    for key, default in example.items():
        if key in SECRET_KEYS:
            continue
        if key in overrides:
            env[key] = overrides[key]
        elif old.get(key):
            env[key] = old[key]
        else:
            env[key] = default

    secret_values, generated = {}, []
    for key in SECRET_KEYS:
        if old.get(key):
            secret_values[key] = old[key]
        else:
            secret_values[key] = generate_secret()
            generated.append(key)

    dropped = sorted(k for k in old if k not in example and k not in SECRET_KEYS)
    return env, secret_values, dropped, generated


def render_env(example_text: str, values: dict[str, str]) -> str:
    """Rewrite .env-example text with ``values``, keeping its comments and order.

    Lines for keys not in ``values`` (the secrets) are removed.
    """
    out = []
    for line in example_text.splitlines():
        m = re.match(r"^([A-Z0-9_]+)=", line)
        if not m:
            out.append(line)
            continue
        key = m.group(1)
        if key in values:
            out.append(f"{key}={values[key]}")
    return "\n".join(out) + "\n"


def render_secrets(values: dict[str, str]) -> str:
    lines = ["# Generated by scripts/migrate-from-compose.py — carried over from the old .env"]
    lines += [f"{k}={v}" for k, v in values.items()]
    return "\n".join(lines) + "\n"


def patch_matomo_dbname(config_text: str, new_name: str) -> str:
    """Point the [database] section of Matomo's config.ini.php at ``new_name``."""
    out, section = [], ""
    for line in config_text.splitlines(keepends=True):
        s = line.strip()
        if s.startswith("[") and s.endswith("]"):
            section = s
        elif section == "[database]" and re.match(r"^dbname\s*=", s):
            line = f'dbname = "{new_name}"\n'
        out.append(line)
    return "".join(out)


def render_proxy_conf(cert: str, key: str, upstream_port: int) -> str:
    """nginx config for the host TLS endpoint in front of Apache."""
    return f"""# Generated by scripts/migrate-from-compose.py
# TLS from the load balancer terminates here; Apache (rootless Podman) listens on :{upstream_port}.
map $http_upgrade $connection_upgrade {{
    default upgrade;
    ''      close;
}}

server {{
    listen 443 ssl;
    server_name _;
    ssl_certificate     {cert};
    ssl_certificate_key {key};

    client_max_body_size 10G;

    location / {{
        proxy_pass http://127.0.0.1:{upstream_port};
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto https;
        # WebSocket — the main app cannot log in without it
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection $connection_upgrade;
        proxy_connect_timeout 60s;
        proxy_send_timeout 86400s;
        proxy_read_timeout 86400s;
    }}
}}
"""


def mongo_config_yaml(password: str) -> str:
    """mongodump --config file; a JSON string is valid YAML and escapes everything."""
    return f"password: {json.dumps(password)}\n"


# ── process helpers ───────────────────────────────────────────────────────────


class Ctx:
    def __init__(self, args: argparse.Namespace):
        self.args = args
        self.old = Path(args.old_dir).resolve()
        self.user = args.user
        self.home = Path(args.home).resolve()
        self.new = self.home / "visible-speech-deployment"
        self.work = self.home / "migration"

    # user info is resolved lazily: it does not exist before 'host'
    @property
    def pw(self) -> pwd.struct_passwd:
        try:
            return pwd.getpwnam(self.user)
        except KeyError as e:
            raise MigrationError(f"user {self.user!r} does not exist — run the 'host' phase first") from e

    def run(
        self,
        cmd: list[str],
        *,
        as_user: bool = False,
        cwd: Path | None = None,
        input: str | None = None,
        stdin=None,
        stdout=None,
        check: bool = True,
        capture: bool = False,
        quiet: bool = False,
    ) -> subprocess.CompletedProcess:
        if as_user:
            try:
                uid = self.pw.pw_uid
            except MigrationError:
                if not DRY_RUN:
                    raise
                uid = "<uid>"  # dry run before 'host' created the user
            cmd = [
                "runuser",
                "-u",
                self.user,
                "--",
                "env",
                f"HOME={self.home}",
                f"USER={self.user}",
                f"XDG_RUNTIME_DIR=/run/user/{uid}",
                f"DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/{uid}/bus",
                "PATH=/usr/local/bin:/usr/bin:/bin",
            ] + cmd
        shown = shlex.join(cmd)
        if as_user:
            shown = f"[{self.user}] " + shlex.join(cmd[cmd.index("PATH=/usr/local/bin:/usr/bin:/bin") + 1 :])
        if cwd:
            shown = f"(cd {cwd}) {shown}"
        if not quiet:
            say(f"  $ {shown}", C_BOLD)
        if DRY_RUN:
            return subprocess.CompletedProcess(cmd, 0, "", "")
        res = subprocess.run(
            cmd,
            cwd=cwd,
            input=input,
            text=True,
            stdin=None if input is not None else (stdin or subprocess.DEVNULL),
            stdout=subprocess.PIPE if capture else stdout,
            stderr=subprocess.PIPE if capture else None,
        )
        if check and res.returncode != 0:
            raise MigrationError(f"command failed ({res.returncode}): {shown}")
        return res

    def probe(self, cmd: list[str], *, as_user: bool = False, cwd: Path | None = None) -> tuple[int, str]:
        """Run a read-only command even in dry-run mode; returns (rc, stdout)."""
        global DRY_RUN
        saved, DRY_RUN = DRY_RUN, False
        try:
            res = self.run(cmd, as_user=as_user, cwd=cwd, check=False, capture=True, quiet=True)
        except MigrationError:  # the service user does not exist yet
            return 1, ""
        finally:
            DRY_RUN = saved
        return res.returncode, (res.stdout or "").strip()

    def visp(self, *argv: str, check: bool = True) -> subprocess.CompletedProcess:
        return self.run(["python3", "./visp.py", *argv], as_user=True, cwd=self.new, check=check)

    def compose(self, *argv: str, **kw) -> subprocess.CompletedProcess:
        return self.run(["docker", "compose", *argv], cwd=self.old, **kw)

    # state ------------------------------------------------------------------
    def state_path(self) -> Path:
        return self.work / STATE_FILE

    def state(self) -> dict:
        try:
            return json.loads(self.state_path().read_text())
        except (OSError, ValueError):
            return {}

    def mark(self, phase: str, **extra) -> None:
        if DRY_RUN:
            return
        st = self.state()
        st.setdefault("done", {})[phase] = datetime.datetime.now().isoformat(timespec="seconds")
        st.update(extra)
        self.work.mkdir(parents=True, exist_ok=True)
        self.state_path().write_text(json.dumps(st, indent=2) + "\n")

    def require(self, *phases: str) -> None:
        done = self.state().get("done", {})
        missing = [p for p in phases if p not in done]
        if missing and not self.args.force:
            raise MigrationError(f"run phase(s) {', '.join(missing)} first (or pass --force)")

    def old_env(self) -> dict[str, str]:
        env = load_env_file(self.old / ".env")
        if not env:
            raise MigrationError(f"{self.old}/.env is missing or empty")
        return env

    def chown_user(self, path: Path) -> None:
        self.run(["chown", "-R", f"{self.user}:{self.user}", str(path)])

    def wait_for(self, what: str, cmd: list[str], *, as_user: bool = False, cwd: Path | None = None) -> None:
        if DRY_RUN:
            say(f"  (would wait for {what})")
            return
        for _ in range(60):
            if self.probe(cmd, as_user=as_user, cwd=cwd)[0] == 0:
                say(f"  ✓ {what} is up", C_GREEN)
                return
            time.sleep(2)
        raise MigrationError(f"timed out waiting for {what}")


# ── phases ────────────────────────────────────────────────────────────────────


def phase_preflight(ctx: Ctx) -> None:
    heading("Preflight (read-only)")
    problems = 0

    def check(ok: bool, good: str, bad: str) -> None:
        nonlocal problems
        if ok:
            say(f"  ✓ {good}", C_GREEN)
        else:
            say(f"  ✗ {bad}", C_RED)
            problems += 1

    check(os.geteuid() == 0, "running as root", "must run as root (sudo)")
    check((ctx.old / ".env").is_file(), f"old .env in {ctx.old}", f"no .env in {ctx.old}")
    check(
        (ctx.old / "docker-compose.yml").exists(),
        "old docker-compose.yml present",
        f"no docker-compose.yml in {ctx.old}",
    )
    old = load_env_file(ctx.old / ".env")
    missing = [k for k in REQUIRED_OLD_SECRETS if not old.get(k)]
    check(not missing, "old .env has the database passwords", f"old .env lacks {', '.join(missing)}")
    say(f"  BASE_DOMAIN in old .env: {old.get('BASE_DOMAIN', '?')}")

    rc, out = ctx.probe(["docker", "ps", "--format", "{{.Names}}"])
    running = [n for n in out.splitlines() if n]
    say(f"  running docker containers: {', '.join(running) or 'none'}")
    old_project = old.get("COMPOSE_PROJECT_NAME", "visp")
    visp_running = [n for n in running if n.startswith(f"{old_project}-") or n.startswith(f"{old_project}_")]
    if visp_running:
        warn(f"old VISP containers are running ({', '.join(visp_running)}); backup-old will stop them")

    repos = ctx.old / "mounts/repositories"
    _, du = ctx.probe(["du", "-sb", str(repos)])
    repos_bytes = int(du.split()[0]) if du else 0
    stat = shutil.disk_usage(ctx.home.parent if not ctx.home.exists() else ctx.home)
    need = repos_bytes + 80 * 1024**3  # copy + container images (~60G) + dumps
    check(
        stat.free > need,
        f"{stat.free / 1024**3:.0f} G free on the target volume (need ~{need / 1024**3:.0f} G)",
        f"only {stat.free / 1024**3:.0f} G free on the target volume, need ~{need / 1024**3:.0f} G",
    )

    _, fstype = ctx.probe(["stat", "-f", "-c", "%T", str(ctx.home.parent)])
    if fstype == "xfs":
        _, xfs = ctx.probe(["xfs_info", str(ctx.home.parent)])
        check("ftype=1" in xfs, "XFS has ftype=1 (overlayfs OK)", "XFS without ftype=1 — overlayfs will not work")
    else:
        say(f"  target filesystem: {fstype}")

    _, userns = ctx.probe(["sysctl", "-n", "kernel.apparmor_restrict_unprivileged_userns"])
    if userns == "1":
        say("  AppArmor restricts unprivileged user namespaces — 'host' runs a rootless smoke test to confirm")

    rc, _ = ctx.probe(["sh", "-c", "ss -ltn '( sport = :8081 )' | grep -q LISTEN"])
    check(rc != 0, "port 8081 is free", "something already listens on 8081 (new Apache needs it)")

    say("")
    say("Outside this script — arrange before cutover:", C_BOLD)
    say(
        f"  • DNS/load balancer for artic., app., recorder., matomo. and the TRATT subdomain of {old.get('BASE_DOMAIN')}"
    )
    say("  • the load balancer re-encrypting to this host on :443 (the 'edge' phase serves it)")
    say("  • if Ansible manages users or /etc/subuid here, add the service user there too")
    if problems:
        raise MigrationError(f"{problems} preflight problem(s)")


def phase_host(ctx: Ctx) -> None:
    heading("Host: packages, service user, rootless Podman")
    ctx.run(["apt-get", "update"])
    ctx.run(["apt-get", "install", "-y", *APT_PACKAGES])

    try:
        pwd.getpwnam(ctx.user)
        say(f"  user {ctx.user} already exists")
    except KeyError:
        ctx.run(
            ["useradd", "--create-home", "--home-dir", str(ctx.home), "--shell", "/bin/bash", "--user-group", ctx.user]
        )

    for path, flag in (("/etc/subuid", "--add-subuids"), ("/etc/subgid", "--add-subgids")):
        entries = parse_subid_file(Path(path).read_text() if Path(path).exists() else "")
        if any(name == ctx.user for name, _, _ in entries):
            say(f"  {path}: {ctx.user} already has a range")
            continue
        start = next_subid_start(entries)
        ctx.run(["usermod", flag, f"{start}-{start + SUBID_COUNT - 1}", ctx.user])

    ctx.run(["loginctl", "enable-linger", ctx.user])
    if not DRY_RUN:
        ctx.run(["systemctl", "start", f"user@{ctx.pw.pw_uid}.service"])
        ctx.wait_for("user systemd bus", ["test", "-S", f"/run/user/{ctx.pw.pw_uid}/bus"])
    # Keep the API service running, not just socket-activated (see AGENTS.md).
    ctx.run(["systemctl", "--user", "enable", "--now", "podman.socket", "podman.service"], as_user=True)

    rc, backend = ctx.probe(["podman", "info", "--format", "{{.Host.NetworkBackend}}"], as_user=True)
    if not DRY_RUN and backend != "netavark":
        raise MigrationError(f"podman network backend is {backend!r}, netavark is required")
    ctx.run(["podman", "run", "--rm", "docker.io/library/alpine:3.23", "true"], as_user=True)
    say("  ✓ rootless Podman works", C_GREEN)
    ctx.mark("host")


def phase_checkout(ctx: Ctx) -> None:
    heading("Checkout")
    ctx.require("host")
    if (ctx.new / ".git").exists():
        say(f"  {ctx.new} exists — fetching")
        ctx.run(["git", "fetch", "origin"], as_user=True, cwd=ctx.new)
        ctx.run(["git", "checkout", ctx.args.branch], as_user=True, cwd=ctx.new)
        ctx.run(["git", "pull", "--ff-only"], as_user=True, cwd=ctx.new)
    else:
        ctx.run(["git", "clone", "--branch", ctx.args.branch, REPO_URL, str(ctx.new)], as_user=True)
    ctx.visp("deploy", "update")
    ctx.mark("checkout")


def phase_config(ctx: Ctx) -> None:
    heading("Config: .env and .env.secrets")
    ctx.require("checkout")
    for f in (".env", ".env.secrets"):
        if (ctx.new / f).exists() and not ctx.args.force:
            raise MigrationError(f"{ctx.new / f} already exists — pass --force to regenerate it")

    old = ctx.old_env()
    example_text = (ctx.new / ".env-example").read_text()
    example = load_env_file(ctx.new / ".env-example")
    overrides = {
        "ABS_ROOT_PATH": str(ctx.new),
        "HTTP_PORT": "8081",
        "HTTP_PROTOCOL": "https",
        "LOCAL_IDP_ENABLED": "false",
        "WHISPERX_ENABLED": "true",
        "MATOMO_DB_NAME": NEW_MATOMO_DB_NAME,
        "TRATT_SUBDOMAIN": ctx.args.tratt_subdomain,
        "DOCKER_SOCKET_PATH": f"/run/user/{ctx.pw.pw_uid}/podman/podman.sock",
    }
    env, secret_values, dropped, generated = plan_env(old, example, overrides)

    for key in ("BASE_DOMAIN", "ADMIN_EMAIL", "TRATT_SUBDOMAIN", "ABS_ROOT_PATH"):
        say(f"  {key}={env.get(key)}")
    if dropped:
        say(f"  dropped from the old .env (no longer used): {', '.join(dropped)}")
    if generated:
        say(f"  generated (not in old .env): {', '.join(generated)}")
    if old.get("MATOMO_DB_NAME", NEW_MATOMO_DB_NAME) != NEW_MATOMO_DB_NAME:
        say(f"  Matomo DB renamed {old['MATOMO_DB_NAME']} → {NEW_MATOMO_DB_NAME} (config patched in 'migrate')")

    if not DRY_RUN:
        (ctx.new / ".env").write_text(render_env(example_text, env))
        secrets_path = ctx.new / ".env.secrets"
        secrets_path.write_text(render_secrets(secret_values))
        secrets_path.chmod(0o600)
        for f in (".env", ".env.secrets"):
            shutil.chown(ctx.new / f, ctx.user, ctx.user)
    say("  ✓ wrote .env and .env.secrets", C_GREEN)
    ctx.mark("config", old_matomo_db=old.get("MATOMO_DB_NAME", NEW_MATOMO_DB_NAME))


def phase_sync_data(ctx: Ctx) -> None:
    heading("Sync data (rsync, re-runnable)")
    ctx.require("checkout")
    if "migrate" in ctx.state().get("done", {}) and not ctx.args.force:
        raise MigrationError(
            "'migrate' already ran — re-syncing would undo migrate-meta-json (pass --force, then re-run migrate)"
        )
    rsync = ["rsync", "-aHAX", "--delete", "--info=progress2,stats1"]
    pairs = [
        ("mounts/repositories/", "mounts/repositories/"),
        ("mounts/session-manager/unimported_audio/", "mounts/session-manager/unimported_audio/"),
        ("mounts/matomo/config/", "mounts/matomo/config/"),
    ]
    for src, dst in pairs:
        if not (ctx.old / src).exists():
            say(f"  (skip {src}: not in old install)")
            continue
        if not DRY_RUN:
            (ctx.new / dst).mkdir(parents=True, exist_ok=True)
        # Trailing slashes matter to rsync (copy contents, not the dir); Path drops them.
        ctx.run(rsync + [f"{ctx.old / src}/", f"{ctx.new / dst}/"])
        ctx.chown_user(ctx.new / dst)

    # certs/ is untracked in both trees; never --delete what the new tree added.
    ctx.run(["rsync", "-aHAX", str(ctx.old / "certs") + "/", str(ctx.new / "certs") + "/"])
    ctx.chown_user(ctx.new / "certs")
    mmdb = ctx.old / "mounts/matomo/DBIP-City.mmdb"
    if mmdb.is_file():
        ctx.run(["rsync", "-a", str(mmdb), str(ctx.new / "mounts/matomo/DBIP-City.mmdb")])
        ctx.chown_user(ctx.new / "mounts/matomo/DBIP-City.mmdb")

    if ctx.args.whisper_models_from:
        src = Path(ctx.args.whisper_models_from)
        if not DRY_RUN and not (src / "packages.json").is_file():
            raise MigrationError(f"{src} has no packages.json — expected a complete WhisperVault models directory")
        dst = ctx.new / "mounts/whisper/models"
        ctx.run(["rsync", "-a", "--info=progress2", str(src) + "/", str(dst) + "/"])
        ctx.chown_user(dst)
    elif not (ctx.new / "mounts/whisper/models/packages.json").exists():
        warn("no WhisperVault models yet — pass --whisper-models-from <dir> (a copy of an existing models dir)")
    ctx.mark("sync-data")


def phase_backup_old(ctx: Ctx) -> None:
    heading("Back up the old databases")
    old = ctx.old_env()
    if not DRY_RUN:
        ctx.work.mkdir(parents=True, exist_ok=True)
    stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    name = f"visp_mongodb_{MONGO_VERSION}_{stamp}"

    # Only the databases; nothing else of the old stack may write meanwhile.
    ctx.compose("up", "-d", "mongo", "matomo-db")
    ctx.wait_for(
        "old mongo",
        ["docker", "compose", "exec", "-T", "mongo", "mongosh", "--quiet", "--eval", "db.runCommand({ping:1})"],
        cwd=ctx.old,
    )
    ctx.compose(
        "exec",
        "-T",
        "mongo",
        "sh",
        "-c",
        "umask 077 && cat > /tmp/.migrate-dump.yaml",
        input=mongo_config_yaml(old["MONGO_ROOT_PASSWORD"]),
    )
    try:
        ctx.compose(
            "exec",
            "-T",
            "mongo",
            "mongodump",
            "--config=/tmp/.migrate-dump.yaml",
            "--username=root",
            "--authenticationDatabase=admin",
            f"--out=/tmp/{name}",
        )
    finally:
        ctx.compose("exec", "-T", "mongo", "rm", "-f", "/tmp/.migrate-dump.yaml", check=False)
    ctx.compose("exec", "-T", "mongo", "tar", "-czf", f"/tmp/{name}.tar.gz", "-C", "/tmp", name)
    mongo_archive = ctx.work / f"{name}.tar.gz"
    ctx.compose("cp", f"mongo:/tmp/{name}.tar.gz", str(mongo_archive))
    ctx.compose("exec", "-T", "mongo", "rm", "-rf", f"/tmp/{name}", f"/tmp/{name}.tar.gz", check=False)

    ctx.wait_for(
        "old matomo-db",
        [
            "docker",
            "compose",
            "exec",
            "-T",
            "matomo-db",
            "sh",
            "-c",
            'MYSQL_PWD="$MYSQL_ROOT_PASSWORD" exec mysqladmin -uroot ping',
        ],
        cwd=ctx.old,
    )
    matomo_dump = ctx.work / f"matomo_{stamp}.sql"
    say(f"  → {matomo_dump}")
    with open(os.devnull if DRY_RUN else matomo_dump, "w") as out:
        ctx.compose(
            "exec",
            "-T",
            "matomo-db",
            "sh",
            "-c",
            'MYSQL_PWD="$MYSQL_ROOT_PASSWORD" exec mysqldump -uroot --single-transaction --routines --triggers '
            '"$MYSQL_DATABASE"',
            stdout=out,
        )

    # 'down', not 'stop': 'restart: always' would bring stopped containers back on reboot.
    ctx.compose("down")
    ctx.chown_user(ctx.work)
    ctx.mark("backup-old", mongo_archive=str(mongo_archive), matomo_dump=str(matomo_dump))
    say("  ✓ old databases dumped and the old stack is down", C_GREEN)
    say("  Now re-run 'sync-data' to copy anything that changed since the first sync.")


def phase_install(ctx: Ctx) -> None:
    heading("Install quadlets (prod)")
    ctx.require("config", "sync-data")
    ctx.visp("install", "--mode", "prod")
    ctx.mark("install")


def phase_build(ctx: Ctx) -> None:
    heading("Build images")
    ctx.require("install")
    ctx.visp("build", "all", "--config", ctx.args.webclient_config)
    ctx.mark("build")


def phase_migrate(ctx: Ctx) -> None:
    heading("Restore and migrate data")
    ctx.require("backup-old", "sync-data", "install", "build")
    st = ctx.state()

    ctx.visp("stop", "all", check=False)
    ctx.visp("start", "mongo")
    ctx.wait_for(
        "mongo", ["podman", "exec", "mongo", "mongosh", "--quiet", "--eval", "db.runCommand({ping:1})"], as_user=True
    )
    # --drop makes this phase re-runnable: every run starts from the old data again.
    ctx.visp("restore", st.get("mongo_archive", "<mongo archive>"), "--force", "--drop")

    ctx.run(["python3", "scripts/migrate-permissions.py"], as_user=True, cwd=ctx.new)
    ctx.run(["python3", "scripts/migrate-permissions.py", "--apply"], as_user=True, cwd=ctx.new)
    ctx.run(["python3", "scripts/migrate-meta-json.py", "--apply"], as_user=True, cwd=ctx.new)

    for username in ctx.args.sysadmin:
        ctx.visp("users", "set-system-role", username, "sys_admin")
    if not ctx.args.sysadmin:
        warn("no --sysadmin given: nobody can create projects or open /admin until you grant it")

    # Matomo: a fresh MariaDB 10.11 (created with the old credentials) plus a logical
    # dump of the 10.3 data — so no in-place mariadb-upgrade is involved.
    config = ctx.new / "mounts/matomo/config/config.ini.php"
    old_db = st.get("old_matomo_db", NEW_MATOMO_DB_NAME)
    if old_db != NEW_MATOMO_DB_NAME and config.is_file() and not DRY_RUN:
        config.write_text(patch_matomo_dbname(config.read_text(), NEW_MATOMO_DB_NAME))
        say(f"  patched Matomo config dbname {old_db} → {NEW_MATOMO_DB_NAME}")
    # Matomo writes its config as www-data (uid 33 in the container).
    ctx.run(["podman", "unshare", "chown", "-R", "33:33", "mounts/matomo/config"], as_user=True, cwd=ctx.new)

    ctx.visp("start", "matomo-db")
    ctx.wait_for(
        "matomo-db",
        ["podman", "exec", "matomo-db", "sh", "-c", 'MYSQL_PWD="$MYSQL_ROOT_PASSWORD" exec mariadb-admin -uroot ping'],
        as_user=True,
    )
    dump = Path(st.get("matomo_dump", "<matomo dump>"))
    as_root = 'MYSQL_PWD="$MYSQL_ROOT_PASSWORD" exec mariadb -uroot'
    ctx.run(
        [
            "podman",
            "exec",
            "matomo-db",
            "sh",
            "-c",
            f'{as_root} -e "DROP DATABASE IF EXISTS {NEW_MATOMO_DB_NAME}; CREATE DATABASE {NEW_MATOMO_DB_NAME};"',
        ],
        as_user=True,
    )
    say(f"  importing {dump}")
    with open(os.devnull if DRY_RUN else dump) as f:
        ctx.run(
            ["podman", "exec", "-i", "matomo-db", "sh", "-c", f"{as_root} {NEW_MATOMO_DB_NAME}"], as_user=True, stdin=f
        )
    ctx.mark("migrate")


def phase_start(ctx: Ctx) -> None:
    heading("Start")
    ctx.require("migrate")
    ctx.visp("start", "all")
    ctx.wait_for("matomo", ["podman", "exec", "matomo", "test", "-f", "/var/www/html/console"], as_user=True)
    ctx.run(
        ["podman", "exec", "-u", "www-data", "matomo", "php", "/var/www/html/console", "core:update", "--yes"],
        as_user=True,
        check=False,
    )
    ctx.visp("status", check=False)
    ctx.mark("start")


def phase_edge(ctx: Ctx) -> None:
    heading("Edge: host TLS proxy → Apache :8081")
    conf = Path(ctx.args.proxy_conf)
    if not conf.is_file():
        raise MigrationError(f"{conf} not found — pass --proxy-conf")
    backup = conf.with_name(conf.name + ".pre-podman")
    if not backup.exists():
        ctx.run(["cp", "-p", str(conf), str(backup)])
    new_text = render_proxy_conf(ctx.args.proxy_cert, ctx.args.proxy_key, 8081)
    if not DRY_RUN:
        # Truncate in place: the file is bind-mounted into the container, and a
        # rename would leave the container on the old inode.
        with open(conf, "w") as f:
            f.write(new_text)
    say(f"  wrote {conf} (previous version: {backup})")
    c = ctx.args.proxy_container
    res = ctx.run(["docker", "exec", c, "nginx", "-t"], check=False)
    if res.returncode != 0:
        if not DRY_RUN:
            with open(conf, "w") as f:
                f.write(backup.read_text())
        raise MigrationError("nginx rejected the new config — restored the previous one")
    ctx.run(["docker", "exec", c, "nginx", "-s", "reload"])
    ctx.mark("edge")


def phase_verify(ctx: Ctx) -> None:
    heading("Verify")
    env = load_env_file(ctx.new / ".env")
    domain = env.get("BASE_DOMAIN", "")
    ctx.visp("status", check=False)
    ctx.visp("users", "list", check=False)
    ctx.visp("deploy", "status", check=False)

    rc, _ = ctx.probe(
        ["podman", "exec", "-u", "_shibd", "apache", "test", "-r", "/etc/certs/sp-cert/key.pem"], as_user=True
    )
    if rc == 0:
        say("  ✓ shibd can read the SP key", C_GREEN)
    else:
        warn("shibd cannot read /etc/certs/sp-cert/key.pem — check ownership/mode of certs/sp-cert/ (login will fail)")

    for label, cmd in (
        (
            "apache :8081",
            ["curl", "-s", "-o", "/dev/null", "-w", "%{http_code}", "-H", f"Host: {domain}", "http://127.0.0.1:8081/"],
        ),
        (
            "tls proxy :443",
            [
                "curl",
                "-sk",
                "-o",
                "/dev/null",
                "-w",
                "%{http_code}",
                "--resolve",
                f"{domain}:443:127.0.0.1",
                f"https://{domain}/",
            ],
        ),
    ):
        _, code = ctx.probe(cmd)
        say(f"  {label}: HTTP {code or '—'}", C_GREEN if code[:1] in ("2", "3") else C_RED)
    say("\nThen log in, open a project, a session and ARTIC, and run a short transcription.")


PHASES = {
    "preflight": phase_preflight,
    "host": phase_host,
    "checkout": phase_checkout,
    "config": phase_config,
    "sync-data": phase_sync_data,
    "backup-old": phase_backup_old,
    "install": phase_install,
    "build": phase_build,
    "migrate": phase_migrate,
    "start": phase_start,
    "edge": phase_edge,
    "verify": phase_verify,
}


def main() -> int:
    global DRY_RUN
    parser = argparse.ArgumentParser(
        description=__doc__.split("\n\n")[0],
        epilog="Phases, in order: " + " → ".join(PHASES),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("phase", choices=list(PHASES))
    parser.add_argument("--old-dir", required=True, help="old Docker Compose install (read, never modified)")
    parser.add_argument("--user", default="visp", help="service user to create/use (default: visp)")
    parser.add_argument("--home", default="/data/visp", help="service user's home, on the data volume")
    parser.add_argument("--branch", default="master", help="branch of this repo to check out")
    parser.add_argument("--webclient-config", default="visp", help="webclient build config (default: visp)")
    parser.add_argument(
        "--tratt-subdomain", default="octra", help="TRATT subdomain prefix; 'octra' keeps the old DNS name working"
    )
    parser.add_argument("--sysadmin", action="append", default=[], help="username to grant sys_admin (repeatable)")
    parser.add_argument("--whisper-models-from", help="local dir holding a complete WhisperVault models set")
    parser.add_argument("--proxy-conf", default="/opt/tls-proxy/proxy.conf")
    parser.add_argument("--proxy-container", default="tls-proxy-tls-proxy-1")
    parser.add_argument("--proxy-cert", default="/etc/tls/cert.pem", help="cert path as the proxy container sees it")
    parser.add_argument("--proxy-key", default="/etc/tls/key.pem", help="key path as the proxy container sees it")
    parser.add_argument("--dry-run", action="store_true", help="print commands instead of running them")
    parser.add_argument("--force", action="store_true", help="skip phase-order checks / overwrite generated files")
    args = parser.parse_args()
    DRY_RUN = args.dry_run

    if os.geteuid() != 0 and args.phase != "preflight":
        say("Run as root (sudo).", C_RED)
        return 1
    ctx = Ctx(args)
    try:
        PHASES[args.phase](ctx)
    except MigrationError as e:
        say(f"\n✗ {args.phase}: {e}", C_RED)
        return 1
    if DRY_RUN:
        say("\n(dry run — nothing was changed)", C_YELLOW)
    else:
        say(f"\n✓ {args.phase} done", C_GREEN)
    return 0


if __name__ == "__main__":
    sys.exit(main())
