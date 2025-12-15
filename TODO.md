# VISP Deployment TODO

## High Priority

### Authentication & Access
- [ ] **Register production domain with SWAMID IdP**
  - Domain: `visp.pdf-server.humlab.umu.se`
  - Required files: `shibboleth2.xml`, `attribute-map.xml`, `swamid-idp.xml`
  - Contact: Umeå University IT department
  - Issue: Currently using TEST_USER_LOGIN_KEY workaround for authentication

### Matomo Analytics Integration
- [ ] **Re-enable Matomo tracking** (currently stashed in git)
  - Stash contains: `matomo-tracker.js`, docker volume mounts, documentation
  - Location: Check `git stash list` for "WIP: Matomo tracking implementation"
  - Approach: Add `<script src="/matomo-tracker.js"></script>` to application templates
  - See: `docs/MATOMO_INTEGRATION.md` (also in stash)
  - Alternative: Consider using Traefik middleware or individual app integration

## Medium Priority

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

## Low Priority / Future Enhancements

### Infrastructure
- [ ] **Migrate to Podman Quadlets** (as previously discussed)
  - Benefits: systemd integration, rootless by default, better for production
  - Status: Planning phase
  - See: `dev-notes/BUILD_STRATEGY.md`

### Code Organization
- [ ] **Consolidate duplicate configuration**
  - `.env` has some duplicated/corrupted values (admin email repetition, ABS_ROOT_PATH repetition)
  - Add validation to `visp_deploy.py` to detect and warn about duplicates

- [ ] **Create .env.example template**
  - Currently using `.env-example` if it exists
  - Should have comprehensive documented template in repo
  - Include all required and optional variables with descriptions

### Documentation
- [ ] **Document Apache vhost configuration**
  - Current issue: `ServerName https://${BASE_DOMAIN}:443` was invalid syntax
  - Fixed but should document the proper format and what variables are available

### Testing & Validation
- [ ] **Add automated deployment tests**
  - Test that all services start successfully
  - Test that authentication works (both Shibboleth and test user)
  - Test that APIs are accessible
  - Could integrate with `visp_deploy.py status` command

## Notes

### Git History
All completed tasks are documented in git commits. Use `git log` to review implementation details.

### Git Stashes
- "WIP: Matomo tracking implementation" - Contains tracker files and Docker mounts
- "WIP: Docker compose changes for Matomo tracker mounts" - Volume mount configs
