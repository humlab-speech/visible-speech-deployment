# Docker Build Contexts

This directory contains Dockerfiles and build support files for all VISP containerized services.

## Purpose

Dockerfiles define **how to build** container images. They are part of **this project**, not external dependencies.

## Contents

- `apache/` - Apache + Shibboleth + PHP web server
- `emu-webapp/` - EMU-webApp build container (multi-stage)
- `emu-webapp-server/` - EMU backend server
- `session-manager/` - Session management + RStudio/Jupyter templates
- `wsrng-server/` - WebSocket recording server
- `octra/` - OCTRA annotation tool
- `whisper/` - Whisper transcription service
- `whisperx/` - WhisperX transcription service
- `labjs/` - Lab.js experimental software
- `hs-wsr-client/` - Speech recognition client

## What Belongs Here

✅ **Dockerfiles** - Image build instructions
✅ **Build scripts** - Helper scripts for building images
✅ **Support files** - Config templates, build-time resources
✅ **Session templates** - RStudio/Jupyter/VS Code session configurations
✅ **README files** - Documentation for each service

❌ **Source code** - Goes in `external/` if it's an external repo
❌ **Runtime data** - Goes in `mounts/` for persistent storage
❌ **User files** - Goes in `mounts/repositories/`

## How It Works

Dockerfiles in this directory **reference** source code from `external/`:

```dockerfile
# Build context is ./external/session-manager
# Dockerfile copies files from that context
COPY package.json ./
COPY src/ ./src/
```

Or in some cases, clone directly during build:
```dockerfile
# Apache Dockerfile
RUN git clone https://github.com/humlab-speech/webclient
```

## Usage

### Manual Build
```bash
# Build a single service
docker compose build session-manager

# Build all services
docker compose build
```

### Via visp_deploy.py
```bash
# Builds all containerized services
python3 visp_deploy.py install
```

## Relationship to Other Directories

- **`external/`**: Contains **source code** that Dockerfiles build FROM
- **`mounts/`**: Contains **runtime data** that containers write TO

Think of it this way:
1. `external/` = **ingredients** (source code)
2. `docker/` = **recipes** (how to cook/build)
3. `mounts/` = **leftovers** (data produced at runtime)

## See Also

- `docs/FOLDER_STRUCTURE.md` - Complete explanation of directory structure
- `docs/DEV_VS_PROD.md` - Build behavior in dev vs prod modes
