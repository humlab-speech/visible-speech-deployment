# Visible Speech

This is a collection of dockerized services which as a whole makes out the Visible Speech (VISP) system.

## 📚 Documentation

- **[Complete Deployment Guide](docs/DEPLOYMENT_GUIDE.md)** - Comprehensive guide for dev and production deployments
- **[Quick Reference Card](docs/QUICK_REFERENCE.md)** - Cheat sheet for common tasks and troubleshooting
- **[Troubleshooting Decision Tree](docs/TROUBLESHOOTING.md)** - Step-by-step problem diagnosis and solutions
- **[Webclient Build Configuration](docs/WEBCLIENT_BUILD_CONFIG.md)** - Understanding Angular environment configurations
- **[Version Management](docs/VERSION_MANAGEMENT.md)** - Managing component versions
- **[Folder Structure](docs/FOLDER_STRUCTURE.md)** - Understanding the project layout
- **[Dev vs Prod](docs/DEV_VS_PROD.md)** - Differences between deployment modes
- **[Podman Networks](docs/PODMAN_NETWORKS.md)** - Network configuration for Podman deployments

## Quick Start

### ⚠️ Migration to Podman in Progress

**Current Status**: This project is being migrated from Docker to Podman with systemd Quadlets.

- ✅ Phase 1: Podman socket compatibility verified (node-docker-api works)
- ✅ Phase 2: systemd enabled in WSL
- ✅ Phase 3: Podman 4.6.2 installed with Quadlet support
- ✅ Phase 4: Core services converted to Quadlets
- ✅ Phase 5: Dev/Prod mode support added
- 🔄 Phase 6: Testing and documentation (IN PROGRESS)

---

## 🦭 Podman Deployment (Recommended)

The `visp-podman.py` script provides unified management for Podman deployments using systemd Quadlets.

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

**First-time Setup**: visp-podman.py will automatically configure netavark if not detected.

**Migrating from CNI**: If you have existing containers:
1. **Backup your database first**: `./visp-podman.py backup`
2. Run `./visp-podman.py install` - it will detect CNI and offer to migrate
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
./visp-podman.py build session-manager
./visp-podman.py build container-agent  # For dev mode

# 4. Install quadlets (dev mode by default)
./visp-podman.py install --mode dev

# 5. Reload systemd and start services
./visp-podman.py reload
./visp-podman.py start all

# 6. Check status
./visp-podman.py status
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
./visp-podman.py mode

# Switch to production
./visp-podman.py install --mode prod --force
./visp-podman.py reload
./visp-podman.py restart all

# Switch to development
./visp-podman.py install --mode dev --force
./visp-podman.py reload
./visp-podman.py restart all
```

### visp-podman.py Commands

```bash
# Status and monitoring
./visp-podman.py status              # Show all services, quadlet links, containers
./visp-podman.py logs                # View all logs
./visp-podman.py logs session-manager -f  # Follow specific service logs

# Service control
./visp-podman.py start all           # Start all services
./visp-podman.py stop all            # Stop all services
./visp-podman.py restart all         # Restart all services
./visp-podman.py restart session-manager  # Restart specific service

# Quadlet management
./visp-podman.py install             # Link quadlets to systemd
./visp-podman.py install --mode prod --force  # Install prod quadlets
./visp-podman.py uninstall           # Remove quadlet links
./visp-podman.py reload              # Reload systemd daemon

# Building
./visp-podman.py build --list        # List all buildable targets
./visp-podman.py build               # Build all container images

# Build service containers
./visp-podman.py build session-manager  # Session manager service
./visp-podman.py build apache        # Apache web server
./visp-podman.py build whisper       # Whisper transcription
./visp-podman.py build wsrng-server  # Random number generator

# Build session images (for RStudio/Jupyter containers)
./visp-podman.py build operations-session  # Base session (required first)
./visp-podman.py build rstudio-session     # RStudio (depends on operations)
./visp-podman.py build jupyter-session     # Jupyter (depends on operations)

# Build Node.js projects (containerized, no npm required on host)
./visp-podman.py build container-agent  # Required for dev mode
./visp-podman.py build webclient        # Default: visp config
./visp-podman.py build webclient --config datalab  # Datalab config

# Build options
./visp-podman.py build apache --no-cache  # Clean rebuild
./visp-podman.py build --pull         # Pull latest base images

# Debugging
./visp-podman.py debug session-manager  # Debug service startup issues
./visp-podman.py shell apache        # Open shell in container
./visp-podman.py exec apache ls /var/www/html  # Run command in container

# Network management
./visp-podman.py network             # Show network and DNS info
./visp-podman.py network ensure      # Create missing networks

# Image management
./visp-podman.py images              # List VISP images, networks, and build status
./visp-podman.py images base         # Audit base images from Dockerfiles (version pinning check)

# Permissions
./visp-podman.py fix-permissions     # Fix mount path permissions using podman unshare

# Database management
./visp-podman.py backup              # Backup MongoDB to current directory
./visp-podman.py backup -o /backups/db.tar.gz  # Backup to specific path
./visp-podman.py restore backup.tar.gz  # Restore from backup (with confirmation)
./visp-podman.py restore backup.tar.gz --force  # Restore without confirmation
```

### Database Backup and Restore

VISP provides MongoDB backup/restore functionality with version tracking:

```bash
# Create a timestamped backup
./visp-podman.py backup
# Output: visp_mongodb_6.0.14_20260128_091500.tar.gz

# Backup to specific directory
./visp-podman.py backup -o /backups/production.tar.gz

# Restore database (prompts for confirmation)
./visp-podman.py restore visp_mongodb_6.0.14_20260128_091500.tar.gz

# Force restore without confirmation
./visp-podman.py restore backup.tar.gz --force

# Quick backup helper script
./backup-database.sh                 # Backs up to ./backups/
./backup-database.sh /path/to/dir    # Backs up to specific directory
```

**Backup Strategy:**
- **Database**: Use `visp-podman.py backup` (small, frequent backups)
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
- Created automatically by `./visp-podman.py install`
- Secrets are never stored in quadlet files or visible via `podman inspect`
- Each container only receives the secrets it needs
- Removed automatically by `./visp-podman.py uninstall`

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
./visp-podman.py build container-agent

# Build webclient with specific configuration
./visp-podman.py build webclient                    # Default: visp config
./visp-podman.py build webclient --config datalab   # Datalab config
./visp-podman.py build webclient --config visp-pdf-server  # PDF server config

# Clean rebuild
./visp-podman.py build container-agent --no-cache
```

### Building All Image Types

VISP uses three categories of container images:

**1. Service Containers** (core infrastructure):
```bash
./visp-podman.py build apache           # Web server with Shibboleth
./visp-podman.py build session-manager  # Session orchestrator
./visp-podman.py build whisper          # Speech transcription
./visp-podman.py build wsrng-server     # Random number generator
./visp-podman.py build emu-webapp       # EMU annotation tool
./visp-podman.py build emu-webapp-server
./visp-podman.py build octra            # OCTRA transcription
```

**2. Session Images** (user environments - must build in order):
```bash
# Build base session first (contains R and common libraries)
./visp-podman.py build operations-session

# Then build specialized sessions (depend on operations-session)
./visp-podman.py build rstudio-session  # RStudio IDE
./visp-podman.py build jupyter-session  # Jupyter Notebook
```

**3. Node.js Projects** (build artifacts for services):
```bash
./visp-podman.py build container-agent  # Required for dev mode
./visp-podman.py build webclient        # Angular web interface
```

**Build all at once:**
```bash
./visp-podman.py build  # Builds all container images
```

### ⚠️ Important: Build Dependencies

**Apache container behavior:**
- **If `external/webclient/dist/` exists** → Uses it (fast) ✅
- **If dist/ missing** → Builds webclient inside container (5-10 min) ⏱️
- **Recommendation**: Always pre-build: `./visp-podman.py build webclient`

**Session images behavior:**
- Always build container-agent from source (multi-stage build)
- No pre-built check (always fresh build ~30 sec)

**Development workflow:**
```bash
# Edit webclient code
./visp-podman.py build webclient        # Rebuild dist/
systemctl --user restart apache         # Pick up new dist/
# Refresh browser

# Edit PHP code (webapi)
# Just refresh browser - mounted, auto-detected
```

**Production deployment:**
```bash
# Use version locking to ensure reproducible builds
python3 visp-deploy.py status           # Check versions
python3 visp-deploy.py lock webclient   # Lock to current tested version
git add versions.json && git commit -m "Lock webclient version"

# Build with locked versions
./visp-podman.py build webclient
./visp-podman.py build apache
```

See [Version Management](docs/VERSION_MANAGEMENT.md) for details on locking/unlocking versions.

### Inspecting Container Images

Monitor and audit container images:

```bash
# List VISP images with build status
./visp-podman.py images
# Shows:
# - All expected VISP images (visp-apache, visp-session-manager, etc.)
# - Build status (✓ built / ✗ not built)
# - Image size and creation time
# - Network backend (netavark/CNI)
# - VISP networks status
# - Container network connections

# Audit base images from Dockerfiles
./visp-podman.py images base
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
├── dev/                    # Development mode quadlets
│   ├── apache.container
│   ├── session-manager.container
│   ├── traefik.container   # Only in dev
│   ├── mongo.container
│   ├── whisper.container
│   ├── wsrng-server.container
│   └── *.network
├── prod/                   # Production mode quadlets
│   ├── apache.container    # MODE=prod, no Traefik
│   ├── session-manager.container  # DEVELOPMENT_MODE=false
│   └── ...
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
./visp-podman.py debug session-manager

# Check container-agent build (dev mode)
ls -la container-agent/dist/  # Should contain main.js
```

---

### For Development (Local) - Docker Compose (Legacy)

```bash
git clone https://github.com/humlab-speech/visible-speech-deployment.git
cd visible-speech-deployment
sudo python3 visp-deploy.py install --mode=dev
docker compose up -d
```

Access at: **https://visp.local** (add to `/etc/hosts`)

### For Production (with DNS) - Podman Quadlets

```bash
git clone https://github.com/humlab-speech/visible-speech-deployment.git
cd visible-speech-deployment

# Edit .env file - CRITICAL: Set BASE_DOMAIN and WEBCLIENT_BUILD
nano .env

# Build required images
./visp-podman.py build session-manager
./visp-podman.py build apache

# Install production quadlets
./visp-podman.py install --mode prod

# Start services
./visp-podman.py reload
./visp-podman.py start all
```

**⚠️ IMPORTANT**: The `WEBCLIENT_BUILD` setting in `.env` MUST match your `BASE_DOMAIN`. See the [Complete Deployment Guide](docs/DEPLOYMENT_GUIDE.md) for details.

## Deployment Script

The `visp-deploy.py` script automates installation, updates, and status checks:

```bash
# Install system (dev or prod mode)
sudo python3 visp-deploy.py install --mode=dev

# Check repository status, Docker images, and configuration
python3 visp-deploy.py status

# Update all repositories and components
python3 visp-deploy.py update

# Build session images
python3 visp-deploy.py build                    # Build all (no cache)
python3 visp-deploy.py build --cache            # Build all (with cache)
python3 visp-deploy.py build operations         # Build only operations
python3 visp-deploy.py build rstudio jupyter    # Build specific images

# Version locking commands (see below)
python3 visp-deploy.py lock <component>    # Lock to current version
python3 visp-deploy.py unlock <component>  # Unlock to track latest
python3 visp-deploy.py rollback <component> # Rollback to locked version
```

### Version Management

Component versions can be **locked** (pinned to specific commits) or **unlocked** (tracking latest):

- **Dev mode** (default): Installs unlocked - always pulls latest code
- **Prod mode**: Installs locked - uses tested versions from `versions.json`

**Testing updates in production:**
```bash
# 1. Unlock component to allow updates
python3 visp-deploy.py unlock webclient

# 2. Update to latest (shows commit dates and counts)
python3 visp-deploy.py update

# 3a. If update works - lock the new version
python3 visp-deploy.py lock webclient

# 3b. If update breaks - rollback to previous version
python3 visp-deploy.py rollback webclient

# 4. Commit locked versions for team
git add versions.json && git commit -m "chore: lock webclient to new version"
```

Use `--all` flag to operate on all components at once.

### What the Install Script Does

- ✅ Creates `.env` from template (non-sensitive configuration)
- ✅ Creates `.env.secrets` with auto-generated passwords
- ✅ Clones all required repositories
- ✅ Builds Node.js components in Docker containers (no host Node.js needed)
- ✅ Generates SSL certificates for local development
- ✅ Creates required directories and log files
- ✅ Sets proper file permissions
- ✅ Configures docker-compose for dev or prod mode

### Automated Demo Installation
For demo deployments, run the automated installer which will set up everything with auto-generated passwords and default settings. Node.js builds are performed in containers, so no host installation of Node.js is required.

1. Enter into visible-speech-deployment directory.
1. RUN `sudo python3 visp-deploy.py install --mode=dev` (fully automated for demo)
1. The script will install dependencies, clone repositories, build components using Node.js containers, auto-generate passwords, and build Docker images in the background.
1. Once complete, run `docker compose up -d`
1. Follow the remaining manual steps for setup (MongoDB, etc.)

**For complete instructions, see [docs/DEPLOYMENT_GUIDE.md](docs/DEPLOYMENT_GUIDE.md)**

## Included Services

- Traefik
  - Edge router

- Webserver
  - Apache + Shibboleth - Serves the main portal page and handles authentication via SWAMID

- Session Manager - Spawns and manages session containers (such as RStudio and Jupyter) on request. Also handles dynamic routing of network traffic into these containers.

- EMU-webApp - Web-based annotation tool

- OCTRA - Local mode only (only hosted, not integrated)

- LabJS - Standalone

## Prerequisites & Installation

See the **[Complete Deployment Guide](docs/DEPLOYMENT_GUIDE.md)** for:
- Prerequisites and system requirements
- Step-by-step installation for development and production
- Configuring custom domains and SSL certificates
- Adding new deployment domains to the webclient
- Post-installation configuration
- Comprehensive troubleshooting

## Quick Installation Summary

### Prerequisites

A Linux environment based on Debian or Ubuntu.

If you are using WSL2, you will run into issues if you put this project inside an NTFS mount, such as `/mnt/c`, use a location inside the WSL2 container instead, such as `~/`. Note that you need to have docker and docker-compose available.

### Install System Dependencies

```bash
sudo apt install -y curl git openssl docker.io docker-compose python3 python3-pip
sudo usermod -aG docker $USER
```

### Automated Installation

**Development Mode:**
```bash
sudo python3 visp-deploy.py install --mode=dev
docker compose up -d
```

**Production Mode:**
```bash
# Edit .env first! Set BASE_DOMAIN and WEBCLIENT_BUILD
sudo python3 visp-deploy.py install --mode=prod
docker compose build
docker compose up -d
```

### Manual Steps After Installation

1. Add to `/etc/hosts` (for local dev):
   ```
   127.0.0.1 visp.local
   127.0.0.1 emu-webapp.visp.local
   ```

2. Access test user (dev/demo):
   ```
   https://visp.local/?login=<TEST_USER_LOGIN_KEY from .env.secrets>
   ```

3. **Mongo Express** (Database Admin Interface):
   - **Local:** https://visp.local:28084 or http://localhost:28084
   - **Production:** https://me.yourdomain.com
   - **Username:** `mongo`
   - **Password:** Found in `.env.secrets` file as `MONGO_EXPRESS_PASSWORD`
   - Use this to manage users, set privileges (like `createProjects`), and inspect database

4. Configure MongoDB users as needed (see deployment guide)

## Reverse Proxy Configuration (Production)

For production deployments behind an nginx reverse proxy (common with load balancers), you need to configure nginx to handle all subdomains. The Apache container inside Docker serves multiple subdomains via VirtualHost configuration.

### Required Subdomains

- `yourdomain.com` (main VISP interface - **requires WebSocket support for login**)
- `app.yourdomain.com` (application portal - **requires WebSocket support**)
- `emu-webapp.yourdomain.com` (EMU annotation tool - separate WebSocket)
- `labjs.yourdomain.com` (LabJS experiments)
- `matomo.yourdomain.com` (analytics)
- `me.yourdomain.com` (MongoDB admin)
- `octra.yourdomain.com` (OCTRA transcription)
- `recorder.yourdomain.com` (audio recorder)

**⚠️ CRITICAL:** WebSocket support is **required** for the main domain and app subdomain. The VISP application uses WebSocket connections to `session-manager:8020` for user authentication and session management. Without WebSocket support, users cannot log in.

### Quick Setup

Pre-configured nginx configuration files are available in `nginx-configs-temp/`:

```bash
# 1. Request SSL certificates for all subdomains
cd nginx-configs-temp/
sudo ./request-certs.sh

# 2. Install the nginx configuration
sudo cp visp-demo-complete.conf /etc/nginx/sites-enabled/yourdomain.conf

# 3. Edit the config to replace 'visp-demo.humlab.umu.se' with your domain
sudo nano /etc/nginx/sites-enabled/yourdomain.conf

# 4. Test and reload
sudo nginx -t
sudo systemctl reload nginx
```

### Key Configuration Points

- **WebSocket Support**: **CRITICAL** - Required for main VISP application login and session management
  - Main domain: WebSocket connects to session-manager:8020
  - Without WebSocket: Login page will not function
- **Proxy Headers**: Must include `Host`, `X-Forwarded-For`, `X-Forwarded-Proto`
- **Upgrade Headers**: Required for WebSocket connections (`Upgrade: websocket`, `Connection: upgrade`)
- **Timeouts**: Long timeouts needed for annotation sessions (24 hours recommended)

### Load Balancer Note

If using a load balancer with SSL termination:
- You can use Let's Encrypt certificates on the backend nginx (recommended for full encryption)
- Or use self-signed certificates (simpler but less secure)
- The nginx config provided uses Let's Encrypt for defense-in-depth security

See `nginx-configs-temp/INSTRUCTIONS.md` for complete setup instructions.

## INSTALLATION

### Prerequisites

A Linux environment based on Debian or Ubuntu.

If you are using WSL2, you will run into issues if you put this project inside an NTFS mount, such as `/mnt/c`, use a location inside the WSL2 container instead, such as `~/`. Note that you need to have docker and docker-compose available.

### Automated Demo Installation

For demo deployments, run the automated installer which will set up everything with auto-generated passwords and default settings:

1. Enter into visible-speech-deployment directory.
1. RUN `sudo python3 visp-deploy.py install` (fully automated for demo)
1. The script will install dependencies, clone repositories, build components, auto-generate passwords, and build Docker images in the background.
1. Once complete, run `docker-compose up -d`
1. Follow the remaining manual steps for setup (MongoDB, etc.)

### Update System

To update the system components:

1. RUN `python3 visp-deploy.py update`
1. This will update all repositories, rebuild components, and check Docker images.

## DEVELOPMENT

### Pre-commit Hooks

This project uses pre-commit hooks to maintain code quality. The hooks are automatically installed when you first commit, and they will run on every commit to ensure:

- Python code is formatted with Black
- Python code passes Flake8 linting (line length 120, excludes generated files)
- No trailing whitespace
- Files end with newlines
- YAML files are valid (excluding problematic third-party files)
- No large files are added
- No merge conflicts exist
- No debug statements in Python code

The pre-commit hooks are configured to avoid issues with third-party code in the `mounts/` directory and generated files.

### Node.js Builds

All Node.js components (webclient, container-agent, session-manager, wsrng-server) are built using Docker containers, so no host installation of Node.js is required for development or deployment.

### Python Environment

The deployment script (`visp-deploy.py`) is written in Python 3 and handles all installation and update operations.

## TROUBLESHOOTING

### Permission Errors

**Error:** `PermissionError: [Errno 1] Operation not permitted`

**Cause:** Script trying to set file ownership to match current user's UID/GID.

**Solutions:**
- **Production deployments:** Run with `sudo python3 visp-deploy.py install`
- **Development/demo:** Run without sudo - script shows warnings but continues successfully
- **Why:** Script sets proper file ownership to match the current user for Docker container access

### Missing Dependencies

**Error:** `WARNING: Missing required dependencies`

**Solution:**
```bash
sudo apt install -y curl git openssl docker.io docker-compose
```

### Python Library Errors

**Error:** `tabulate library not found`

**Solution:**
```bash
pip3 install tabulate
# Or for user-only installation:
pip3 install --user tabulate
```

### Docker Permission Issues

**Error:** `docker: permission denied`

**Solutions:**
```bash
# Add user to docker group (logout/login required):
sudo usermod -aG docker $USER

# Or run with sudo (not recommended for development):
sudo python3 visp-deploy.py install
```

## Manual installation

These are the steps performed by the install script:

1. Copy .env-example to .env and fill it out with appropriate information.
1. Generate some local certificates. These would not be used in production, but we assume a local development installation here. `openssl req -x509 -newkey rsa:4096 -keyout certs/visp.local/c>1. Grab latest webclient `git clone https://github.com/humlab-speech/webclient`
1. Grab latest webapi `git clone https://github.com/humlab-speech/webapi`
1. Grab latest container-agent `git clone https://github.com/humlab-speech/container-agent`
1. Install & build container-agent `cd container-agent && npm install && npm run build && cd ..`
1. Install & build webclient `cd webclient && npm install && npm run build && cd ..`
1. Install Session-Manager `git clone https://github.com/humlab-speech/session-manager`
---

## WSL-Specific: Port Forwarding Workaround

If you're running VISP on **WSL2 with rootless Podman**, you may need to forward ports 80/443 from your Windows host to the Traefik ports (8080/8443) in WSL. Rootless Podman cannot bind to privileged ports (<1024) directly.

**⚠️ Important**: Forward to the **WSL IP address** (e.g., `172.29.x.x`), NOT `127.0.0.1`. Using localhost will not work.

**Setup** (run in PowerShell as Administrator on Windows):

```powershell
# Get your WSL IP address (from WSL terminal: hostname -I)
$WSL_IP = "172.29.72.57"  # Replace with your actual WSL IP

# Forward Windows port 80 → WSL port 8080
netsh interface portproxy add v4tov4 listenport=80 listenaddress=0.0.0.0 connectport=8080 connectaddress=$WSL_IP

# Forward Windows port 443 → WSL port 8443
netsh interface portproxy add v4tov4 listenport=443 listenaddress=0.0.0.0 connectport=8443 connectaddress=$WSL_IP

# Verify the rules were added
netsh interface portproxy show all
```

After this, you can access VISP from Windows at `https://visp.local` (using ports 80/443).

**Cleanup** (if you need to remove the rules later):

```powershell
# Remove port forwarding rules
netsh interface portproxy delete v4tov4 listenport=80 listenaddress=0.0.0.0
netsh interface portproxy delete v4tov4 listenport=443 listenaddress=0.0.0.0

# Verify they're removed
netsh interface portproxy show all
```
