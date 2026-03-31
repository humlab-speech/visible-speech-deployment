# Runtime Mount Points

This directory contains host filesystem paths that are bind-mounted into Podman containers for persistent storage and runtime data.

## Purpose

Containers are ephemeral — data inside them is lost when containers are recreated. Volumes in this directory provide:
- **Persistent storage** (databases, user files)
- **Log access** from host system
- **Runtime configuration** without rebuilding images
- **Shared data** between containers

## Contents

- `apache/` - Apache logs and runtime data
- `mongo/` - **MongoDB database** (persistent storage!)
- `emu-webapp-server/` - EMU server logs and config
- `webapi/` - PHP API logs (webapi.log, webapi.debug.log, php_error.log)
- `session-manager/` - Session manager logs and data
- `repositories/` - **User repositories and project data**
- `repository-template/` - Template for new user repos
- `transcription-queued/` - Transcription job queue
- `octra/` - OCTRA runtime data
- `traefik/` - Traefik reverse proxy config (dev mode)
- `vscode/` - VS Code server data
- `whisper/` - Whisper model cache
- `whisperx/` - WhisperX model cache

## What Belongs Here

✅ **Database files** - MongoDB data, SQLite databases
✅ **Logs** - Service logs accessible from host
✅ **User data** - Uploaded files, created repositories
✅ **Caches** - Model files, temporary data
✅ **Runtime config** - `.env` files for services

❌ **Source code** - Goes in `external/` (external repos) or project root (this project)
❌ **Dockerfiles** - Goes in `docker/`
❌ **Built artifacts** - Built inside containers or baked into images

## Git Tracking

This directory structure is tracked in git, but:
- Data files are **NOT** tracked (in `.gitignore`)
- Only directory structure and essential templates are committed
- User data and logs are excluded

## Usage in Quadlets

Volumes are specified in quadlet `.container` files under `quadlets/dev/` and `quadlets/prod/`:

```ini
# Example from a .container quadlet file
[Container]
Volume=%h/Projects/visible-speech-deployment/mounts/session-manager/session-manager.log:/session-manager/logs/session-manager.log:Z
Volume=%h/Projects/visible-speech-deployment/mounts/repositories:/repositories:Z
Volume=%h/Projects/visible-speech-deployment/mounts/mongo/data:/data/db:Z
```

The `:Z` suffix is for SELinux contexts (required on RHEL/Fedora).

## Important: Database Persistence

⚠️ **Critical**: `mounts/mongo/` contains the MongoDB database!
- Deleting this = **losing all user data, projects, sessions**
- Always backup before major changes
- Included in backup scripts

## Relationship to Other Directories

- **`external/`**: Contains **source code** (what the app IS)
- **`docker/`**: Contains **build instructions** (Dockerfiles/Containerfiles)
- **`mounts/`**: Contains **runtime data** (what the app PRODUCES)

In development mode:
- `external/` is mounted for live code editing
- `mounts/` is mounted for persistent data

In production mode:
- `external/` is NOT mounted (code is baked in)
- `mounts/` IS mounted (data must persist)

## Cleanup

Most files here can be deleted to reset the system:
```bash
# WARNING: This will delete all user data!
rm -rf mounts/mongo/data/*
rm -rf mounts/repositories/*
rm mounts/*/logs/*
```

**Exception**: Keep directory structure and `repository-template/`

## See Also

- `AGENTS.md` - Comprehensive project architecture reference
