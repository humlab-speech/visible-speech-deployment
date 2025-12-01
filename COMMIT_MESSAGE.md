# Commit Summary: External Folder Refactor

## Title
Refactor: Move external repositories to external/ directory and implement Dockerfile single source of truth

## Summary
Major restructuring to improve project organization and follow single source of truth principle:
- All external Git repositories now cloned to `external/` directory
- Dockerfiles for Humlab-controlled services moved to their respective repos
- Legacy Dockerfiles archived with explanation docs
- Comprehensive documentation added

## Changes Made

### 1. External Repository Structure
- Created `external/` directory for all external Git repos
- Updated `visp_deploy.py` to clone repos to `external/{repo-name}/`
- Updated `docker-compose.dev.yml` and `docker-compose.prod.yml` to reference `external/`
- Simplified `.gitignore` to exclude entire `external/` directory

### 2. Dockerfile Organization (Single Source of Truth)
**Principle**: Services we control should own their build process

**Moved to external repos**:
- `session-manager` - Dockerfile now in `external/session-manager/` (multi-stage, modern)
- `wsrng-server` - Dockerfile now in `external/wsrng-server/` (Alpine-based, secure)

**Archived legacy Dockerfiles**:
- `docker/session-manager/Dockerfile` → `ARCHIVE/docker-legacy/`
- `docker/wsrng-server/Dockerfile` → `ARCHIVE/docker-legacy/`
- Added `DOCKERFILE_LOCATION.md` in each directory explaining the change

**Kept in docker/ directory**:
- Deployment-specific infrastructure (Apache gateway)
- External services we don't control (OCTRA, Whisper, etc.)
- Supporting files (session templates, build scripts)

### 3. Service Environment File Generation
- Fixed `setup_service_env_files()` to use `external/wsrng-server/.env`
- Auto-generates `.env` from `.env-example` + main `.env` values
- Ensures single source of truth for configuration

### 4. Build Context Fixes
- Fixed emu-webapp build context to use `./external` directory
- Cleaned up duplicate `docker/emu-webapp/EMU-webApp/` (63MB, moved to ARCHIVE)
- Verified all Docker builds complete successfully

### 5. Version Management
- Enhanced `versions.json` with `locked_version` fields
- All external repos now have documented last-known-good commits
- Supports "latest" or specific commit SHA for controlled deployment

### 6. Comprehensive Documentation
**New documents**:
- `docs/FOLDER_STRUCTURE.md` - Explains external/, docker/, mounts/ purposes
- `docs/DEV_VS_PROD.md` - Development vs production mode differences
- `docs/DOCKERFILE_AUDIT.md` - Complete analysis of all Dockerfiles
- `docs/VERSION_MANAGEMENT.md` - Guide for managing external repo versions
- `docs/EXTERNAL_FOLDER_REFACTOR.md` - Migration analysis and process
- `external/README.md` - Purpose and usage of external/ directory
- `docker/README.md` - What belongs in docker/ and why
- `mounts/README.md` - Runtime mount points explanation

**Updated documents**:
- `TODO.md` - Added comprehensive task tracking
- `versions.json` - Added locked_version fields with current commits

### 7. Permission Fixes
- Updated `create_required_directories()` to skip MongoDB log if exists (permission fix)
- Fixed permission handling for external repos

## Testing Performed
✅ Clone test: All 7 repos clone to correct location in external/
✅ Build test: All Docker images build successfully
✅ .env generation: wsrng-server/.env properly generated from .env-example
✅ Docker Compose: Path resolution works correctly
✅ Clean install: Tested full installation from scratch

## Benefits

### Portability
- Services can be built standalone without deployment project
- Each service repo is now self-contained
- Other projects can reuse our services

### Development Workflow
- Clear separation: code vs deployment config
- Dev mode: mount external/ for hot-reload
- Prod mode: bake external/ into images
- Single `git clone` gets service code + build instructions

### Maintainability
- Version control: Dockerfile changes with code
- Clear ownership: service owns its build process
- No duplicate Dockerfiles to keep in sync
- Easier to apply updates across services

### Organization
- external/ = source code (ingredients)
- docker/ = build instructions (recipes)
- mounts/ = runtime data (leftovers)

## Breaking Changes
None - all changes are internal restructuring. Services work identically.

## Migration Notes
- Old duplicate repos in project root moved to ARCHIVE/ (not deleted)
- Legacy Dockerfiles archived with explanation docs (not deleted)
- All changes backward compatible with existing deployments

## See Also
- docs/FOLDER_STRUCTURE.md - Complete structure explanation
- docs/DOCKERFILE_AUDIT.md - Detailed Dockerfile analysis
- docs/DEV_VS_PROD.md - Development vs production modes
