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

### For Development (Local)

```bash
git clone https://github.com/humlab-speech/visible-speech-deployment.git
cd visible-speech-deployment
sudo python3 visp_deploy.py install --mode=dev
docker compose up -d
```

Access at: **https://visp.local** (add to `/etc/hosts`)

### For Production (with DNS)

```bash
git clone https://github.com/humlab-speech/visible-speech-deployment.git
cd visible-speech-deployment

# Edit .env file - CRITICAL: Set BASE_DOMAIN and WEBCLIENT_BUILD
nano .env

sudo python3 visp_deploy.py install --mode=prod
docker compose build
docker compose up -d
```

**⚠️ IMPORTANT**: The `WEBCLIENT_BUILD` setting in `.env` MUST match your `BASE_DOMAIN`. See the [Complete Deployment Guide](docs/DEPLOYMENT_GUIDE.md) for details.

## Deployment Script

The `visp_deploy.py` script automates installation, updates, and status checks:

```bash
# Install system (dev or prod mode)
sudo python3 visp_deploy.py install --mode=dev

# Check repository status and configuration
python3 visp_deploy.py status

# Update all repositories and components
python3 visp_deploy.py update
```

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

3. Configure MongoDB users as needed (see deployment guide)

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
