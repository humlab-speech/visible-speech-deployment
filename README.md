# Visible Speech

A collection of containerised services that together form the **Visible Speech (VISP)** academic speech-annotation and transcription platform. Managed via **rootless Podman** with **systemd Quadlets**.

## 📚 Documentation

- **[Version Management](docs/VERSION_MANAGEMENT.md)** - Managing component versions and locking
- **[Backup & Restore](docs/BACKUP_RESTORE.md)** - MongoDB backup and restore procedures
- **[Matomo Integration](docs/MATOMO_INTEGRATION.md)** - Optional analytics setup
- **[Version Checking](docs/VERSION_CHECKING.md)** - Image vs repo version comparison
- **[AGENTS.md](AGENTS.md)** - Comprehensive project architecture reference for AI agents and developers

## Quick Start

## 🦭 Podman Deployment (Recommended)

The `visp.py` script provides unified management for Podman deployments using systemd Quadlets.

### Prerequisites for Podman

```bash
# Install Podman (4.6+ required for Quadlets)
sudo apt install -y podman podman-docker

# Install netavark for proper DNS support (REQUIRED)
sudo apt install -y podman-netavark aardvark-dns

# Enable user lingering (allows services to run without login)
sudo loginctl enable-linger $USER

# Verify Podman version
podman --version  # Should be 4.6+
podman info | grep networkBackend  # Should show "netavark"
```

**⚠️ IMPORTANT: Netavark Network Backend Required**

VISP requires the **netavark** network backend for proper DNS resolution. The older CNI backend has critical DNS issues causing 20+ second timeouts.

**First-time Setup**: visp.py will automatically configure netavark if not detected.

**Migrating from CNI**: If you have existing containers:
1. **Backup your database first**: `./visp.py backup`
2. Run `./visp.py install` - it will detect CNI and offer to migrate
3. **All containers will be removed** (images preserved) during migration
4. Networks will be recreated automatically

**Why netavark?**
- ✅ Fast, reliable DNS (0.02s vs 20s+ with CNI)
- ✅ Works with Internal=true networks (proper isolation)
- ✅ Modern, officially recommended by Podman team
- ✅ Better performance and stability

### Quick Start with Podman

```bash
# 1. Clone the repository
git clone https://github.com/humlab-speech/visible-speech-deployment.git
cd visible-speech-deployment

# 2. Copy and configure environment
cp .env-example .env
nano .env  # Set BASE_DOMAIN, ADMIN_EMAIL, etc.
# .env.secrets is created automatically with generated passwords

# 3. Build required images
./visp.py build session-manager
./visp.py build container-agent  # For dev mode

# 4. Install quadlets (dev mode by default)
./visp.py install --mode dev

# 5. Reload systemd and start services
./visp.py reload
./visp.py start all

# 6. Check status
./visp.py status
```

### Deployment Modes

VISP supports two deployment modes with different configurations:

| Feature | Development | Production |
|---------|-------------|------------|
| Traefik reverse proxy | ✅ Included | ❌ Not included (use host nginx) |
| DEVELOPMENT_MODE | true | false |
| container-agent | Mounted from local build | Baked into session images |
| Source code | Can be mounted for hot-reload | Baked into images |
| LOG_LEVEL | debug | info |
| Typical use | Local development, testing | Server deployment |

**Switch modes:**
```bash
# View current mode
./visp.py mode

# Switch to production
./visp.py install --mode prod --force
./visp.py reload
./visp.py restart all

# Switch to development
./visp.py install --mode dev --force
./visp.py reload
./visp.py restart all
```

### visp.py Commands

```bash
# Status and monitoring
./visp.py status              # Show all services, quadlet links, containers
./visp.py logs                # View all logs
./visp.py logs session-manager -f  # Follow specific service logs

# Service control
./visp.py start all           # Start all services
./visp.py stop all            # Stop all services
./visp.py restart all         # Restart all services
./visp.py restart session-manager  # Restart specific service

# Quadlet management
./visp.py install             # Link quadlets to systemd
./visp.py install --mode prod --force  # Install prod quadlets
./visp.py uninstall           # Remove quadlet links
./visp.py reload              # Reload systemd daemon

# Building
./visp.py build --list        # List all buildable targets
./visp.py build               # Build all container images

# Build service containers
./visp.py build session-manager  # Session manager service
./visp.py build apache        # Apache web server
./visp.py build whisper       # Whisper transcription
./visp.py build wsrng-server  # Random number generator

# Build session images (for RStudio/Jupyter containers)
./visp.py build operations-session  # Base session (required first)
./visp.py build rstudio-session     # RStudio (depends on operations)
./visp.py build jupyter-session     # Jupyter (depends on operations)

# Build Node.js projects (containerized, no npm required on host)
./visp.py build container-agent  # Required for dev mode
./visp.py build webclient        # Default: visp config
./visp.py build webclient --config datalab  # Datalab config

# Build options
./visp.py build apache --no-cache  # Clean rebuild
./visp.py build --pull         # Pull latest base images

# Debugging
./visp.py debug session-manager  # Debug service startup issues
./visp.py shell apache        # Open shell in container
./visp.py exec apache ls /var/www/html  # Run command in container

# Network management
./visp.py network             # Show network and DNS info
./visp.py network ensure      # Create missing networks

# Image management
./visp.py images              # List VISP images, networks, and build status
./visp.py images base         # Audit base images from Dockerfiles (version pinning check)

# Permissions
./visp.py fix-permissions     # Fix mount path permissions using podman unshare

# Database management
./visp.py backup              # Backup MongoDB to current directory
./visp.py backup -o /backups/db.tar.gz  # Backup to specific path
./visp.py restore backup.tar.gz  # Restore from backup (with confirmation)
./visp.py restore backup.tar.gz --force  # Restore without confirmation
```

### Database Backup and Restore

VISP provides MongoDB backup/restore functionality with version tracking:

```bash
# Create a timestamped backup
./visp.py backup
# Output: visp_mongodb_6.0.14_20260128_091500.tar.gz

# Backup to specific directory
./visp.py backup -o /backups/production.tar.gz

# Restore database (prompts for confirmation)
./visp.py restore visp_mongodb_6.0.14_20260128_091500.tar.gz

# Force restore without confirmation
./visp.py restore backup.tar.gz --force

# Quick backup helper script
./backup-database.sh                 # Backs up to ./backups/
./backup-database.sh /path/to/dir    # Backs up to specific directory
```

**Backup Strategy:**
- **Database**: Use `visp.py backup` (small, frequent backups)
- **Audio Files**: Use rsync/filesystem backup for `mounts/repositories/` (large, infrequent)
- Backups include MongoDB version info in filename
- Uses mongodump/mongorestore for proper database consistency

### Environment and Secrets Management

VISP uses a split configuration approach for security:

**`.env`** - Non-sensitive configuration (tracked in git)
```bash
BASE_DOMAIN=visp.local
ADMIN_EMAIL=admin@example.com
WEBCLIENT_BUILD=visp-build
# etc.
```

**`.env.secrets`** - Passwords and tokens (gitignored)
```bash
MONGO_ROOT_PASSWORD=<auto-generated>
HS_API_ACCESS_TOKEN=<auto-generated>
TEST_USER_LOGIN_KEY=<auto-generated>
```

**Podman Secrets** - Injected at runtime
- Created automatically by `./visp.py install`
- Secrets are never stored in quadlet files or visible via `podman inspect`
- Each container only receives the secrets it needs
- Removed automatically by `./visp.py uninstall`

```bash
# List Podman secrets
podman secret ls

# Secrets are created from .env.secrets:
# - visp_mongo_root_password
# - visp_api_access_token
# - visp_test_user_login_key
# - visp_mongo_uri (derived)
# - visp_media_file_base_url (derived)
```

### Building Node.js Projects (No Host npm Required)

All Node.js builds run inside containers - no npm/Node.js installation needed on the host:

```bash
# Build container-agent (required for dev mode)
./visp.py build container-agent

# Build webclient with specific configuration
./visp.py build webclient                    # Default: visp config
./visp.py build webclient --config datalab   # Datalab config
./visp.py build webclient --config visp-pdf-server  # PDF server config

# Clean rebuild
./visp.py build container-agent --no-cache
```

### Building All Image Types

VISP uses three categories of container images:

**1. Service Containers** (core infrastructure):
```bash
./visp.py build apache           # Web server with Shibboleth
./visp.py build session-manager  # Session orchestrator
./visp.py build whisper          # Speech transcription
./visp.py build wsrng-server     # Random number generator
./visp.py build emu-webapp       # EMU annotation tool
./visp.py build emu-webapp-server
./visp.py build octra            # OCTRA transcription
```

**2. Session Images** (user environments - must build in order):
```bash
# Build base session first (contains R and common libraries)
./visp.py build operations-session

# Then build specialized sessions (depend on operations-session)
./visp.py build rstudio-session  # RStudio IDE
./visp.py build jupyter-session  # Jupyter Notebook
```

**3. Node.js Projects** (build artifacts for services):
```bash
./visp.py build container-agent  # Required for dev mode
./visp.py build webclient        # Angular web interface
```

**Build all at once:**
```bash
./visp.py build  # Builds all container images
```

### ⚠️ Important: Build Dependencies

**Apache container behavior:**
- **If `external/webclient/dist/` exists** → Uses it (fast) ✅
- **If dist/ missing** → Builds webclient inside container (5-10 min) ⏱️
- **Recommendation**: Always pre-build: `./visp.py build webclient`

**Session images behavior:**
- Always build container-agent from source (multi-stage build)
- No pre-built check (always fresh build ~30 sec)

**Development workflow:**
```bash
# Edit webclient code
./visp.py build webclient        # Rebuild dist/
systemctl --user restart apache         # Pick up new dist/
# Refresh browser

# Edit PHP code (api.php)
# Just refresh browser - mounted, auto-detected
```

**Production deployment:**
```bash
# Use version locking to ensure reproducible builds
./visp.py deploy status           # Check versions
./visp.py deploy lock webclient   # Lock to current tested version
git add versions.json && git commit -m "Lock webclient version"

# Build with locked versions
./visp.py build webclient
./visp.py build apache
```

See [Version Management](docs/VERSION_MANAGEMENT.md) for details on locking/unlocking versions.

### Inspecting Container Images

Monitor and audit container images:

```bash
# List VISP images with build status
./visp.py images
# Shows:
# - All expected VISP images (visp-apache, visp-session-manager, etc.)
# - Build status (✓ built / ✗ not built)
# - Image size and creation time
# - Network backend (netavark/CNI)
# - VISP networks status
# - Container network connections

# Audit base images from Dockerfiles
./visp.py images base
# Shows:
# - All base images used in Dockerfiles (debian, node, nginx, etc.)
# - Version pinning status (✓ pinned / ⚠️ unpinned)
# - Which Dockerfiles use each base image
# - Summary of unpinned images
# Useful for:
#   - Checking for outdated base images
#   - Ensuring reproducible builds
#   - Finding which files need updating
```

**Version Pinning Best Practices:**
- ✅ Pin all base images to specific versions (e.g., `debian:bookworm-20260202`)
- ⚠️ Avoid floating tags like `latest`, `stable`, `:20`, `:4`
- 📌 Use `images base` to audit current pinning status
- 🔄 Periodically check for newer versions and test updates

### Quadlet File Structure

```
quadlets/
├── dev/                           # Development mode quadlets
│   ├── apache.container
│   ├── emu-webapp.container
│   ├── emu-webapp-server.container
│   ├── matomo.container           # Optional analytics
│   ├── matomo-db.container
│   ├── mongo.container
│   ├── octra.container
│   ├── session-manager.container
│   ├── traefik.container          # Only in dev (TLS termination)
│   ├── whisperx.container         # Optional transcription
│   ├── wsrng-server.container
│   ├── visp-net.network
│   └── octra-net.network
├── prod/                          # Production mode (no Traefik)
│   └── (same services, DEVELOPMENT_MODE=false)
```

### Troubleshooting Podman

```bash
# Check service status
systemctl --user status session-manager.service

# View recent logs
journalctl --user -u session-manager.service --since "10 minutes ago"

# Check if quadlets are properly linked
ls -la ~/.config/containers/systemd/

# Verify networks exist
podman network ls

# Debug container issues
./visp.py debug session-manager

# Check container-agent build (dev mode)
ls -la container-agent/dist/  # Should contain main.js
```

---

## Version Management

Component versions can be **locked** (pinned to specific commits) or **unlocked** (tracking latest).
Managed via `./visp.py deploy`:

```bash
# Update all external repos to latest
./visp.py deploy update

# Check current versions
./visp.py deploy status

# Lock a component to current tested version
./visp.py deploy lock webclient

# Unlock to track latest again
./visp.py deploy unlock webclient

# Rollback to previously locked version
./visp.py deploy rollback webclient
```

See [Version Management](docs/VERSION_MANAGEMENT.md) for details.

## Included Services

| Service | Description |
|---------|-------------|
| **Traefik** | Edge router / TLS termination (dev mode only) |
| **Apache** | Web server + Shibboleth + PHP authentication; hosts the PHP API and webclient |
| **Session Manager** | Spawns and manages session containers (RStudio, Jupyter) via WebSocket |
| **MongoDB** | Database |
| **EMU-webApp** | Web-based speech annotation tool |
| **EMU-webApp Server** | EMU backend (Node.js, WebSocket + file API) |
| **OCTRA** | Transcription annotation tool |
| **wsrng-server** | Web Speech Recorder server |
| **WhisperX** | Speech-to-text transcription (optional) |
| **Matomo** | Usage analytics (optional) |

## Post-Installation Steps

1. Add to `/etc/hosts` (for local dev):
   ```
   127.0.0.1 visp.local
   127.0.0.1 emu-webapp.visp.local
   ```

2. Access test user (dev/demo):
   ```
   https://visp.local/?login=<TEST_USER_LOGIN_KEY from .env.secrets>
   ```

3. Grant user privileges (see `AGENTS.md` → User Management):
   ```bash
   python3 visp-users.py list
   python3 visp-users.py grant <username> createProjects
   ```

## Reverse Proxy Configuration (Production)

For production deployments behind a host nginx reverse proxy, configure nginx to handle all subdomains. Apache inside the Podman container serves multiple subdomains via VirtualHost configuration.

### Required Subdomains

- `yourdomain.com` (main VISP interface — **requires WebSocket support**)
- `app.yourdomain.com` (application portal — **requires WebSocket support**)
- `emu-webapp.yourdomain.com` (EMU annotation tool)
- `matomo.yourdomain.com` (analytics, optional)
- `octra.yourdomain.com` (OCTRA transcription)
- `recorder.yourdomain.com` (audio recorder)

**⚠️ CRITICAL:** WebSocket support is **required** for the main domain and app subdomain. Without it, users cannot log in.

### Key Configuration Points

- **WebSocket Support**: Main domain routes WebSocket to `session-manager:8020`
- **Proxy Headers**: Must include `Host`, `X-Forwarded-For`, `X-Forwarded-Proto`
- **Upgrade Headers**: Required for WebSocket (`Upgrade: websocket`, `Connection: upgrade`)
- **Timeouts**: Long timeouts needed for annotation sessions (24 hours recommended)

## Development

### Pre-commit Hooks

Pre-commit hooks run automatically on every commit:

- **ruff-format** — Python formatting
- **ruff** — Python linting (line length 120)
- **pytest** — Full test suite
- **trailing-whitespace**, **end-of-file-fixer**, **check-yaml**, **check-merge-conflict**, **debug-statements**

```bash
pre-commit run --all-files   # Run manually
```

### Node.js Builds

All Node.js components are built inside containers — no host npm/Node.js installation required:

```bash
./visp.py build webclient        # Angular web interface
./visp.py build container-agent  # Required for dev mode
```

## WSL-Specific: Port Forwarding

When running VISP on **WSL2 with rootless Podman**, forward ports from Windows to WSL's Traefik (8080/8443).

**⚠️ Important**: Use the **WSL IP address** (e.g., `172.29.x.x`), NOT `127.0.0.1`.

```powershell
# PowerShell as Administrator — get WSL IP from WSL terminal: hostname -I
$WSL_IP = "172.29.72.57"  # Replace with actual WSL IP

netsh interface portproxy add v4tov4 listenport=80 listenaddress=0.0.0.0 connectport=8080 connectaddress=$WSL_IP
netsh interface portproxy add v4tov4 listenport=443 listenaddress=0.0.0.0 connectport=8443 connectaddress=$WSL_IP
```

See `AGENTS.md` → WSL Deployment Notes for full details.
