# Visible Speech

This is a collection of docker containers, which as a whole makes out the Visible Speech (VISP) system.

### Automated Demo Installation
For demo deployments, run the automated installer which will set up everything with auto-generated passwords and default settings. Node.js builds are performed in containers, so no host installation of Node.js is required.

1. Enter into visible-speech-deployment directory.
1. RUN `sudo python3 visp_deploy.py install` (fully automated for demo)
1. The script will install dependencies, clone repositories, build components using Node.js containers, auto-generate passwords, and build Docker images in the background.
1. Once complete, run `docker-compose up -d`
1. Follow the remaining manual steps for setup (MongoDB, etc.)

### Update System

To update the system components:

1. RUN `python3 visp_deploy.py update`
1. This will update all repositories, rebuild components using Node.js containers, and check Docker images.

Included services:

- Traefik
  - Edge router

- Webserver
  - Apache + Shibboleth - Serves the main portal page and handles authentication via SWAMID

- Session Manager - Spawns and manages session containers (such as RStudio and Jupyter) on request. Also handles dynamic routing of network traffic into these containers.

- EMU-webApp - Web-based annotation tool

- OCTRA - Local mode only (only hosted, not integrated)

- LabJS - Standalone

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

## Manual installation

These are the steps performed by the install script:

1. Copy .env-example to .env and fill it out with appropriate information.
1. Generate some local certificates. These would not be used in production, but we assume a local development installation here. `openssl req -x509 -newkey rsa:4096 -keyout certs/visp.local/c>1. Grab latest webclient `git clone https://github.com/humlab-speech/webclient`
1. Grab latest webapi `git clone https://github.com/humlab-speech/webapi`
1. Grab latest container-agent `git clone https://github.com/humlab-speech/container-agent`
1. Install & build container-agent `cd container-agent && npm install && npm run build && cd ..`
1. Install & build webclient `cd webclient && npm install && npm run build && cd ..`
1. Install Session-Manager `git clone https://github.com/humlab-speech/session-manager`
