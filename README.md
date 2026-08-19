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
# 1. Clone
git clone https://github.com/humlab-speech/visible-speech-deployment.git
cd visible-speech-deployment

# 2. Generate local config, secrets, certificates, and dev quadlets
./visp.py install --mode dev
nano .env  # Optional: adjust BASE_DOMAIN, ADMIN_EMAIL, optional services, etc.

# 3. Fetch external application repositories
./visp.py deploy update

# 4. Re-render quadlets after any .env edits and after external repos exist
./visp.py install --mode dev --force

# 5. Build images
./visp.py build                  # Build all images (or selectively, see below)

# 6. Start
./visp.py reload                 # Reload systemd daemon
./visp.py start all

# 7. Verify
./visp.py status
```

`.env` is copied from `.env-example` automatically during first install. `.env.secrets` is also created automatically with generated passwords and tokens.

## Deployment Modes

| Feature | Development | Production |
|---------|-------------|------------|
| `DEVELOPMENT_MODE` | `true` | `false` |
| Source code | Mounted for hot-reload | Baked into images |
| session-manager | nodemon restarts on `src/` edits | `node src/index.js`, rebuild to change |
| `LOG_LEVEL` | `debug` | `info` |

Switch modes with `./visp.py install --mode <dev|prod> --force && ./visp.py reload`.

In dev mode, editing `external/session-manager/src/` restarts the service in ~2s with no
rebuild. Add dependencies with `./visp.py npm session-manager -- install <pkg>` (which runs
npm inside the service image, not on the host) followed by
`./visp.py restart session-manager`. Note that a reload drops in-memory session state, so
running Jupyter sessions must be restarted — see AGENTS.md for the full caveats.

## Common `visp.py` Commands

```bash
# Lifecycle
./visp.py install [--mode dev|prod]  # Install quadlet units + create secrets
./visp.py uninstall                  # Remove quadlet units + secrets
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

# Dependencies for dev source-mounted services (runs npm inside the service image)
./visp.py npm session-manager -- install <pkg>
./visp.py npm session-manager -- ci  # Restore node_modules from the lockfile

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
   127.0.0.1 visp.local app.visp.local artic.visp.local octra.visp.local recorder.visp.local matomo.visp.local mongo.visp.local idp.visp.local
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

## Local Dev Host Nginx

In dev mode, the Apache container publishes HTTP on `8081` and HTTPS on `8443`. A host-OS nginx can listen on standard ports `80`/`443` for `visp.local` and proxy to Apache on `8443`.

1. Add local domains to `/etc/hosts`:
   ```text
   127.0.0.1 visp.local app.visp.local artic.visp.local octra.visp.local recorder.visp.local matomo.visp.local mongo.visp.local idp.visp.local
   ```

2. Create `/etc/nginx/sites-available/visp.local`:
   ```nginx
   server {
       listen 80;
       listen [::]:80;
       server_name visp.local *.visp.local;

       return 301 https://$host$request_uri;
   }

   server {
       listen 443 ssl;
       listen [::]:443 ssl;
       server_name visp.local *.visp.local;

       include /etc/nginx/snippets/snakeoil.conf;

       client_max_body_size 10G;

       location / {
           proxy_pass https://127.0.0.1:8443;

           proxy_ssl_server_name on;
           proxy_ssl_name $host;

           proxy_http_version 1.1;
           proxy_set_header Host $host;
           proxy_set_header X-Real-IP $remote_addr;
           proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
           proxy_set_header X-Forwarded-Proto $scheme;
           proxy_set_header Upgrade $http_upgrade;
           proxy_set_header Connection "upgrade";

           proxy_connect_timeout 60s;
           proxy_send_timeout 86400s;
           proxy_read_timeout 86400s;
       }
   }
   ```

3. Enable and reload nginx:
   ```bash
   sudo ln -s /etc/nginx/sites-available/visp.local /etc/nginx/sites-enabled/visp.local
   sudo nginx -t
   sudo systemctl reload nginx
   ```

## Reverse Proxy (Production)

In production, a host nginx forwards to Apache, usually on port `8081`. Apache handles all subdomains internally via VirtualHost.

**Required subdomains** (replace `yourdomain.com`):
- `yourdomain.com` — main app (**WebSocket required**)
- `artic.yourdomain.com` — EMU annotation
- `octra.yourdomain.com` — OCTRA transcription
- `recorder.yourdomain.com` — audio recorder
- `matomo.yourdomain.com` — analytics (optional)

**⚠️ WebSocket proxying is required** on the main domain — without it users cannot log in. Proxy headers (`Host`, `X-Forwarded-For`, `X-Forwarded-Proto`, `Upgrade`, `Connection`) and long timeouts (~24 h) are needed.

Set `client_max_body_size 10G;` (or higher) in the nginx `server` block so audio uploads are not rejected by nginx before reaching Apache/PHP.

## Development

Pre-commit hooks run on every commit. Run manually with:

```bash
pre-commit run --all-files
```

Hooks: `ruff-format`, `ruff` (linting, line length 120), `pytest`, plus standard file hygiene checks.

## WSL2 Note

When running on WSL2, port-forward from Windows to WSL using the WSL IP (from `hostname -I`), **not** `127.0.0.1`. See [AGENTS.md](AGENTS.md) → *WSL Deployment Notes* for the full setup.
