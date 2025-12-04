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

- [ ] **Consolidate session image build scripts**
  - Current: `docker/session-manager/build-*.sh` scripts
    - `build-rstudio.sh`, `build-jupyter.sh`, etc.
    - Manually run, separate from main deployment
    - Hard-coded base image versions
  - Questions to investigate:
    1. Should these be in docker-compose.yml?
       - Pro: Single `docker compose build` builds everything
       - Con: Session images rarely need rebuilding
       - Pro: Easier to version control base images
    2. Should visp_deploy.py handle them?
       - Add `python3 visp_deploy.py build-sessions` command?
       - Pro: Can check for new RStudio/Jupyter versions automatically
       - Con: Adds complexity to deployment script
    3. Should they stay separate?
       - Pro: Session images are optional/independent
       - Con: Easy to forget to update them
  - Base image version checking:
    - RStudio: Check https://hub.docker.com/r/rocker/rstudio/tags
    - Jupyter: Check https://hub.docker.com/r/jupyter/scipy-notebook/tags
    - Could add automatic check: "New RStudio version available: 4.3.2 -> 4.4.0"
  - Recommendation: **Integrate into visp_deploy.py** as separate command
    - `python3 visp_deploy.py build-sessions --check-updates`
    - Keeps session builds optional but more discoverable
    - Can add version checking logic
    - Benefits from our version management approach

- [ ] **Migrate to Podman Quadlets** (as previously discussed)
  - Benefits: systemd integration, rootless by default, better for production
  - Status: Planning phase
  - See: `dev-notes/BUILD_STRATEGY.md`

- [ ] **Fix permission handling in production images**
  - ✅ Fixed: wsrng-server (commit a8dcb4d)
  - [ ] TODO: Audit other services for similar issues
    - session-manager
    - emu-webapp-server
    - Check if other Node.js services have same problem

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

## Notes
- **Git stashes to review:**
  - "WIP: Matomo tracking implementation" - Contains tracker files and Docker mounts
  - "WIP: Docker compose changes for Matomo tracker mounts" - Volume mount configs

- **Branch status:**
  - Current: `feature/backend-cleanup`
  - Ahead of origin by multiple commits (need to push)
