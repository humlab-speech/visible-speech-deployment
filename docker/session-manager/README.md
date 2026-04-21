# Session Containers

This directory contains container build files for **user session containers** that are launched by the session-manager service.

## What it builds:

- `visp-jupyter-session` - Jupyter + R session image (self-contained: builds R packages and container-agent directly). Used for both interactive Jupyter sessions and short-lived operations tasks (EMU-DB creation, audio import, etc.)

## Important Distinction:

This is **NOT** for building the session-manager service itself. The session-manager service is built from:
- **Source**: `external/session-manager/` directory
- **Dockerfile**: `docker/session-manager/Dockerfile`

## Usage:

Use `visp.py` to build session images:

```bash
# Build the session image
./visp.py build jupyter-session

# Update external repos first, then rebuild
./visp.py deploy update
```

## Directory Structure:

- `jupyter-session/` - Jupyter + R session image (Dockerfile)
- `files/` - Shared files used by session containers
- `project-template-structure/` - Default project structure for new sessions
- `container-agent/` - Copied into build context automatically by visp.py

## Build Process:

1. **Prepare**: Copy `external/container-agent` into build context
2. **Build jupyter-session**: Multi-stage build — builds container-agent with Node.js, installs R packages (emuR, wrassp, etc.), sets up Jupyter environment
3. **Cleanup**: Remove temporary container-agent copy
