# VISP Deployment TODO

> Completed items have been removed — see `git log` for implementation details.
> Key completions: Podman quadlet migration (phases 1–3g), WhisperVault integration,
> netavark migration, Matomo analytics (ad-blocker-safe proxy rename), Angular 18→20
> upgrade, Podman secrets, orphaned bundle detection, version drift tracking, disk leak
> fix, path traversal protection (session-manager), SWAMID certificate deployment +
> login verified, `deploy status --strict`, upload permission fixes.

## High Priority

- [ ] **Verify uploads work end-to-end in Podman**
  - Upload permission fixes are in place (`fix-permissions`, `chmod 777`, api.php)
  - Still needed: reproduce an upload while tailing Apache + session-manager logs,
    verify files land on host at `mounts/apache/apache/uploads/`
  - See AGENTS.md "File Upload Pipeline" section for debugging guide

## Medium Priority

### Security

- [ ] **Review 777 permissions on container-writable bind mounts**
  - `visp.py install` sets `chmod 777` on uploads, repositories, log directories
  - Consider `podman unshare chown`, `--userns=keep-id`, or ACLs for tighter permissions
  - Low risk on single-user server, problematic on shared systems

- [ ] **Implement socket proxy for container management**
  - session-manager and traefik have full Podman API access via socket mount
  - Consider [tecnativa/docker-socket-proxy](https://github.com/Tecnativa/docker-socket-proxy)
  - Restrict to only required API operations per service

- [ ] **Harden container security in quadlets** (for production)
  - Drop unnecessary capabilities (`DropCapability=ALL` + selective add-back)
  - Read-only filesystems where possible (`ReadOnly=true`)
  - Resource limits (`Memory=`, `CPUQuota=`)
  - Run as non-root where possible

- [ ] **Per-project network isolation for session containers**
  - Currently all session containers (Jupyter, RStudio) share `systemd-visp-net`
  - Each project should get an isolated Podman network
  - Needs dynamic network creation in session-manager (replaces static `VISP_NETWORK_NAME`)

### Build & Images

- [ ] **Pin base Docker images to specific versions**
  - 🔴 Critical: `rocker/rstudio:4` → pinned version (R version changes break packages)
  - 🟡 High: `debian:bookworm`, `node:20-bookworm-slim` (add dated snapshots)
  - 🟢 Already pinned: octra, jupyter, wsrng-server

- [ ] **Audit Dockerfiles for version consistency**
  - Some Dockerfiles do `git clone` without specifying version
  - Prefer using `external/` as build context (controlled by versions.json)

- [ ] **Rename Dockerfile → Containerfile** (OCI standard, cosmetic)

### Infrastructure

- [ ] **Finish Podman migration cleanup**
  - Test full deployment workflow on fresh install
  - Consider adding bash completion to `visp.py`

### Data Integrity

- [ ] **Quarantine orphaned bundles via explicit user action**
  - Detection already implemented in `getProjectHealthStatus()`
  - Need: `quarantineOrphanedBundles(projectId)` to move `_bndl` dirs to
    `VISP_emuDB/_quarantine/` instead of deleting
  - Webclient: add action button or extend cleanup dialog

### Documentation

- [ ] **Document Apache vhost configuration** (fixed `ServerName` syntax; needs docs)

- [ ] **Add automated deployment tests**
  - Service startup, authentication, API accessibility
  - Could integrate with `visp.py deploy status`

## Low Priority

- [ ] **Add `repair-session` command** to re-run emuDB import for a single session
  - Currently broken bundles require delete + re-upload + re-create
  - Would re-run emuDB import in-place for a single session

- [ ] **Investigate crash recovery for running sessions**
  - Orphaned Jupyter/RStudio containers after session-manager crash
  - Label containers with metadata, reconstruct state on restart

- [ ] **MAYBE: Proxy session container network traffic**
  - Jupyter/RStudio containers can access host LAN
  - Options: squid proxy, nftables filtering, Podman network policy
  - Priority depends on deployment security requirements

- [ ] **MAYBE: Reconsider dev mode build strategy for webclient**
  - Current: external build via `visp.py build webclient`, mount `dist/`
  - Alternative: restore `ng build --watch` for hot-reload (had permission issues)

## Notes

- **Sass deprecation warnings**: font-awesome `@import` and `lighten()` — cosmetic,
  will become errors in Dart Sass 3.0 (future cleanup)
