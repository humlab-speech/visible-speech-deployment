# Visible Speech

A collection of containerised services forming the **Visible Speech (VISP)** academic speech-annotation and transcription platform. Managed via **rootless Podman** with **systemd Quadlets**.

## 📚 Documentation

- **[AGENTS.md](AGENTS.md)** — Full architecture reference: service topology, build system, debugging, conventions
- **[Version Management](docs/VERSION_MANAGEMENT.md)** — Locking and managing external component versions
- **[Backup & Restore](docs/BACKUP_RESTORE.md)** — MongoDB backup and restore procedures
- **[Matomo Setup](docs/MATOMO_SETUP.md)** — Optional analytics setup
- **[Version Checking](docs/VERSION_CHECKING.md)** — Image vs repo version comparison

## Prerequisites

```bash
# Podman 4.6+ with netavark network backend (required for DNS)
sudo apt install -y podman podman-netavark aardvark-dns

# Enable user lingering (services survive without login)
sudo loginctl enable-linger $USER

# Verify
podman --version                     # 4.6+
podman info | grep networkBackend    # netavark
```

## Quick Start

```bash
# 1. Clone and configure
git clone https://github.com/humlab-speech/visible-speech-deployment.git
cd visible-speech-deployment
cp .env-example .env
nano .env  # Set BASE_DOMAIN, ADMIN_EMAIL, etc.
# .env.secrets is auto-generated with random passwords on first install

# 2. Build images
./visp.py build                  # Build all images (or selectively, see below)

# 3. Install and start
./visp.py install --mode dev     # Link quadlets to systemd
./visp.py reload                 # Reload systemd daemon
./visp.py start all

# 4. Verify
./visp.py status
```

## Deployment Modes

| Feature | Development | Production |
|---------|-------------|------------|
| `DEVELOPMENT_MODE` | `true` | `false` |
| Source code | Mounted for hot-reload | Baked into images |
| `LOG_LEVEL` | `debug` | `info` |

Switch modes with `./visp.py install --mode <dev|prod> --force && ./visp.py reload`.

## Common `visp.py` Commands

```bash
# Lifecycle
./visp.py install [--mode dev|prod]  # Link quadlets + create secrets
./visp.py uninstall                  # Remove quadlet links + secrets
./visp.py reload                     # systemctl --user daemon-reload
./visp.py start all / stop all / restart all
./visp.py restart <service>

# Status and debugging
./visp.py status                     # Services, images, containers
./visp.py logs <service> [-f]        # Follow service logs
./visp.py debug <service>            # Journal + start errors
./visp.py shell <service>            # bash inside container

# Building (no host npm/node required — all builds run in containers)
./visp.py build --list               # Show all buildable targets
./visp.py build                      # Build everything
./visp.py build <target>             # e.g. apache, session-manager, webclient
./visp.py build <target> --no-cache  # Clean rebuild

# Database
./visp.py backup                     # Dump MongoDB → timestamped .tar.gz
./visp.py restore <file>             # Restore (prompts for confirmation)

# External repos
./visp.py deploy update              # Pull latest external repos
./visp.py deploy status              # Check repo/image/version alignment
```

See `./visp.py --help` for the full command reference.

## Included Services

| Service | Description |
|---------|-------------|
| **Apache** | Web server + Shibboleth auth; hosts PHP API and Angular webclient |
| **Local IdP** | SimpleSAMLphp test Identity Provider at `idp.BASE_DOMAIN` (dev mode only) |
| **Session Manager** | Spawns and manages user session containers via WebSocket |
| **MongoDB** | Database |
| **artic** | Web-based speech annotation tool |
| **emu-webapp-server** | artic backend (Node.js) |
| **OCTRA** | Transcription annotation tool |
| **wsrng-server** | Web Speech Recorder server |
| **WhisperX** | Speech-to-text transcription via Unix Domain Socket (optional) |
| **Matomo** | Usage analytics (optional) |

## Post-Installation Steps

1. Add to `/etc/hosts` (local dev only):
   ```
   127.0.0.1 visp.local
   127.0.0.1 artic.visp.local
   127.0.0.1 idp.visp.local
   ```

2. Sign in through the dev IdP (dev mode):
   ```
   https://visp.local
   ```
   The app redirects to `/DS/Login` and then to `https://idp.BASE_DOMAIN/simplesaml/`.
   Default test users:
   ```
   test1 / test1pass
   test2 / test2pass
   test3 / test3pass
   ```

3. Grant user privileges:
   ```bash
   python3 visp-users.py list
   python3 visp-users.py grant <username> createProjects
   ```
   See [AGENTS.md](AGENTS.md) → *User management* for details.

## Reverse Proxy (Production)

In production, a host nginx forwards to Apache (port 8081). Apache handles all subdomains internally via VirtualHost.

**Required subdomains** (replace `yourdomain.com`):
- `yourdomain.com` — main app (**WebSocket required**)
- `artic.yourdomain.com` — EMU annotation
- `octra.yourdomain.com` — OCTRA transcription
- `recorder.yourdomain.com` — audio recorder
- `matomo.yourdomain.com` — analytics (optional)

**⚠️ WebSocket proxying is required** on the main domain — without it users cannot log in. Proxy headers (`Host`, `X-Forwarded-For`, `X-Forwarded-Proto`, `Upgrade`, `Connection`) and long timeouts (~24 h) are needed.

## Development

Pre-commit hooks run on every commit. Run manually with:

```bash
pre-commit run --all-files
```

Hooks: `ruff-format`, `ruff` (linting, line length 120), `pytest`, plus standard file hygiene checks.

## WSL2 Note

When running on WSL2, port-forward from Windows to WSL using the WSL IP (from `hostname -I`), **not** `127.0.0.1`. See [AGENTS.md](AGENTS.md) → *WSL Deployment Notes* for the full setup.
