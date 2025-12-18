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

## Quick Start

### ⚠️ Migration to Podman in Progress

**Current Status**: This project is being migrated from Docker to Podman with systemd Quadlets.

- ✅ Phase 1: Podman socket compatibility verified (node-docker-api works)
- ✅ Phase 2: systemd enabled in WSL
- ✅ Phase 3: Podman 4.6.2 installed with Quadlet support
- 🔄 Phase 4: Converting docker-compose services to Quadlets (IN PROGRESS)

**Temporary**: Docker Compose still works for now. Quadlets in `quadlets/` directory.

### For Development (Local) - Docker Compose

```bash
git clone https://github.com/humlab-speech/visible-speech-deployment.git
cd visible-speech-deployment
sudo python3 visp_deploy.py install --mode=dev
docker compose up -d
```

Access at: **https://visp.local** (add to `/etc/hosts`)

### For Production (with DNS) - Podman Quadlets (UPCOMING)

```bash
git clone https://github.com/humlab-speech/visible-speech-deployment.git
cd visible-speech-deployment

# Edit .env file - CRITICAL: Set BASE_DOMAIN and WEBCLIENT_BUILD
nano .env

sudo python3 visp_deploy.py install --mode=prod

# Copy quadlets to systemd directory
cp quadlets/*.{container,network,volume} ~/.config/containers/systemd/

# Enable and start services
systemctl --user daemon-reload
systemctl --user start visp.target
```

**⚠️ IMPORTANT**: The `WEBCLIENT_BUILD` setting in `.env` MUST match your `BASE_DOMAIN`. See the [Complete Deployment Guide](docs/DEPLOYMENT_GUIDE.md) for details.

## Deployment Script

The `visp_deploy.py` script automates installation, updates, and status checks:

```bash
# Install system (dev or prod mode)
sudo python3 visp_deploy.py install --mode=dev

# Check repository status, Docker images, and configuration
python3 visp_deploy.py status

# Update all repositories and components
python3 visp_deploy.py update

# Build session images
python3 visp_deploy.py build                    # Build all (no cache)
python3 visp_deploy.py build --cache            # Build all (with cache)
python3 visp_deploy.py build operations         # Build only operations
python3 visp_deploy.py build rstudio jupyter    # Build specific images

# Version locking commands (see below)
python3 visp_deploy.py lock <component>    # Lock to current version
python3 visp_deploy.py unlock <component>  # Unlock to track latest
python3 visp_deploy.py rollback <component> # Rollback to locked version
```

### Version Management

Component versions can be **locked** (pinned to specific commits) or **unlocked** (tracking latest):

- **Dev mode** (default): Installs unlocked - always pulls latest code
- **Prod mode**: Installs locked - uses tested versions from `versions.json`

**Testing updates in production:**
```bash
# 1. Unlock component to allow updates
python3 visp_deploy.py unlock webclient

# 2. Update to latest (shows commit dates and counts)
python3 visp_deploy.py update

# 3a. If update works - lock the new version
python3 visp_deploy.py lock webclient

# 3b. If update breaks - rollback to previous version
python3 visp_deploy.py rollback webclient

# 4. Commit locked versions for team
git add versions.json && git commit -m "chore: lock webclient to new version"
```

Use `--all` flag to operate on all components at once.

### What the Install Script Does

- ✅ Creates `.env` from template with auto-generated passwords
- ✅ Clones all required repositories
- ✅ Builds Node.js components in Docker containers (no host Node.js needed)
- ✅ Generates SSL certificates for local development
- ✅ Creates required directories and log files
- ✅ Sets proper file permissions
- ✅ Configures docker-compose for dev or prod mode

### Automated Demo Installation
For demo deployments, run the automated installer which will set up everything with auto-generated passwords and default settings. Node.js builds are performed in containers, so no host installation of Node.js is required.

1. Enter into visible-speech-deployment directory.
1. RUN `sudo python3 visp_deploy.py install --mode=dev` (fully automated for demo)
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
sudo python3 visp_deploy.py install --mode=dev
docker compose up -d
```

**Production Mode:**
```bash
# Edit .env first! Set BASE_DOMAIN and WEBCLIENT_BUILD
sudo python3 visp_deploy.py install --mode=prod
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
   https://visp.local/?login=<TEST_USER_LOGIN_KEY from .env>
   ```

3. **Mongo Express** (Database Admin Interface):
   - **Local:** https://visp.local:28084 or http://localhost:28084
   - **Production:** https://me.yourdomain.com
   - **Username:** `mongo`
   - **Password:** Found in `.env` file as `MONGO_EXPRESS_PASSWORD`
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
1. RUN `sudo python3 visp_deploy.py install` (fully automated for demo)
1. The script will install dependencies, clone repositories, build components, auto-generate passwords, and build Docker images in the background.
1. Once complete, run `docker-compose up -d`
1. Follow the remaining manual steps for setup (MongoDB, etc.)

### Update System

To update the system components:

1. RUN `python3 visp_deploy.py update`
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

The deployment script (`visp_deploy.py`) is written in Python 3 and handles all installation and update operations.

## TROUBLESHOOTING

### Permission Errors

**Error:** `PermissionError: [Errno 1] Operation not permitted`

**Cause:** Script trying to set file ownership to match current user's UID/GID.

**Solutions:**
- **Production deployments:** Run with `sudo python3 visp_deploy.py install`
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
sudo python3 visp_deploy.py install
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
