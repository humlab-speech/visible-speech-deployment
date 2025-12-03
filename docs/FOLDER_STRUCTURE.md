# VISP Deployment Folder Structure

**Status**: Mid-refactor - External repos moved to `external/`, cleanup in progress
**Date**: December 1, 2025
**Branch**: feature/backend-cleanup

## Overview

This document explains the three main directory structures in the VISP deployment project and their purposes.

---

## 1. `external/` Directory

**Purpose**: Contains external Git repositories that are cloned and managed by the deployment system.

**Status**: ✅ **NEW - Fully implemented and working**

**Contents**:
- `external/webclient/` - VISP web frontend (Angular)
- `external/webapi/` - PHP backend API
- `external/container-agent/` - Container management agent
- `external/wsrng-server/` - WebSocket recording server (Node.js)
- `external/session-manager/` - Session management service (Node.js)
- `external/emu-webapp-server/` - EMU-webApp backend server (Node.js)
- `external/EMU-webApp/` - EMU speech annotation tool (Angular)

**Management**:
- Cloned by `visp_deploy.py` during installation
- Version-controlled via `versions.json` (supports "latest" or commit SHAs)
- **Not tracked in this repo's git** (listed in `.gitignore`)
- Source code is mounted into dev containers for hot-reload
- Code is baked into production Docker images

**⚠️ CRITICAL**: The `external/` directory **must be populated before building Docker images**.
- Docker build contexts point directly to repos in `external/` (e.g., `./external/EMU-webApp`)
- If `external/` is empty, builds will fail with "not found" errors
- **Always run `python3 visp_deploy.py install` before `docker compose build`**

**Why external/?**
- Clear separation between deployment code and external dependencies
- Centralized location for all external repos
- Easier to manage versions and updates
- Prevents confusion about what's part of this project vs external

---

## 2. `docker/` Directory

**Purpose**: Contains Dockerfiles and build contexts for containerized services.

**Status**: ⚠️ **PARTIALLY CONVERTED - Contains mix of local and external references**

**Contents**:
- `docker/apache/` - Apache + Shibboleth + PHP container (Dockerfile only)
- `docker/emu-webapp/` - EMU-webApp build container
  - ✅ `Dockerfile` - Part of this project
  - ❌ `EMU-webApp/` - **DUPLICATE! Should be removed** (now in `external/`)
  - `README.md` - Documentation
## 2. `docker/` Directory

**Purpose**: Contains Dockerfiles ONLY for services we don't control or deployment-specific infrastructure.

**Status**: ✅ **CLEANED UP - Following single source of truth principle**

**Design Principle**:
- **Services we control** (Humlab repos) → Dockerfile lives in the service's own repo (under `external/`)
- **Services we don't control** → Dockerfile lives here in `docker/`
- **Deployment-specific glue** → Dockerfile lives here in `docker/`

**Contents**:

### Deployment-Specific Infrastructure (Ours, but VISP-specific)
- `docker/apache/` - VISP Apache + Shibboleth + routing configuration
  - Not a generic service, specific to this deployment
  - Integrates multiple external services (webclient, webapi, SimpleSAMLphp)

- `docker/emu-webapp/` - EMU-webApp build wrapper
  - Builds the upstream EMU-webApp (which we forked but don't fully control)
  - Build context points to `external/` directory

### External Services (Not Ours)
- `docker/emu-webapp-server/` - ⚠️ Consider moving to external repo
- `docker/octra/` - OCTRA annotation tool (external project)
- `docker/whisper/` - Whisper transcription service (external project)
- `docker/whisperx/` - WhisperX transcription service (external project)
- `docker/labjs/` - Lab.js experimental software (external project)
- `docker/hs-wsr-client/` - Speech recognition client (external project)

### Supporting Files
- `docker/session-manager/` - Session templates and build scripts
  - ❌ No Dockerfile here (moved to external/session-manager/)
  - ✅ Session template directories (jupyter-session/, rstudio-session/, etc.)
  - ✅ Build scripts for session images
  - ✅ Supporting files
  - ✅ README explaining Dockerfile location

**What Belongs Here**:
✅ **Dockerfiles for external projects** we don't control
✅ **Dockerfiles for deployment-specific infrastructure** (Apache gateway)
✅ **Build support files** (templates, scripts, configs)
✅ **Session templates** and build contexts

**What Does NOT Belong Here**:
❌ **Dockerfiles for Humlab-controlled services** (those go in the service's repo)
❌ **Source code** from external repos (goes in `external/`)
❌ **Runtime data** (goes in `mounts/`)

**Rationale**:
Services we control should be buildable standalone without this deployment project. This enables:
- Independent development and testing
- CI/CD in the service's own repository
- Reuse by other projects
- Clear ownership: service repo owns code + how to build it

---

## 3. `mounts/` Directory

**Purpose**: Host filesystem directories that are mounted into Docker containers at runtime.

**Status**: ✅ **Stable - Working as designed**

**Contents**:
- `mounts/apache/` - Apache logs and runtime data
- `mounts/mongo/` - MongoDB data and logs (persistent database storage)
- `mounts/emu-webapp-server/` - EMU server logs and .env file
- `mounts/webapi/` - API logs and runtime files
- `mounts/session-manager/` - Session manager logs and data
- `mounts/hird-webapp/` - HIRD webapp data
- `mounts/octra/` - OCTRA runtime data
- `mounts/repositories/` - User repositories and project data
- `mounts/repository-template/` - Template for new repositories
- `mounts/transcription-queued/` - Transcription job queue
- `mounts/traefik/` - Traefik reverse proxy configuration
- `mounts/vscode/` - VS Code server data
- `mounts/whisper/` - Whisper model cache and data
- `mounts/whisperx/` - WhisperX model cache and data

**Purpose Details**:
- Provides **persistent storage** for container data
- Allows **runtime configuration** without rebuilding images
- Enables **log access** from host system
- Stores **user data** (MongoDB, repositories, transcriptions)
- **Tracked in git**: Directory structure only, not data files

**Why mounts/?**
- Docker containers are ephemeral - data inside containers is lost on restart
- Mounting host directories preserves data across container restarts
- Allows direct access to logs and config files without entering containers
- Separates runtime/user data from code and images

---

## Conversion Status

### ✅ Completed
1. Created `external/` directory structure
2. Updated `visp_deploy.py` to clone repos to `external/`
3. Updated `docker-compose.dev.yml` to use `external/` paths for volume mounts
4. Updated `docker-compose.prod.yml` to use `external/` paths for build contexts
5. Updated `.gitignore` to exclude `external/` (simplified from individual repo ignores)
6. Implemented version management via `versions.json`
7. Tested clone functionality - all 7 repos clone correctly to `external/`
8. Verified Docker Compose path resolution
9. Fixed `setup_service_env_files()` to use `external/wsrng-server/.env`
10. Updated MongoDB log creation to skip if file exists (permission fix)
11. Fixed emu-webapp build context to use `./external` directory
12. Cleaned up duplicate EMU-webApp in `docker/emu-webapp/` (moved to ARCHIVE)
13. **Implemented "Single Source of Truth" for Dockerfiles**:
    - Archived legacy `docker/session-manager/Dockerfile` (not used)
    - Archived legacy `docker/wsrng-server/Dockerfile` (not used)
    - Added README files explaining Dockerfile locations
    - Documented principle: Humlab services own their Dockerfiles
14. Verified all Docker builds complete successfully

### ⏳ Pending
1. **Test full deployment on clean system**
   - Remove `external/` and `mounts/` (backup first)
   - Run `python3 visp_deploy.py install`
   - Verify all services start correctly

2. **Consider moving emu-webapp-server Dockerfile**
   - Currently in `docker/emu-webapp-server/`
   - Should potentially move to external repo for consistency

3. **Update documentation**
   - ✅ FOLDER_STRUCTURE.md updated with Dockerfile philosophy
   - ✅ DOCKERFILE_AUDIT.md created with full analysis
   - ⏳ Update main README with new structure
   - Build context for `emu-webapp` expects `EMU-webApp/` subdirectory
   - Context should be `./external` so Dockerfile can find `EMU-webApp/`
   - **Fix**: Change docker-compose build context from `.` to `./external`

### ⏳ Pending
1. **Clean up duplicate `docker/emu-webapp/EMU-webApp/`**
   - 63MB duplicate of code now in `external/EMU-webApp/`
   - Action: Move to `ARCHIVE/docker-emu-webapp-old/` for safety

2. **Verify all Docker builds complete successfully**
   - After fixing emu-webapp build context
   - Ensure all images build with new structure

3. **Test full deployment on clean system**
   - Remove `external/` and `mounts/` (backup first)
   - Run `python3 visp_deploy.py install`
   - Verify all services start correctly

4. **Update documentation**
   - Document the external/ approach in main README
   - Update deployment guide with new structure

---

## Quick Reference

**Question**: Where does source code from external repos go?
**Answer**: `external/{repo-name}/` - cloned by visp_deploy.py

**Question**: Where are Dockerfiles stored?
**Answer**: `docker/{service-name}/Dockerfile` - part of this project

**Question**: Where does persistent data go?
**Answer**: `mounts/{service-name}/` - mounted into containers

**Question**: Are docker/* directories external repos?
**Answer**: **NO** - They are part of THIS project. Only Dockerfiles and build support files belong there, not source code.

**Question**: Why is there EMU-webApp in both docker/ and external/?
**Answer**: **Bug/leftover** - `docker/emu-webapp/EMU-webApp/` is old and should be removed. Only `external/EMU-webApp/` should exist.

---

## Build Context Rules

For services that use external repos:

1. **If Dockerfile expects `RepoName/` as subdirectory**:
   - Set build context to `./external`
   - Dockerfile will find `external/RepoName/` → copies as `RepoName/`
   - Example: emu-webapp

2. **If Dockerfile expects current directory to BE the repo**:
   - Set build context to `./external/RepoName`
   - Dockerfile sees repo root directly
   - Example: session-manager, wsrng-server (if they had custom Dockerfiles)

3. **If Dockerfile clones repo during build** (legacy approach):
   - Set build context to `./docker/ServiceName`
   - Dockerfile runs `git clone` inside container
   - Example: apache (currently does this for webclient)
   - **Note**: This approach is being phased out in favor of using external/

---

## Next Steps

1. Clean up `docker/emu-webapp/EMU-webApp/` duplicate
2. Verify emu-webapp build context fix works
3. Complete full build test
4. Commit all changes
5. Test clean installation
6. Deploy to production
