# Docker Session Containers

This directory contains build scripts and Dockerfiles for **user session containers** that are launched by the session-manager service.

## What it builds:

- `visp-rstudio-session` - RStudio environment for users
- `visp-jupyter-session` - Jupyter Notebook environment for users
- `visp-operations-session` - Operations/analysis environment for users

## Important Distinction:

This is **NOT** for building the session-manager service itself. The session-manager service is built from:
- **Source**: `../session-manager/` directory
- **Dockerfile**: `../session-manager/Dockerfile`

## Usage:

```bash
# Build all user session images
./build-session-images.sh

# Or build individual images
docker build -t visp-rstudio-session -f rstudio-session/Dockerfile .
docker build -t visp-jupyter-session -f jupyter-session/Dockerfile .
docker build -t visp-operations-session -f operations-session/Dockerfile .
```

## Directory Structure:

- `build-session-images.sh` - Main build script for all session images
- `build-session-images-no-cache.sh` - Build script without Docker cache
- `*-session/` - Individual session container directories with Dockerfiles
- `files/` - Shared files used by session containers
- `project-template-structure/` - Default project structure for new sessions
