# VISP Deployment TODO

## High Priority

### Authentication & Access
- [ ] **Register production domain with SWAMID IdP**
  - Domain: `visp.pdf-server.humlab.umu.se`
  - Required files: `shibboleth2.xml`, `attribute-map.xml`, `swamid-idp.xml`
  - Contact: Umeå University IT department
  - Issue: Currently using TEST_USER_LOGIN_KEY workaround for authentication

- [x] **✅ Fix production index.php for test user login** (Completed Dec 4, 2025)
  - Fixed: TEST_USER_LOGIN_KEY now automatically sets loginAllowed:true for test users
  - Test users created via /?login=<key> no longer get 401 errors
  - No more manual MongoDB intervention needed
  - Change in: `external/webclient/src/index.php`

### Matomo Analytics Integration
- [ ] **Re-enable Matomo tracking** (currently stashed in git)
  - Stash contains: `matomo-tracker.js`, docker volume mounts, documentation
  - Location: Check `git stash list` for "WIP: Matomo tracking implementation"
  - Approach: Add `<script src="/matomo-tracker.js"></script>` to application templates
  - See: `docs/MATOMO_INTEGRATION.md` (also in stash)
  - Alternative: Consider using Traefik middleware or individual app integration

## Medium Priority

### Dependency & Version Management
- [x] **✅ Pin external repository versions in versions.json** (Completed Dec 1, 2025)
  - Added `locked_version` field to `versions.json` with current commit SHAs
  - All repos documented with last-known-good commits
  - Supports "latest" or specific commit SHA for controlled deployment
  - See: `docs/VERSION_MANAGEMENT.md` for workflow guide

- [x] **✅ Reorganize external dependencies** (Completed Dec 1, 2025)
  - Implemented: Moved all external repos to `external/` directory
  - Updated `visp_deploy.py` to clone to `external/{repo-name}/`
  - Updated `docker-compose.dev.yml` and `docker-compose.prod.yml` for external/ paths
  - Updated `.gitignore` to exclude entire `external/` directory
  - Benefits achieved:
    - Clear project structure (external/ vs internal code)
    - Easy .gitignore patterns
    - Obvious what's maintained internally vs externally
  - See: `docs/FOLDER_STRUCTURE.md` for complete explanation

### Production vs Development Separation
- [x] **✅ Clarify dev vs prod deployment modes** (Completed Dec 1, 2025)
  - Created comprehensive `docs/DEV_VS_PROD.md` documentation
  - Documented key differences:
    - Dev: Traefik proxy, source mounts, hot-reload, TEST_USER_LOGIN_KEY
    - Prod: Direct Apache, baked images, SWAMID auth, always restart
  - Clarified use cases:
    - Dev mode: Local development, testing, iteration
    - Prod mode: Behind existing reverse proxy (Nginx on host)
  - Includes migration guide and troubleshooting
  - See: `docs/DEV_VS_PROD.md` for complete guide

### Build & Deployment Improvements
- [x] **✅ Made WEBCLIENT_BUILD configurable** (Completed Dec 4, 2025)
  - Implemented: WEBCLIENT_BUILD setting in .env controls which domain to build for
  - Added: Build arg passing from docker-compose to Dockerfile
  - Enhanced: visp_deploy.py reads WEBCLIENT_BUILD and uses it for builds
  - Added: Configuration validation in `visp_deploy.py status` command
  - Added: Domain detection that works with 6.5MB minified Angular bundles
  - Supports: visp-build, visp-demo-build, visp-pdf-server-build, visp-local-build
  - Benefits: Easy multi-domain deployment, local dev without changing prod config
  - See: `docs/WEBCLIENT_BUILD_CONFIG.md` for technical details

- [x] **✅ Added comprehensive deployment documentation** (Completed Dec 4, 2025)
  - Created: `docs/DEPLOYMENT_GUIDE.md` (890 lines) - Complete step-by-step guide
  - Created: `docs/QUICK_REFERENCE.md` - Quick reference card with domain mapping
  - Created: `docs/TROUBLESHOOTING.md` - Decision tree troubleshooting guide
  - Created: `docs/WEBCLIENT_BUILD_CONFIG.md` - Technical configuration deep-dive
  - Updated: README.md with documentation links and restructured content
  - Includes: Adding new domains, dev vs prod workflows, validation checks

- [ ] **Consider moving emu-webapp-server Dockerfile to external repo**
  - Current: Dockerfile is in `docker/emu-webapp-server/`
  - Inconsistency: Other Humlab services (session-manager, wsrng-server) have Dockerfiles in their repos
  - Recommendation: Follow "single source of truth" principle
  - Benefits: Standalone buildability, version alignment with code
  - See: `docs/DOCKERFILE_AUDIT.md` for analysis

- [ ] **Add version drift detection to visp_deploy.py**
  - ✅ Partially implemented: `visp_deploy.py status` now checks uncommitted changes
  - ✅ Shows repository status: clean, has changes, ahead/behind remote
  - [ ] TODO: Add explicit drift warnings with --strict flag
  - Check if external repos have uncommitted local changes
    - Use `git status --porcelain` to detect modified/staged files
    - Warn: "external/session-manager has uncommitted changes - deploy may not be reproducible"
  - Check how far behind/ahead repos are from remote
    - Use `git fetch` + `git rev-list --count origin/main..HEAD`
    - Warn: "external/wsrng-server is 3 commits behind origin/main"
    - Warn: "external/webclient has 2 unpushed local commits"
  - Compare deployed version vs versions.json
    - Current checked out commit vs `locked_version` field
    - Warn if mismatch: "external/session-manager is at abc123 but versions.json specifies def456"
  - Add `--strict` flag to fail on any drift (for CI/CD)
  - Already in `visp_deploy.py status` command (partially)

- [ ] **Audit Dockerfiles for version consistency**
  - Problem: Some Dockerfiles do `git clone` without specifying version
    - `docker/apache/Dockerfile` - clones webclient from main branch
    - Need to check all Dockerfiles for unversioned git clones
  - Solution approaches:
    1. **Pass version as build arg** (recommended)
       ```dockerfile
       ARG WEBCLIENT_VERSION=latest
       RUN git clone --branch ${WEBCLIENT_VERSION} https://github.com/...
       ```
       Then in docker-compose: `args: WEBCLIENT_VERSION: ${WEBCLIENT_VERSION}`
    2. **Use external/ as build context** (already doing for some services)
       - Relies on versions.json controlling what's checked out
       - More consistent with our "single source of truth" approach
  - Action: Create audit script to find all `git clone` in Dockerfiles
  - Document which Dockerfiles are version-locked and which aren't

- [x] **✅ Consolidate session image build scripts** (Completed Dec 11, 2025)
  - Replaced: All 5 bash scripts with Python `SessionImageBuilder` class
    - Deleted: `build-rstudio.sh`, `build-jupyter.sh`, `build-operations-session.sh`
    - Deleted: `build-session-images.sh`, `build-session-images-no-cache.sh`
  - Implemented: `python3 visp_deploy.py build [images] [--cache]` command
    - Granular control: build all, or specific images (operations/rstudio/jupyter)
    - Default --no-cache for clean builds, optional --cache flag
  - Features:
    - Automatic build context preparation (copies container-agent)
    - Builds in correct dependency order (operations first)
    - Cleanup on success or failure
    - Better error handling than bash scripts
  - Optimizations:
    - Jupyter now copies pre-built container-agent from operations-session
    - Eliminated duplicate npm builds across images
    - Faster builds, guaranteed consistency
  - Integrated into `visp_deploy.py update` workflow
  - See: Commit 15dfca4 for implementation details

- [ ] **Migrate to Podman Quadlets** (as previously discussed)
  - Benefits: systemd integration, rootless by default, better for production
  - Status: Planning phase
  - See: `dev-notes/BUILD_STRATEGY.md`

- [x] **✅ Fix permission handling in production images** (Completed)
  - ✅ Fixed: wsrng-server (commit a8dcb4d)
  - ✅ Fixed: All services now use proper permissions via fix-permissions.sh
  - ✅ Integrated: Permission fixing in visp_deploy.py deployment workflow
  - See commits: d069db6, 7c035e2 for implementation

## Low Priority / Future Enhancements

### Code Organization
- [ ] **Consolidate duplicate configuration**
  - `.env` has some duplicated/corrupted values (admin email repetition, ABS_ROOT_PATH repetition)
  - Add validation to `visp_deploy.py` to detect and warn about duplicates

- [ ] **Create .env.example template**
  - Currently using `.env-example` if it exists
  - Should have comprehensive documented template in repo
  - Include all required and optional variables with descriptions

### Documentation
- [x] **✅ Add architecture documentation** (Completed Dec 1, 2025)
  - Created `docs/FOLDER_STRUCTURE.md` - Complete directory structure explanation
  - Created `docs/DEV_VS_PROD.md` - Development vs production architecture
  - Created `docs/DOCKERFILE_AUDIT.md` - Complete Dockerfile analysis
  - Documented request flow for both modes:
    - Dev: Nginx (host) → Traefik (container) → Apache (container) → Apps
    - Prod: Nginx (host) → Apache (container) → Apps
  - Added README files in external/, docker/, and mounts/ directories
  - See: `docs/` directory for all documentation

- [ ] **Document Apache vhost configuration**
  - Current issue: `ServerName https://${BASE_DOMAIN}:443` was invalid syntax
  - Fixed but should document the proper format and what variables are available

### Testing & Validation
- [ ] **Add automated deployment tests**
  - Test that all services start successfully
  - Test that authentication works (both Shibboleth and test user)
  - Test that APIs are accessible
  - Could integrate with `visp_deploy.py status` command

## Completed ✅
- ✅ Fixed MongoDB password synchronization between .env and services (commit 0ef0191)
- ✅ Refactored session-manager WhisperService initialization (commit ee3bf55)
- ✅ Added file permission fixing to deployment script (commits d069db6, 7c035e2)
- ✅ Fixed Docker Compose V2 compatibility
- ✅ Removed problematic Matomo Apache injection (commit 1a2333b)
- ✅ Fixed wsrng-server production image permissions (commit a8dcb4d)
- ✅ Added missing password variables to deployment script (commit 2d470d2)
- ✅ **External folder refactor** (Dec 1, 2025)
  - Moved all external repos to `external/` directory
  - Updated all paths in visp_deploy.py and docker-compose files
  - Simplified .gitignore
- ✅ **Dockerfile single source of truth** (Dec 1, 2025)
  - Humlab-controlled services now own their Dockerfiles (in external repos)
  - Archived legacy duplicate Dockerfiles
  - Added explanation docs in docker/ subdirectories
- ✅ **Version management implementation** (Dec 1, 2025)
  - Added `locked_version` fields to versions.json
  - Documented version control workflow
- ✅ **Comprehensive documentation** (Dec 1, 2025)
  - Added FOLDER_STRUCTURE.md, DEV_VS_PROD.md, DOCKERFILE_AUDIT.md
  - Added VERSION_MANAGEMENT.md, EXTERNAL_FOLDER_REFACTOR.md
  - Added README files in external/, docker/, mounts/
- ✅ **Service .env auto-generation** (Dec 1, 2025)
  - Fixed wsrng-server/.env generation from .env-example + main .env
  - Single source of truth for configuration values
- ✅ **Configurable webclient build system** (Dec 4, 2025)
  - Made WEBCLIENT_BUILD setting configurable via .env
  - Added support for multiple deployment domains (visp, visp-demo, visp-pdf-server, visp-local)
  - Enhanced visp_deploy.py with build validation and configuration checks
  - Fixed domain detection in large minified bundles (10MB read buffer)
  - Added deployment mode detection (dev vs prod) in status command
- ✅ **Deployment documentation suite** (Dec 4, 2025)
  - Created comprehensive DEPLOYMENT_GUIDE.md (890 lines)
  - Created QUICK_REFERENCE.md with domain mapping table
  - Created TROUBLESHOOTING.md with decision tree format
  - Created WEBCLIENT_BUILD_CONFIG.md technical guide
  - Restructured README.md with clear documentation links
- ✅ **Fixed TEST_USER_LOGIN_KEY authentication** (Dec 4, 2025)
  - Test users now automatically get loginAllowed:true
  - No more 401 errors requiring manual MongoDB updates
  - Fixed in external/webclient/src/index.php
- ✅ **Session image build system refactored to Python** (Dec 11, 2025)
  - Replaced 5 bash scripts with SessionImageBuilder class
  - Added `python3 visp_deploy.py build` command with granular control
  - Optimized Jupyter Dockerfile to reuse pre-built container-agent
  - Integrated build context management and cleanup
  - See commit: 15dfca4
- ✅ **Fixed MongoDB user privileges** (Dec 9, 2025)
  - Users can now create projects (added createProjects privilege)
  - Updated index.php to auto-grant privilege for test users
  - Created fix-testuser-privileges.sh helper script
- ✅ **Fixed production WebSocket connections** (Dec 9, 2025)
  - Added nginx WebSocket support with map directive
  - Fixed HTTP 502 errors during WebSocket upgrade
  - Documented in ARCHIVE/websocket-debugging-dec2024/

## Notes
- **Git stashes to review:**
  - "WIP: Matomo tracking implementation" - Contains tracker files and Docker mounts
  - "WIP: Docker compose changes for Matomo tracker mounts" - Volume mount configs

- **Branch status:**
  - Current: `feature/backend-cleanup`
  - Ahead of origin by multiple commits (need to push)
