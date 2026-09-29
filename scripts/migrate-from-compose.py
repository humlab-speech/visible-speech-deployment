#!/usr/bin/env python3
"""Deploy a new rootless-Podman VISP next to a legacy Docker Compose install and cut over to it.

The old install is never modified: its files are only read, and its containers are
only started, stopped and exec'd into (to dump the databases). The new instance is a
fresh checkout owned by a dedicated service user, with a fresh .env and freshly
generated secrets. Only data is carried over: project repositories, the Mongo
application databases, the Matomo database and config, the Shibboleth SP
certificate, and a few non-secret settings (BASE_DOMAIN, ADMIN_EMAIL, ...).

Run as root, one phase at a time. Every phase can be previewed with --dry-run, logs
everything (secrets masked) under /var/log/visp-migrate/, and refuses to run before
the phases it depends on.

  Build the new instance (the old one keeps serving users throughout):
    preflight   read-only checks of the host, the old install and the target volume
    host        packages, service user (home on the data volume), subuid/subgid,
                lingering, user podman socket, rootless smoke test
    checkout    clone this repository for the service user, then 'deploy update'
    config      fresh .env and .env.secrets
    sync-files  rsync repositories, certs, Matomo config, unimported audio and
                WhisperVault models from the old install
    install     './visp.py install --mode prod'
    build       './visp.py build all --config <webclient config>'
    databases   dump the old Mongo application databases and Matomo; restore them,
                check document counts, run migrate-permissions and
                migrate-meta-json, grant sys_admin, import Matomo
    start       start the new instance
    verify      health checks of the new instance

  Try it:
    preview     add a TLS listener on 127.0.0.1:8443 -> new instance to the host proxy
                (production traffic is untouched; reach it through an SSH tunnel)

  Switch:
    cutover     stop the old writers, re-sync files, re-dump and re-migrate the
                databases, stop the old stack, start the new one and point the host
                proxy at it
    rollback    point the proxy back, stop the new instance, start the old one

  Anytime:
    status      what has run, when, and where the dumps and logs are

sync-files and databases can be re-run at any point before the cutover: databases
always starts over from a fresh dump, and re-syncing invalidates it so 'start' refuses
until the migrations have been re-applied to the re-synced files.

Example:
  sudo ./scripts/migrate-from-compose.py preflight --old-dir /srv/old-visp
  sudo ./scripts/migrate-from-compose.py databases --old-dir /srv/old-visp \\
       --sysadmin some_user_at_example_dot_org --sysadmin other_user_at_example_dot_org
"""

from __future__ import annotations

import argparse
import datetime
import fcntl
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
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from vispctl.env import load_env_file  # noqa: E402

REPO_URL = "https://github.com/humlab-speech/visible-speech-deployment"
MONGO_DUMP_PREFIX = "visp_mongodb_6.0.27"  # './visp.py restore' expects visp_mongodb_*
NEW_MATOMO_DB = "matomo_db"  # hard-coded in quadlets/prod/matomo*.container
NEW_HTTP_PORT = 8081  # PublishPort of quadlets/prod/apache.container
PREVIEW_LISTEN = "127.0.0.1:8443"
SUBID_COUNT = 65536
LOG_DIR = Path("/var/log/visp-migrate")
STATE_DIR = Path("/var/lib/visp-migrate")
SYSTEM_DATABASES = {"admin", "config", "local"}
CORE_CONTAINERS = {"apache", "session-manager", "mongo", "emu-webapp-server", "wsrng-server", "matomo", "matomo-db"}

# Non-secret settings worth keeping from the old .env. Everything else starts from
# the new .env-example, and every secret is generated fresh.
CARRY_KEYS = ["BASE_DOMAIN", "ADMIN_EMAIL", "PROJECT_NAME", "ACCESS_LIST_ENABLED", "EMUDB_INTEGRATION_ENABLED"]

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
FIXED_SECRETS = {"MATOMO_DB_USER": "matomo"}  # a user name, not a credential

APT_PACKAGES = [
    "podman",
    "uidmap",
    "slirp4netns",
    "passt",
    "netavark",
    "aardvark-dns",
    "systemd-container",  # machinectl, for working as the service user by hand
    "git",
    "rsync",
    "curl",
]

PREVIEW_BEGIN = "# >>> visp-migrate preview (remove with: migrate-from-compose.py rollback) >>>"
PREVIEW_END = "# <<< visp-migrate preview <<<"

RED, GREEN, YELLOW, CYAN, BOLD, NC = "\033[0;31m", "\033[0;32m", "\033[1;33m", "\033[0;36m", "\033[1m", "\033[0m"

DRY_RUN = False


class MigrationError(Exception):
    pass


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
    old: dict[str, str], example: dict[str, str], overrides: dict[str, str]
) -> tuple[dict[str, str], list[str]]:
    """Values for the new .env: override, else a carried old value, else the example default.

    Only CARRY_KEYS are taken from the old .env, and secrets are left out entirely.
    Returns ``(env, carried_keys)``.
    """
    env, carried = {}, []
    for key, default in example.items():
        if key in SECRET_KEYS:
            continue
        if key in overrides:
            env[key] = overrides[key]
        elif key in CARRY_KEYS and old.get(key):
            env[key] = old[key]
            carried.append(key)
        else:
            env[key] = default
    return env, carried


def plan_secrets(existing: dict[str, str]) -> tuple[dict[str, str], list[str]]:
    """Keep existing secrets, generate the missing ones. Returns ``(secrets, generated_keys)``."""
    values, generated = {}, []
    for key in SECRET_KEYS:
        if existing.get(key):
            values[key] = existing[key]
        else:
            values[key] = FIXED_SECRETS.get(key) or generate_secret()
            generated.append(key)
    return values, generated


def render_env(example_text: str, values: dict[str, str]) -> str:
    """Rewrite .env-example text with ``values``, keeping comments and order; drop other keys."""
    out = []
    for line in example_text.splitlines():
        m = re.match(r"^([A-Z0-9_]+)=", line)
        if not m:
            out.append(line)
        elif m.group(1) in values:
            out.append(f"{m.group(1)}={values[m.group(1)]}")
    return "\n".join(out) + "\n"


def render_secrets(values: dict[str, str]) -> str:
    lines = ["# Generated by scripts/migrate-from-compose.py"]
    lines += [f"{k}={v}" for k, v in values.items()]
    return "\n".join(lines) + "\n"


def patch_ini_section(text: str, section: str, values: dict[str, str]) -> str:
    """Set ``key = "value"`` lines inside one ini section, adding the keys it lacks."""
    out: list[str] = []
    current = None
    seen: set[str] = set()

    def add_missing() -> None:
        if out and not out[-1].endswith("\n"):
            out[-1] += "\n"
        out.extend(f'{key} = "{value}"\n' for key, value in values.items() if key not in seen)

    for line in text.splitlines(keepends=True):
        stripped = line.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            if current == section:
                add_missing()
            current = stripped
            out.append(line)
            continue
        m = re.match(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=", line)
        if current == section and m and m.group(1) in values:
            seen.add(m.group(1))
            out.append(f'{m.group(1)} = "{values[m.group(1)]}"\n')
            continue
        out.append(line)
    if current == section:
        add_missing()
    return "".join(out)


_UPGRADE_MAP = """map $http_upgrade $connection_upgrade {
    default upgrade;
    ''      close;
}
"""


def _proxy_server_block(listen: str, cert: str, key: str, upstream_port: int) -> str:
    return f"""server {{
    listen {listen} ssl;
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
        # WebSocket: the main app cannot log in without it
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection $connection_upgrade;
        proxy_connect_timeout 60s;
        proxy_send_timeout 86400s;
        proxy_read_timeout 86400s;
    }}
}}
"""


def render_proxy_conf(cert: str, key: str, upstream_port: int) -> str:
    """Production nginx config: TLS from the load balancer terminates here, then Apache."""
    return (
        "# Generated by scripts/migrate-from-compose.py (cutover)\n"
        + _UPGRADE_MAP
        + "\n"
        + _proxy_server_block("443", cert, key, upstream_port)
    )


def strip_preview_block(conf: str) -> str:
    pattern = r"\n*" + re.escape(PREVIEW_BEGIN) + r".*?" + re.escape(PREVIEW_END) + r"\n?"
    return re.sub(pattern, "\n", conf, flags=re.S).rstrip("\n") + "\n"


def add_preview_block(conf: str, cert: str, key: str, upstream_port: int, listen: str = PREVIEW_LISTEN) -> str:
    """Append a loopback-only listener for the new instance; ``conf`` itself stays as it is."""
    conf = strip_preview_block(conf)
    has_map = "map $http_upgrade $connection_upgrade" in conf
    block = (
        f"{PREVIEW_BEGIN}\n"
        + ("" if has_map else _UPGRADE_MAP)
        + _proxy_server_block(listen, cert, key, upstream_port)
        + f"{PREVIEW_END}\n"
    )
    return conf.rstrip("\n") + "\n\n" + block


def app_databases(names: list[str]) -> list[str]:
    return sorted(n for n in names if n not in SYSTEM_DATABASES)


COUNT_JS = (
    "const out = {};"
    "db.adminCommand({listDatabases: 1, nameOnly: true}).databases"
    ".map(d => d.name).filter(n => !['admin', 'config', 'local'].includes(n))"
    ".forEach(n => { const x = db.getSiblingDB(n); out[n] = {};"
    " x.getCollectionNames().forEach(c => { out[n][c] = x.getCollection(c).countDocuments({}); }); });"
    "print(JSON.stringify(out));"
)


def compare_counts(old: dict, new: dict) -> list[str]:
    """Differences between two {db: {collection: count}} maps, as readable lines."""
    problems = []
    for dbname, colls in old.items():
        for coll, count in colls.items():
            got = new.get(dbname, {}).get(coll)
            if got != count:
                problems.append(f"{dbname}.{coll}: old {count}, new {got}")
    return problems


def mongo_config_yaml(password: str) -> str:
    """mongodump --config file; a JSON string is valid YAML and escapes everything."""
    return f"password: {json.dumps(password)}\n"


# ── logging ───────────────────────────────────────────────────────────────────


class Log:
    """Tee to the terminal and a per-run log file, masking known secrets in both."""

    def __init__(self) -> None:
        self.file = None
        self.path: Path | None = None
        self.secrets: set[str] = set()

    def open(self, phase: str) -> None:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        LOG_DIR.chmod(0o700)
        stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        self.path = LOG_DIR / f"{stamp}_{phase}{'_dry-run' if DRY_RUN else ''}.log"
        self.file = open(self.path, "a", buffering=1)
        self.path.chmod(0o600)

    def add_secret(self, value: str | None) -> None:
        if value and len(value) >= 8:
            self.secrets.add(value)

    def mask(self, text: str) -> str:
        for value in sorted(self.secrets, key=len, reverse=True):
            text = text.replace(value, "********")
        return text

    def write(self, text: str, color: str = "", echo: bool = True) -> None:
        text = self.mask(text)
        if self.file:
            ts = datetime.datetime.now().strftime("%H:%M:%S")
            for line in text.splitlines() or [""]:
                self.file.write(f"{ts} {line}\n")
        if echo:
            print(f"{color}{text}{NC}" if color else text, flush=True)


LOG = Log()


def say(msg: str, color: str = "") -> None:
    LOG.write(msg, color)


def heading(msg: str) -> None:
    LOG.write(f"\n=== {msg} ===", CYAN)


def ok(msg: str) -> None:
    LOG.write(f"  ✓ {msg}", GREEN)


def warn(msg: str) -> None:
    LOG.write(f"  ⚠ {msg}", YELLOW)


def _user_exists(name: str) -> bool:
    try:
        pwd.getpwnam(name)
        return True
    except KeyError:
        return False


# ── context: paths, state, command execution ──────────────────────────────────


class Ctx:
    def __init__(self, args: argparse.Namespace):
        self.args = args
        self.old = Path(args.old_dir).resolve() if args.old_dir else None
        self.user = args.user
        self.home = Path(args.home).resolve()
        self.new = self.home / "visible-speech-deployment"
        self.dumps = self.home / "migration-dumps"
        self.state_path = STATE_DIR / "state.json"

    # paths / identity --------------------------------------------------------
    @property
    def pw(self) -> pwd.struct_passwd:
        try:
            return pwd.getpwnam(self.user)
        except KeyError as e:
            raise MigrationError(f"user {self.user!r} does not exist yet (run the 'host' phase)") from e

    def need_old(self) -> Path:
        if not self.old:
            raise MigrationError("--old-dir is required for this phase")
        return self.old

    def check_paths(self) -> None:
        """Refuse dangerous path combinations, and settings that differ from earlier runs."""
        if self.old:
            if self.old == self.new or self.old in self.new.parents or self.new in self.old.parents:
                raise MigrationError(f"old ({self.old}) and new ({self.new}) trees overlap")
        if str(self.home).startswith("/home/"):
            warn(f"{self.home} is under /home — container images alone need ~60 G")
        recorded = self.state().get("settings", {})
        for key, value in (("old_dir", self.old), ("home", self.home), ("user", self.user)):
            if value is not None and recorded.get(key) and recorded[key] != str(value) and not self.args.force:
                raise MigrationError(f"{key} was {recorded[key]} in earlier phases, now {value} (--force if intended)")

    def new_path(self, rel: str) -> Path:
        """A path inside the new tree; guards against ever writing anywhere else."""
        root = self.new.resolve()
        p = (root / rel).resolve()
        if root not in p.parents and p != root:
            raise MigrationError(f"refusing to write outside the new tree: {p}")
        return p

    # state -------------------------------------------------------------------
    def state(self) -> dict:
        try:
            return json.loads(self.state_path.read_text())
        except (OSError, ValueError):
            return {}

    def save_state(self, st: dict) -> None:
        if DRY_RUN:
            return
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        tmp = self.state_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(st, indent=2) + "\n")
        tmp.replace(self.state_path)

    def mark(self, phase: str, **extra) -> None:
        st = self.state()
        st.setdefault("done", {})[phase] = datetime.datetime.now().isoformat(timespec="seconds")
        settings = st.setdefault("settings", {})
        settings.update({"home": str(self.home), "user": self.user})
        if self.old:
            settings["old_dir"] = str(self.old)
        st.update(extra)
        self.save_state(st)

    def invalidate(self, *phases: str) -> None:
        st = self.state()
        for p in phases:
            st.get("done", {}).pop(p, None)
        self.save_state(st)

    def done(self, phase: str) -> bool:
        return phase in self.state().get("done", {})

    def require(self, *phases: str) -> None:
        missing = [p for p in phases if not self.done(p)]
        if missing and not self.args.force:
            raise MigrationError(f"run {', '.join(missing)} first (or pass --force)")

    # commands ----------------------------------------------------------------
    def _wrap(self, cmd: list[str], as_user: bool, cwd: Path | None) -> list[str]:
        if not as_user:
            return cmd
        # A transient unit in the service user's own systemd manager: the session
        # 'machinectl shell' gives (user cgroup, user bus, XDG_RUNTIME_DIR, which
        # rootless Podman and 'systemctl --user' need), but scriptable: exit codes
        # and stdin pass through.
        return [
            "systemd-run",
            f"--machine={self.user}@.host",
            "--user",
            "--pipe",
            "--wait",
            "--quiet",
            "--collect",
            "--service-type=exec",
            f"--working-directory={cwd or self.home}",
            "--setenv=PYTHONUNBUFFERED=1",
            "--",
            *cmd,
        ]

    def run(
        self,
        cmd: list[str],
        *,
        as_user: bool = False,
        cwd: Path | None = None,
        input_text: str | None = None,
        stdin_path: Path | None = None,
        stdout_path: Path | None = None,
        check: bool = True,
        capture: bool = False,
        quiet: bool = False,
        always: bool = False,
    ) -> subprocess.CompletedProcess:
        """Run a command, logging it and its output.

        ``capture`` returns stdout (logged to the file only) with stderr kept apart;
        ``always`` runs it even under --dry-run (read-only probes).
        """
        full = self._wrap(cmd, as_user, cwd)
        shown = (f"[{self.user}] " if as_user else "") + shlex.join(cmd)
        if cwd:
            shown = f"(cd {cwd}) {shown}"
        if stdin_path:
            shown += f" < {stdin_path}"
        if stdout_path:
            shown += f" > {stdout_path}"
        LOG.write(f"  $ {shown}", BOLD, echo=not quiet)
        if DRY_RUN and not always:
            return subprocess.CompletedProcess(full, 0, "", "")

        run_cwd = None if as_user else cwd
        if capture:
            res = subprocess.run(
                full,
                cwd=run_cwd,
                input=input_text,
                stdin=None if input_text is not None else subprocess.DEVNULL,
                capture_output=True,
                text=True,
            )
            LOG.write(f"    -> exit {res.returncode}", echo=False)
            for stream in (res.stdout, res.stderr):
                if stream and stream.strip():
                    LOG.write("    " + stream.strip().replace("\n", "\n    "), echo=False)
        else:
            if stdin_path:
                stdin = open(stdin_path)
            else:
                stdin = subprocess.PIPE if input_text is not None else subprocess.DEVNULL
            stdout = open(stdout_path, "w") if stdout_path else subprocess.PIPE
            try:
                proc = subprocess.Popen(
                    full,
                    cwd=run_cwd,
                    stdin=stdin,
                    stdout=stdout,
                    stderr=subprocess.PIPE if stdout_path else subprocess.STDOUT,
                    text=True,
                    bufsize=1,
                )
                if input_text is not None:
                    proc.stdin.write(input_text)
                    proc.stdin.close()
                for line in proc.stderr if stdout_path else proc.stdout:
                    LOG.write("    " + line.rstrip("\n"))
                rc = proc.wait()
            finally:
                if stdin_path:
                    stdin.close()
                if stdout_path:
                    stdout.close()
            res = subprocess.CompletedProcess(full, rc, "", "")
        if check and res.returncode != 0:
            raise MigrationError(f"command failed (exit {res.returncode}): {LOG.mask(shown)}")
        return res

    def probe(
        self, cmd: list[str], *, as_user: bool = False, cwd: Path | None = None, input_text: str | None = None
    ) -> tuple[int, str]:
        """A read-only command that runs even under --dry-run. Returns (exit code, stdout)."""
        if as_user and not _user_exists(self.user):
            return 1, ""
        res = self.run(
            cmd, as_user=as_user, cwd=cwd, input_text=input_text, check=False, capture=True, quiet=True, always=True
        )
        return res.returncode, (res.stdout or "").strip()

    def visp(self, *argv: str, check: bool = True) -> subprocess.CompletedProcess:
        return self.run(["python3", "./visp.py", *argv], as_user=True, cwd=self.new, check=check)

    def compose_cmd(self, *argv: str) -> list[str]:
        return ["docker", "compose", "--project-directory", str(self.need_old()), *argv]

    def compose(self, *argv: str, **kw) -> subprocess.CompletedProcess:
        return self.run(self.compose_cmd(*argv), cwd=self.need_old(), **kw)

    def wait_for(self, what: str, cmd: list[str], *, as_user: bool = False, timeout: int = 180) -> None:
        if DRY_RUN:
            say(f"  (would wait for {what})")
            return
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self.probe(cmd, as_user=as_user)[0] == 0:
                ok(f"{what} is up")
                return
            time.sleep(3)
        raise MigrationError(f"timed out after {timeout}s waiting for {what}")

    def confirm(self, prompt: str, expected: str) -> None:
        if self.args.yes or DRY_RUN:
            return
        say(prompt, YELLOW)
        try:
            answer = input(f"  Type {expected!r} to continue: ").strip()
        except EOFError:
            answer = ""
        LOG.write(f"  answer: {answer!r}", echo=False)
        if answer != expected:
            raise MigrationError("not confirmed — nothing was changed")

    # environment files -------------------------------------------------------
    def old_env(self) -> dict[str, str]:
        env = load_env_file(self.need_old() / ".env")
        if not env:
            raise MigrationError(f"{self.old}/.env is missing or empty")
        for key in SECRET_KEYS:
            LOG.add_secret(env.get(key))
        return env

    def new_secrets(self) -> dict[str, str]:
        values = load_env_file(self.new / ".env.secrets")
        for value in values.values():
            LOG.add_secret(value)
        return values

    def new_env(self) -> dict[str, str]:
        return load_env_file(self.new / ".env")

    def chown_user(self, path: Path) -> None:
        self.run(["chown", "-R", f"{self.user}:{self.user}", str(path)])

    def free_bytes(self, path: Path) -> int:
        while not path.exists():
            path = path.parent
        return shutil.disk_usage(path).free

    def dir_bytes(self, path: Path) -> int:
        if not path.exists():
            return 0
        _, out = self.probe(["du", "-sb", str(path)])
        return int(out.split()[0]) if out else 0

    # stacks ------------------------------------------------------------------
    def new_running(self) -> list[str]:
        rc, out = self.probe(["podman", "ps", "--format", "{{.Names}}"], as_user=True)
        return [n for n in out.splitlines() if n] if rc == 0 else []

    def old_services(self) -> list[str]:
        rc, out = self.probe(self.compose_cmd("config", "--services"))
        if rc != 0:
            raise MigrationError("could not read the old compose configuration")
        return [s for s in out.splitlines() if s]

    def mongo_counts(self, which: str, password: str) -> dict:
        """{db: {collection: count}} of the application databases; the password goes via stdin."""
        shell = ["mongosh", "--quiet", "-u", "root", "-p", "--authenticationDatabase", "admin", "--eval", COUNT_JS]
        if which == "old":
            rc, out = self.probe(self.compose_cmd("exec", "-T", "mongo", *shell), input_text=password + "\n")
        else:
            rc, out = self.probe(["podman", "exec", "-i", "mongo", *shell], as_user=True, input_text=password + "\n")
        if DRY_RUN and rc != 0:
            return {}
        if rc != 0:
            raise MigrationError(f"could not count documents in the {which} Mongo (see the log)")
        try:
            return json.loads(out.splitlines()[-1])
        except (ValueError, IndexError) as e:
            raise MigrationError(f"unexpected output counting the {which} Mongo (see the log)") from e


# ── phases ────────────────────────────────────────────────────────────────────


def phase_preflight(ctx: Ctx) -> None:
    heading("Preflight (read-only)")
    old = ctx.need_old()
    problems: list[str] = []

    def check(cond: bool, good: str, bad: str, fatal: bool = True) -> None:
        if cond:
            ok(good)
        elif fatal:
            LOG.write(f"  ✗ {bad}", RED)
            problems.append(bad)
        else:
            warn(bad)

    _, osr = ctx.probe(["sh", "-c", ". /etc/os-release && echo $ID $VERSION_ID"])
    check(osr.startswith("ubuntu"), f"OS: {osr}", f"untested OS: {osr}", fatal=False)

    env = load_env_file(old / ".env")
    check(bool(env), f"old .env found in {old}", f"no readable .env in {old}")
    check(bool(env.get("BASE_DOMAIN")), f"BASE_DOMAIN={env.get('BASE_DOMAIN')}", "old .env has no BASE_DOMAIN")
    check(bool(env.get("MONGO_ROOT_PASSWORD")), "old Mongo password available", "old .env has no MONGO_ROOT_PASSWORD")

    rc, services = ctx.probe(ctx.compose_cmd("config", "--services"))
    names = services.split()
    check(rc == 0, f"old compose services: {' '.join(names)}", "cannot read the old compose configuration")
    for svc in ("mongo", "matomo-db"):
        check(svc in names, f"old compose has '{svc}'", f"old compose has no '{svc}' service")
    _, running = ctx.probe(ctx.compose_cmd("ps", "--format", "{{.Service}}"))
    say(f"  old services running: {' '.join(running.split()) or 'none'}")

    for rel, fatal in (("mounts/repositories", True), ("certs/sp-cert", True), ("mounts/matomo/config", False)):
        check((old / rel).exists(), f"old {rel} exists", f"old {rel} is missing", fatal=fatal)

    repos = ctx.dir_bytes(old / "mounts/repositories")
    need = repos + 90 * 1024**3  # copy + images (~60 G) + dumps + headroom
    free = ctx.free_bytes(ctx.home)
    check(
        free > need,
        f"{free / 1024**3:.0f} G free for {ctx.home} (need ~{need / 1024**3:.0f} G)",
        f"only {free / 1024**3:.0f} G free for {ctx.home}, need ~{need / 1024**3:.0f} G",
    )
    base = ctx.home.parent
    _, fstype = ctx.probe(["stat", "-f", "-c", "%T", str(base)])
    if fstype == "xfs":
        _, xfs = ctx.probe(["xfs_info", str(base)])
        check("ftype=1" in xfs, "XFS with ftype=1 (overlayfs works)", "XFS without ftype=1: overlayfs will not work")
    else:
        say(f"  filesystem for {base}: {fstype}")

    for port, what, fatal in ((NEW_HTTP_PORT, "the new Apache", True), (8443, "'preview'", False)):
        rc, _ = ctx.probe(["sh", "-c", f"ss -Hltn 'sport = :{port}' | grep -q ."])
        check(rc != 0, f"port {port} is free", f"port {port} is in use (needed by {what})", fatal=fatal)

    conf = Path(ctx.args.proxy_conf)
    check(conf.is_file(), f"proxy config {conf}", f"no proxy config at {conf} (--proxy-conf)", fatal=False)
    rc, _ = ctx.probe(["docker", "inspect", ctx.args.proxy_container])
    check(rc == 0, f"proxy container {ctx.args.proxy_container}", "proxy container not found", fatal=False)

    _, ref = ctx.probe(["git", "ls-remote", REPO_URL, f"refs/heads/{ctx.args.branch}"])
    check(bool(ref), f"branch {ctx.args.branch} exists on GitHub", f"branch {ctx.args.branch} not found on GitHub")

    if _user_exists(ctx.user):
        home = Path(ctx.pw.pw_dir).resolve()
        check(home == ctx.home, f"service user {ctx.user} exists", f"user {ctx.user} has home {home}, not {ctx.home}")
    else:
        say(f"  service user {ctx.user} will be created with home {ctx.home}")

    _, userns = ctx.probe(["sysctl", "-n", "kernel.apparmor_restrict_unprivileged_userns"])
    if userns == "1":
        say("  AppArmor restricts unprivileged user namespaces — 'host' verifies rootless Podman works")

    say("\n  Outside this script — arrange before the cutover:", BOLD)
    say(f"   • DNS / load balancer for artic., app., recorder., matomo. and {ctx.args.tratt_subdomain}.")
    say("   • if Ansible manages users or /etc/subuid on this host, add the service user there too")
    say("   • a complete WhisperVault models directory (with packages.json) for --whisper-models-from")
    if problems:
        raise MigrationError(f"{len(problems)} problem(s) — fix them and re-run preflight")
    ctx.mark("preflight")


def phase_host(ctx: Ctx) -> None:
    heading("Host: packages, service user, rootless Podman")
    ctx.require("preflight")
    ctx.run(["apt-get", "update"])
    ctx.run(["apt-get", "install", "-y", *APT_PACKAGES])

    if _user_exists(ctx.user):
        say(f"  user {ctx.user} already exists")
    else:
        ctx.run(
            ["useradd", "--create-home", "--home-dir", str(ctx.home), "--shell", "/bin/bash", "--user-group", ctx.user]
        )
    if not DRY_RUN:
        ctx.home.chmod(0o750)

    # useradd usually assigns ranges itself; add one only where it did not.
    for path, flag in (("/etc/subuid", "--add-subuids"), ("/etc/subgid", "--add-subgids")):
        entries = parse_subid_file(Path(path).read_text() if Path(path).exists() else "")
        if any(name == ctx.user for name, _, _ in entries):
            say(f"  {path}: {ctx.user} has a range")
            continue
        start = next_subid_start(entries)
        ctx.run(["usermod", flag, f"{start}-{start + SUBID_COUNT - 1}", ctx.user])

    ctx.run(["loginctl", "enable-linger", ctx.user])
    if not DRY_RUN:
        uid = ctx.pw.pw_uid
        ctx.run(["systemctl", "start", f"user@{uid}.service"])
        ctx.wait_for("the service user's systemd", ["test", "-S", f"/run/user/{uid}/bus"])
    # Keep the API service running, not only socket-activated (see AGENTS.md).
    ctx.run(["systemctl", "--user", "enable", "--now", "podman.socket", "podman.service"], as_user=True)

    _, backend = ctx.probe(["podman", "info", "--format", "{{.Host.NetworkBackend}}"], as_user=True)
    if not DRY_RUN and backend != "netavark":
        raise MigrationError(f"Podman network backend is {backend!r}; netavark is required")
    ctx.run(["podman", "run", "--rm", "docker.io/library/alpine:3.23", "true"], as_user=True)
    ok("rootless Podman works for the service user")
    ctx.mark("host")


def phase_checkout(ctx: Ctx) -> None:
    heading("Checkout")
    ctx.require("host")
    if (ctx.new / ".git").exists():
        _, dirty = ctx.probe(["git", "status", "--porcelain", "--untracked-files=no"], as_user=True, cwd=ctx.new)
        if dirty and not ctx.args.force:
            raise MigrationError(f"{ctx.new} has local changes to tracked files:\n{dirty}")
        ctx.run(["git", "fetch", "origin"], as_user=True, cwd=ctx.new)
        ctx.run(["git", "checkout", ctx.args.branch], as_user=True, cwd=ctx.new)
        ctx.run(["git", "pull", "--ff-only"], as_user=True, cwd=ctx.new)
    else:
        ctx.run(["git", "clone", "--branch", ctx.args.branch, REPO_URL, str(ctx.new)], as_user=True)
    ctx.visp("deploy", "update")
    ctx.mark("checkout")


def phase_config(ctx: Ctx) -> None:
    heading("Config: fresh .env and .env.secrets")
    ctx.require("checkout")
    old = ctx.old_env()
    env_path, secrets_path = ctx.new_path(".env"), ctx.new_path(".env.secrets")

    if env_path.exists():
        if not ctx.args.force:
            raise MigrationError(f"{env_path} exists — pass --force to regenerate it (a backup is kept)")
        ctx.run(
            ["cp", "-p", str(env_path), str(env_path.with_name(f".env.bak-{datetime.datetime.now():%Y%m%d_%H%M%S}"))]
        )

    uid = ctx.pw.pw_uid if _user_exists(ctx.user) else "<uid>"
    overrides = {
        "ABS_ROOT_PATH": str(ctx.new),
        "HTTP_PORT": str(NEW_HTTP_PORT),
        "HTTP_PROTOCOL": "https",
        "LOCAL_IDP_ENABLED": "false",
        "WHISPERX_ENABLED": "true",
        "MATOMO_DB_NAME": NEW_MATOMO_DB,
        "TRATT_SUBDOMAIN": ctx.args.tratt_subdomain,
        "DOCKER_SOCKET_PATH": f"/run/user/{uid}/podman/podman.sock",
    }
    if ctx.args.proxy_blocked_cidrs is not None:
        overrides["PROXY_BLOCKED_CIDRS"] = ctx.args.proxy_blocked_cidrs
    env, carried = plan_env(old, load_env_file(ctx.new / ".env-example"), overrides)
    for key in carried:
        say(f"  from the old .env: {key}={env[key]}")
    for key in ("ABS_ROOT_PATH", "HTTP_PORT", "TRATT_SUBDOMAIN", "LOCAL_IDP_ENABLED", "WHISPERX_ENABLED"):
        say(f"  set:               {key}={env[key]}")
    if not env.get("PROXY_BLOCKED_CIDRS"):
        warn("PROXY_BLOCKED_CIDRS is empty: Jupyter sessions can reach internal networks (--proxy-blocked-cidrs)")

    # Secrets are generated once and never replaced: the new databases are created
    # with them, so regenerating would lock the new instance out of its own data.
    existing = load_env_file(secrets_path) if secrets_path.exists() else {}
    secret_values, generated = plan_secrets(existing)
    for value in secret_values.values():
        LOG.add_secret(value)
    say(f"  secrets generated: {', '.join(generated) or 'none (existing ones kept)'}")

    if not DRY_RUN:
        env_path.write_text(render_env((ctx.new / ".env-example").read_text(), env))
        old_umask = os.umask(0o077)
        try:
            secrets_path.write_text(render_secrets(secret_values))
        finally:
            os.umask(old_umask)
        secrets_path.chmod(0o600)
        for p in (env_path, secrets_path):
            shutil.chown(p, ctx.user, ctx.user)
    ok("wrote .env and .env.secrets")
    ctx.mark("config")


def _sync_files(ctx: Ctx) -> None:
    old = ctx.need_old()
    running = ctx.new_running()
    if running:
        raise MigrationError(
            f"the new instance is running ({', '.join(running)}); stop it first: './visp.py stop all' as {ctx.user}"
        )
    repos_dst = ctx.new_path("mounts/repositories")
    needed = ctx.dir_bytes(old / "mounts/repositories") - ctx.dir_bytes(repos_dst)
    if needed > 0 and ctx.free_bytes(repos_dst) < needed * 1.05:
        raise MigrationError(f"not enough free space: {needed / 1024**3:.0f} G more are needed")

    # Mirrors: --delete, so a re-sync reproduces the old tree exactly.
    for rel in ("mounts/repositories", "mounts/session-manager/unimported_audio", "mounts/matomo/config"):
        src, dst = old / rel, ctx.new_path(rel)
        if not src.exists():
            say(f"  (skip {rel}: not in the old install)")
            continue
        if not DRY_RUN:
            dst.mkdir(parents=True, exist_ok=True)
        # Trailing slashes matter to rsync (copy the contents, not the directory).
        ctx.run(["rsync", "-aHAX", "--delete", "--info=stats1", f"{src}/", f"{dst}/"])
        ctx.chown_user(dst)

    # Additive: never --delete here, the new tree may hold files of its own.
    ctx.run(["rsync", "-aHAX", f"{old / 'certs'}/", f"{ctx.new_path('certs')}/"])
    ctx.chown_user(ctx.new_path("certs"))
    mmdb = old / "mounts/matomo/DBIP-City.mmdb"
    if mmdb.is_file() and mmdb.stat().st_size > 0:
        ctx.run(["rsync", "-a", str(mmdb), str(ctx.new_path("mounts/matomo/DBIP-City.mmdb"))])
        ctx.chown_user(ctx.new_path("mounts/matomo/DBIP-City.mmdb"))

    models = ctx.new_path("mounts/whisper/models")
    if ctx.args.whisper_models_from:
        src = Path(ctx.args.whisper_models_from).resolve()
        if not (src / "packages.json").is_file():
            raise MigrationError(f"{src} has no packages.json — expected a complete WhisperVault models directory")
        if not DRY_RUN:
            models.mkdir(parents=True, exist_ok=True)
        ctx.run(["rsync", "-a", "--info=stats1", f"{src}/", f"{models}/"])
        ctx.chown_user(models)
    elif not (models / "packages.json").exists():
        warn("no WhisperVault models yet: transcription will not work (--whisper-models-from <dir>)")

    _, count = ctx.probe(["sh", "-c", f"find {shlex.quote(str(repos_dst))} -name '*.meta_json' | wc -l"])
    say(f"  .meta_json files now in the new tree: {count or '?'} ('databases' renames them)")


def phase_sync_files(ctx: Ctx) -> None:
    heading("Sync files from the old install (rsync, re-runnable)")
    ctx.require("config")
    if ctx.done("cutover") and not ctx.args.force:
        raise MigrationError("the cutover is done — re-syncing would overwrite live data")
    _sync_files(ctx)
    # The file migrations have to be re-applied to what was just copied.
    ctx.invalidate("databases", "start", "verify")
    ctx.mark("sync-files")


def phase_install(ctx: Ctx) -> None:
    heading("Install quadlets (prod)")
    ctx.require("config", "sync-files")
    ctx.visp("install", "--mode", "prod")
    # Nothing may run on half-migrated data.
    ctx.visp("stop", "all", check=False)
    ctx.mark("install")


def phase_build(ctx: Ctx) -> None:
    heading("Build images")
    ctx.require("install")
    free = ctx.free_bytes(ctx.home)
    if free < 70 * 1024**3:
        warn(f"only {free / 1024**3:.0f} G free — a full build needs ~60 G")
    ctx.visp("build", "all", "--config", ctx.args.webclient_config)
    ctx.mark("build")


def _dump_old(ctx: Ctx) -> tuple[Path, Path, dict]:
    """Dump the old Mongo application databases and Matomo. Returns (mongo archive, matomo dump, counts)."""
    password = ctx.old_env()["MONGO_ROOT_PASSWORD"]
    stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    if not DRY_RUN:
        ctx.dumps.mkdir(parents=True, exist_ok=True)
        ctx.dumps.chmod(0o700)
    ctx.compose("up", "-d", "mongo", "matomo-db")
    ctx.wait_for("old mongo", ctx.compose_cmd("exec", "-T", "mongo", "mongosh", "--quiet", "--eval", "1"))

    counts = ctx.mongo_counts("old", password)
    dbs = app_databases(list(counts)) or ["<application databases>"]
    for dbname in dbs:
        colls = counts.get(dbname, {})
        say(f"  {dbname}: " + ", ".join(f"{c}={n}" for c, n in sorted(colls.items())))

    name = f"{MONGO_DUMP_PREFIX}_{stamp}"
    ctx.compose(
        "exec", "-T", "mongo", "sh", "-c", "umask 077 && cat > /tmp/.visp-migrate.yaml",
        input_text=mongo_config_yaml(password),
    )  # fmt: skip
    try:
        # Application databases only: the new instance keeps its own admin database
        # and root credentials, so nothing from the old .env outlives this dump.
        for dbname in dbs:
            ctx.compose(
                "exec", "-T", "mongo", "mongodump", "--quiet", "--config=/tmp/.visp-migrate.yaml",
                "--username=root", "--authenticationDatabase=admin", f"--db={dbname}", f"--out=/tmp/{name}",
            )  # fmt: skip
    finally:
        ctx.compose("exec", "-T", "mongo", "rm", "-f", "/tmp/.visp-migrate.yaml", check=False)
    archive = ctx.dumps / f"{name}.tar.gz"
    ctx.compose("exec", "-T", "mongo", "tar", "-czf", f"/tmp/{name}.tar.gz", "-C", "/tmp", name)
    ctx.compose("cp", f"mongo:/tmp/{name}.tar.gz", str(archive))
    ctx.compose("exec", "-T", "mongo", "rm", "-rf", f"/tmp/{name}", f"/tmp/{name}.tar.gz", check=False)
    if not DRY_RUN:
        _, listing = ctx.probe(["tar", "-tzf", str(archive)])
        missing = [d for d in dbs if f"{name}/{d}/" not in listing]
        if missing:
            raise MigrationError(f"the Mongo dump lacks {', '.join(missing)}")
        ok(f"Mongo dump {archive} ({archive.stat().st_size / 1024**2:.1f} M)")

    ctx.wait_for(
        "old matomo-db",
        ctx.compose_cmd(
            "exec", "-T", "matomo-db", "sh", "-c", 'MYSQL_PWD="$MYSQL_ROOT_PASSWORD" exec mysqladmin -uroot ping'
        ),
    )
    matomo = ctx.dumps / f"matomo_{stamp}.sql"
    ctx.compose(
        "exec", "-T", "matomo-db", "sh", "-c",
        'MYSQL_PWD="$MYSQL_ROOT_PASSWORD" exec mysqldump -uroot --single-transaction --routines --triggers '
        '"$MYSQL_DATABASE"',
        stdout_path=matomo,
    )  # fmt: skip
    if not DRY_RUN:
        if "Dump completed" not in matomo.read_bytes()[-300:].decode(errors="replace"):
            raise MigrationError(f"the Matomo dump {matomo} looks truncated")
        ok(f"Matomo dump {matomo} ({matomo.stat().st_size / 1024**2:.1f} M)")
    ctx.chown_user(ctx.dumps)
    return archive, matomo, counts


def _restore_and_migrate(ctx: Ctx, archive: Path, matomo: Path, old_counts: dict, sysadmins: list[str]) -> None:
    new_secrets = ctx.new_secrets()
    ctx.visp("stop", "all", check=False)

    ctx.visp("start", "mongo")
    ctx.wait_for("new mongo", ["podman", "exec", "mongo", "mongosh", "--quiet", "--eval", "1"], as_user=True)
    # --drop: every run starts over from the dump, which makes this re-runnable.
    ctx.visp("restore", str(archive), "--force", "--drop")
    problems = compare_counts(old_counts, ctx.mongo_counts("new", new_secrets.get("MONGO_ROOT_PASSWORD", "")))
    if problems:
        raise MigrationError("restored document counts differ:\n    " + "\n    ".join(problems))
    ok("restored document counts match the old databases")

    ctx.run(["python3", "scripts/migrate-permissions.py"], as_user=True, cwd=ctx.new)
    ctx.run(["python3", "scripts/migrate-permissions.py", "--apply"], as_user=True, cwd=ctx.new)
    ctx.run(["python3", "scripts/migrate-meta-json.py", "--apply"], as_user=True, cwd=ctx.new)
    for username in sysadmins:
        ctx.visp("users", "set-system-role", username, "sys_admin")
    if not sysadmins:
        warn("no --sysadmin given: nobody can create projects or open /admin")

    # Matomo: keep the old config (its salt signs tokens), but point it at the new
    # database with the new credentials.
    config = ctx.new_path("mounts/matomo/config/config.ini.php")
    if config.is_file() or DRY_RUN:
        if not DRY_RUN:
            patched = patch_ini_section(
                config.read_text(),
                "[database]",
                {
                    "host": "matomo-db",
                    "dbname": NEW_MATOMO_DB,
                    "username": new_secrets["MATOMO_DB_USER"],
                    "password": new_secrets["MATOMO_DB_PASSWORD"],
                },
            )
            with open(config, "w") as f:  # in place: keeps owner and mode
                f.write(patched)
        ok("Matomo config points at the new database")
    else:
        warn("no Matomo config.ini.php: Matomo will start its setup wizard")
    # Matomo writes its config as www-data (uid 33 inside the container).
    ctx.run(["podman", "unshare", "chown", "-R", "33:33", "mounts/matomo/config"], as_user=True, cwd=ctx.new)

    ctx.visp("start", "matomo-db")
    root_sh = 'MYSQL_PWD="$MYSQL_ROOT_PASSWORD" exec'
    ctx.wait_for(
        "new matomo-db",
        ["podman", "exec", "matomo-db", "sh", "-c", f"{root_sh} mariadb-admin -uroot ping"],
        as_user=True,
    )
    reset = f"DROP DATABASE IF EXISTS {NEW_MATOMO_DB}; CREATE DATABASE {NEW_MATOMO_DB};"
    ctx.run(["podman", "exec", "matomo-db", "sh", "-c", f'{root_sh} mariadb -uroot -e "{reset}"'], as_user=True)
    ctx.run(
        ["podman", "exec", "-i", "matomo-db", "sh", "-c", f"{root_sh} mariadb -uroot {NEW_MATOMO_DB}"],
        as_user=True,
        stdin_path=matomo,
    )
    query = f"SELECT COUNT(*) FROM information_schema.tables WHERE table_schema='{NEW_MATOMO_DB}'"
    rc, tables = ctx.probe(
        ["podman", "exec", "matomo-db", "sh", "-c", f'{root_sh} mariadb -uroot -N -e "{query}"'], as_user=True
    )
    if not DRY_RUN and (rc != 0 or not tables.isdigit() or int(tables) == 0):
        raise MigrationError("the Matomo import produced no tables")
    ok(f"Matomo database imported ({tables or '?'} tables)")
    ctx.visp("stop", "all", check=False)


def phase_databases(ctx: Ctx) -> None:
    heading("Databases: dump the old ones, restore and migrate them into the new instance")
    ctx.require("build", "sync-files")
    if ctx.done("cutover") and not ctx.args.force:
        raise MigrationError("the cutover is done — this would replace live data with the old databases")
    sysadmins = ctx.args.sysadmin or ctx.state().get("sysadmins", [])
    archive, matomo, counts = _dump_old(ctx)
    _restore_and_migrate(ctx, archive, matomo, counts, sysadmins)
    ctx.mark("databases", sysadmins=sysadmins, mongo_archive=str(archive), matomo_dump=str(matomo))


def _start(ctx: Ctx) -> None:
    ctx.visp("start", "all")
    domain = ctx.new_env().get("BASE_DOMAIN", "")
    ctx.wait_for(
        f"Apache on :{NEW_HTTP_PORT}",
        ["curl", "-s", "-o", "/dev/null", "-f", "-H", f"Host: {domain}", f"http://127.0.0.1:{NEW_HTTP_PORT}/"],
    )
    ctx.wait_for("matomo", ["podman", "exec", "matomo", "test", "-f", "/var/www/html/console"], as_user=True)
    ctx.run(
        ["podman", "exec", "-u", "www-data", "matomo", "php", "/var/www/html/console", "core:update", "--yes"],
        as_user=True,
        check=False,
    )
    ctx.visp("status", check=False)


def phase_start(ctx: Ctx) -> None:
    heading("Start the new instance")
    ctx.require("databases", "install", "build")
    _start(ctx)
    ctx.mark("start")


def phase_verify(ctx: Ctx) -> None:
    heading("Verify the new instance")
    ctx.require("start")
    domain = ctx.new_env().get("BASE_DOMAIN", "")
    failures: list[str] = []

    def check(cond: bool, good: str, bad: str) -> None:
        if DRY_RUN:
            say(f"  (would check: {good.split(':')[0]})")
        elif cond:
            ok(good)
        else:
            LOG.write(f"  ✗ {bad}", RED)
            failures.append(bad)

    ctx.visp("status", check=False)
    ctx.visp("users", "list", check=False)

    running = set(ctx.new_running())
    missing = CORE_CONTAINERS - running
    check(not missing, "core containers are running", f"not running: {', '.join(sorted(missing))}")
    check("whisperx" in running, "WhisperVault is running", "WhisperVault is not running ('./visp.py debug whisperx')")

    rc, _ = ctx.probe(
        ["podman", "exec", "-u", "_shibd", "apache", "test", "-r", "/etc/certs/sp-cert/key.pem"], as_user=True
    )
    check(rc == 0, "shibd can read the SP key", "shibd cannot read certs/sp-cert/key.pem: logins will fail")

    for label, host, path in (
        ("main site", domain, "/"),
        ("Shibboleth handler", domain, "/Shibboleth.sso/Metadata"),
        ("Matomo", f"matomo.{domain}", "/"),
    ):
        _, code = ctx.probe(
            ["curl", "-s", "-o", "/dev/null", "-w", "%{http_code}", "-H", f"Host: {host}",
             f"http://127.0.0.1:{NEW_HTTP_PORT}{path}"]
        )  # fmt: skip
        check(code[:1] in ("2", "3"), f"{label}: HTTP {code}", f"{label}: HTTP {code or 'no answer'}")

    port = 443 if ctx.done("cutover") else (8443 if ctx.done("preview") else None)
    if port:
        _, code = ctx.probe(
            ["curl", "-sk", "-o", "/dev/null", "-w", "%{http_code}", "--resolve", f"{domain}:{port}:127.0.0.1",
             f"https://{domain}:{port}/"]
        )  # fmt: skip
        check(code[:1] in ("2", "3"), f"TLS proxy :{port}: HTTP {code}", f"TLS proxy :{port}: HTTP {code or '—'}")

    say("\n  By hand: log in, open a project, a session and ARTIC, record a take, run a short transcription.")
    if failures:
        raise MigrationError(f"{len(failures)} check(s) failed")
    ctx.mark("verify")


def _write_proxy(ctx: Ctx, text: str) -> None:
    """Replace the proxy config in place, validate and reload; put the previous one back on failure."""
    conf = Path(ctx.args.proxy_conf)
    backup = conf.with_name(conf.name + ".pre-podman")
    if not conf.is_file():
        raise MigrationError(f"{conf} not found (--proxy-conf)")
    if not backup.exists():
        ctx.run(["cp", "-p", str(conf), str(backup)])
        say(f"  original config kept as {backup}")
    if DRY_RUN:
        say("  new config:\n" + "\n".join("    | " + line for line in text.splitlines()))
        return
    previous = conf.read_text()
    # In place, not by rename: the file is bind-mounted into the proxy container,
    # which would otherwise keep reading the old inode.
    with open(conf, "w") as f:
        f.write(text)
    container = ctx.args.proxy_container
    if ctx.run(["docker", "exec", container, "nginx", "-t"], check=False).returncode != 0:
        with open(conf, "w") as f:
            f.write(previous)
        raise MigrationError("nginx rejected the new config; the previous one is back in place")
    ctx.run(["docker", "exec", container, "nginx", "-s", "reload"])
    ok(f"proxy reloaded with the new {conf.name}")


def phase_preview(ctx: Ctx) -> None:
    heading(f"Preview: {PREVIEW_LISTEN} -> new instance (production untouched)")
    ctx.require("start")
    if ctx.done("cutover"):
        raise MigrationError("the cutover is done — nothing to preview")
    conf = Path(ctx.args.proxy_conf)
    if not conf.is_file():
        raise MigrationError(f"{conf} not found (--proxy-conf)")
    _write_proxy(ctx, add_preview_block(conf.read_text(), ctx.args.proxy_cert, ctx.args.proxy_key, NEW_HTTP_PORT))
    d = ctx.new_env().get("BASE_DOMAIN", "<domain>")
    say("\n  To reach it from your own machine only:", BOLD)
    say("    sudo ssh -L 443:127.0.0.1:8443 <you>@<this host>")
    say(f"    hosts file: 127.0.0.1 {d} app.{d} artic.{d} recorder.{d} matomo.{d} {ctx.args.tratt_subdomain}.{d}")
    say("  Expect a certificate warning (self-signed). Logins go through SWAMID as usual.")
    say("  Anything changed in the preview is discarded: the cutover copies everything again.")
    ctx.mark("preview")


def phase_cutover(ctx: Ctx) -> None:
    heading("Cutover")
    ctx.require("start", "verify")
    old = ctx.need_old()
    domain = ctx.new_env().get("BASE_DOMAIN", "")
    writers = [s for s in ctx.old_services() if s not in ("mongo", "matomo-db")]
    _, sessions = ctx.probe(["docker", "ps", "--filter", "name=session", "--format", "{{.Names}}"])
    say("  This will:")
    say(f"   1. stop the old writers: {' '.join(writers)}")
    if sessions:
        say(f"      ({len(sessions.split())} old session container(s) are running; unsaved work in them is lost)")
    say("   2. stop the new instance, re-sync the files, re-dump and re-migrate the databases")
    say("   3. stop the old stack completely ('docker compose down')")
    say(f"   4. start the new instance and point {ctx.args.proxy_conf} at it")
    say("  Users are offline from step 1 until step 4 is done.")
    ctx.confirm("  Proceed with the cutover?", domain)

    try:
        ctx.compose("stop", *writers)
        _sync_files(ctx)
        ctx.mark("sync-files")
        sysadmins = ctx.args.sysadmin or ctx.state().get("sysadmins", [])
        archive, matomo, counts = _dump_old(ctx)
        _restore_and_migrate(ctx, archive, matomo, counts, sysadmins)
        ctx.mark("databases", sysadmins=sysadmins, mongo_archive=str(archive), matomo_dump=str(matomo))
        ctx.compose("down")
        _start(ctx)
    except MigrationError:
        say("\n  The proxy still points at the old install, whose data is untouched.", YELLOW)
        say(f"  Bring it back with: migrate-from-compose.py rollback --old-dir {old}", YELLOW)
        raise
    _write_proxy(ctx, render_proxy_conf(ctx.args.proxy_cert, ctx.args.proxy_key, NEW_HTTP_PORT))
    ctx.mark("cutover")
    ctx.invalidate("verify", "preview")
    phase_verify(ctx)
    say(f"\n  The new instance is live. The old tree {old} is untouched: keep it until you are sure.", GREEN)


def phase_rollback(ctx: Ctx) -> None:
    heading("Rollback to the old install")
    old = ctx.need_old()
    conf = Path(ctx.args.proxy_conf)
    backup = conf.with_name(conf.name + ".pre-podman")
    say("  This will:")
    say(f"   1. restore {conf} from {backup.name}" if backup.exists() else "   1. (the proxy config was never changed)")
    say(f"   2. stop the new instance (as {ctx.user})")
    say(f"   3. start the old stack in {old}")
    if ctx.done("cutover"):
        warn("changes users made on the new instance since the cutover stay in the new instance only")
    ctx.confirm("  Roll back?", "rollback")

    if backup.exists():
        _write_proxy(ctx, backup.read_text())
    if _user_exists(ctx.user) and (ctx.new / "visp.py").exists():
        ctx.visp("stop", "all", check=False)
    ctx.compose("up", "-d")
    ctx.invalidate("preview", "cutover", "verify")
    ok("the old install is serving again")


def phase_status(ctx: Ctx) -> None:
    heading("Status")
    st = ctx.state()
    for phase in PHASES:
        if phase in ("rollback", "status"):
            continue
        when = st.get("done", {}).get(phase)
        say(f"  {'✓' if when else '·'} {phase:<11} {when or ''}", GREEN if when else "")
    for key in ("mongo_archive", "matomo_dump", "sysadmins"):
        if key in st:
            say(f"  {key}: {st[key]}")
    say(f"  settings: {st.get('settings', {})}")
    say(f"  logs: {LOG_DIR}")


PHASES = {
    "preflight": phase_preflight,
    "host": phase_host,
    "checkout": phase_checkout,
    "config": phase_config,
    "sync-files": phase_sync_files,
    "install": phase_install,
    "build": phase_build,
    "databases": phase_databases,
    "start": phase_start,
    "verify": phase_verify,
    "preview": phase_preview,
    "cutover": phase_cutover,
    "rollback": phase_rollback,
    "status": phase_status,
}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=__doc__.split("\n\n")[0],
        epilog=__doc__.split("\n\n", 1)[1],
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("phase", choices=list(PHASES))
    parser.add_argument("--old-dir", help="the old Docker Compose install (never modified)")
    parser.add_argument("--user", default="visp", help="service user (default: visp)")
    parser.add_argument("--home", default="/data/visp", help="service user's home, on the data volume")
    parser.add_argument("--branch", default="master", help="branch of this repository to deploy")
    parser.add_argument("--webclient-config", default="visp", help="webclient build config (default: visp)")
    parser.add_argument("--tratt-subdomain", default="octra", help="TRATT subdomain; 'octra' keeps the old DNS name")
    parser.add_argument("--proxy-blocked-cidrs", help="PROXY_BLOCKED_CIDRS for the new .env (internal networks)")
    parser.add_argument("--sysadmin", action="append", default=[], help="grant sys_admin to this user (repeatable)")
    parser.add_argument("--whisper-models-from", help="local directory with a complete WhisperVault models set")
    parser.add_argument("--proxy-conf", default="/opt/tls-proxy/proxy.conf", help="host TLS proxy config file")
    parser.add_argument("--proxy-container", default="tls-proxy-tls-proxy-1", help="host TLS proxy container")
    parser.add_argument("--proxy-cert", default="/etc/tls/cert.pem", help="cert path inside the proxy container")
    parser.add_argument("--proxy-key", default="/etc/tls/key.pem", help="key path inside the proxy container")
    parser.add_argument("--dry-run", action="store_true", help="show what would be done; change nothing")
    parser.add_argument("--yes", action="store_true", help="skip confirmation prompts")
    parser.add_argument("--force", action="store_true", help="override safety checks (phase order, existing files)")
    return parser


def main() -> int:
    global DRY_RUN
    args = build_parser().parse_args()
    DRY_RUN = args.dry_run

    if os.geteuid() != 0:
        print(f"{RED}Run as root (sudo).{NC}")
        return 1
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    STATE_DIR.chmod(0o700)
    lock = open(STATE_DIR / "lock", "w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        print(f"{RED}Another migrate-from-compose.py is running.{NC}")
        return 1

    LOG.open(args.phase)
    LOG.write(f"migrate-from-compose.py {' '.join(sys.argv[1:])}", echo=False)
    say(f"Log: {LOG.path}")
    ctx = Ctx(args)
    try:
        ctx.check_paths()
        PHASES[args.phase](ctx)
    except MigrationError as e:
        say(f"\n✗ {args.phase} failed: {e}\n  Full log: {LOG.path}", RED)
        return 1
    except KeyboardInterrupt:
        say(f"\n✗ {args.phase} interrupted. Full log: {LOG.path}", RED)
        return 130
    except Exception as e:  # unexpected: keep the traceback in the log
        LOG.write(traceback.format_exc(), echo=False)
        say(f"\n✗ {args.phase}: unexpected {type(e).__name__}: {e}\n  Full log: {LOG.path}", RED)
        return 1
    if DRY_RUN:
        say("\n(dry run — nothing was changed)", YELLOW)
    else:
        say(f"\n✓ {args.phase} done", GREEN)
    return 0


if __name__ == "__main__":
    sys.exit(main())
