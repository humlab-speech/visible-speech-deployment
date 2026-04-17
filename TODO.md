# VISP Deployment TODO

> Completed items have been removed — see `git log` for implementation details.
> Key completions: Podman quadlet migration (phases 1–3g), WhisperVault integration,
> netavark migration, Matomo analytics (ad-blocker-safe proxy rename), Angular 18→20
> upgrade, Podman secrets, orphaned bundle detection, version drift tracking, disk leak
> fix, path traversal protection (session-manager), SWAMID certificate deployment +
> login verified, `deploy status --strict`, upload permission fixes, whisperx removed
> from Jupyter image (9.3 GB), RStudio session type dropped, notebook transcription
> via `api.sock` + `visp_transcribe.py` + `Transcription.ipynb` starter notebook.

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

- [x] **Harden container security in quadlets** (for production)
  - Drop unnecessary capabilities (`DropCapability=ALL` + selective add-back)
  - Read-only filesystems where possible (`ReadOnly=true`)
  - Resource limits (`Memory=`, `CPUQuota=`)
  - `NoNewPrivileges=true` on all containers
  - WhisperX left uncapped (CPU/memory-intensive workload)
  - Dynamic session containers (Jupyter/RStudio/Operations) also hardened:
    `cap_drop ALL`, per-profile `cap_add`, `no_new_privileges`, `pids_limit`

- [ ] **🔴 Network-isolate session containers from infrastructure**

  **Verified vulnerability (2026-04-14):** Jupyter/RStudio containers on `systemd-visp-net`
  can reach ALL internal services. Tested from running Jupyter container:

  | Target | Status | Severity |
  |--------|--------|----------|
  | MongoDB (mongo:27017) | OPEN | � Auth enforced (password required), but shouldn't be reachable |
  | Session-Manager API (8080) | OPEN | 🔴 HIGH — could manipulate sessions/containers |
  | Apache (80) | OPEN | 🟡 MEDIUM |
  | EMU-webapp-server (17890) | OPEN | 🟡 MEDIUM |
  | Internet (8.8.8.8:53) | OPEN | ✅ WANTED — pip/CRAN installs need this |

  **Required behaviour:** session containers must only reach the internet, NOT other
  containers or the host. Two approaches:

  **Option A — Separate Podman network (simplest):**
  - Create `visp-sessions-net` with internet access but no connection to `visp-net`
  - Session-manager creates sessions on `visp-sessions-net` instead of `systemd-visp-net`
  - Needs: `.network` quadlet + change `VISP_NETWORK_NAME` / `getContainerConfig()`
  - Con: sessions can't resolve internal DNS names (which is the point)

  **Option B — nftables/iptables rules:**
  - Keep sessions on `visp-net` but add firewall rules to block inter-container traffic
  - More complex, fragile with rootless Podman (slirp4netns/pasta)

  **Option C — Per-project networks (full isolation):**
  - Each project gets its own Podman network (`visp-session-<projectId>`)
  - Maximum isolation (sessions can't even see other users' sessions)
  - Con: network churn, cleanup needed when sessions end

  **Recommendation:** Start with Option A — single `visp-sessions-net` network.
  Gives 90% of the security benefit with minimal complexity. Per-project networks
  (Option C) can be added later if multi-tenant isolation is needed.

### Build & Images

- [x] **Remove whisperx from Jupyter image; implement notebook transcription via api.sock**

  **Done:** whisperx, PyTorch, pyannote.audio and related ML deps removed from
  `visp-jupyter-session`. Image is now **~9.3 GB** (down from ~19.6 GB).
  RStudio session type dropped entirely — no quadlets exist for it.

  Transcription from notebooks uses `visp_transcribe.py` baked into the image.
  It communicates via `api.sock` (per-session Unix Domain Socket) →
  session-manager → WhisperVault queue. Supports all advanced options
  (beam_size, repetition_penalty, vad, vad_onset, condition_on_previous_text).
  A `Transcription.ipynb` starter notebook is copied into each new project
  directory on first session start.

- [ ] **Audit Dockerfiles for version consistency**
  - Some Dockerfiles do `git clone` without specifying version
  - Prefer using `external/` as build context (controlled by versions.json)

- [ ] **Rename Dockerfile → Containerfile** (OCI standard, cosmetic)

### Git / Release

- [ ] **Tag pre-podman state and merge `feature/podman-migration` into `master`**

  Pre-podman state tagged as `pre-podman-migration` in all repos (deployment,
  session-manager, webclient, container-agent) — tags pushed to origin.

  ```
  git checkout master
  git merge --no-ff feature/podman-migration -m "Merge podman migration into master"
  git push origin master
  git push origin pre-podman-migration
  ```

  Use `--no-ff` (merge commit) rather than squash — the 123 commits have clean
  conventional commit messages and the merge commit gives a rollback point.

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

- [ ] **Session doctor: account for `api.sock` in socket dir checks**
  - Currently the doctor only checks for `ui.sock` and `proxy.sock`
  - Should also verify `api.sock` is present for UDS Jupyter sessions
  - Missing `api.sock` means notebook transcription is unavailable but session
    otherwise works fine — worth surfacing as a warning, not a hard error

- [ ] **`visp_transcribe`: reconnect / retry on api.sock connection failure**
  - If session-manager is restarted while a Jupyter session is running, the
    `api.sock` is recreated but the notebook kernel still has the old client
  - Could implement transparent retry in `_client()`: attempt the call, and if
    connection is refused try once more after a short delay (the socket may be
    transiently unavailable during session-manager restart)


- [ ] **MAYBE: Reconsider dev mode build strategy for webclient**
  - Current: external build via `visp.py build webclient`, mount `dist/`
  - Alternative: restore `ng build --watch` for hot-reload (had permission issues)

## Notes

- **Sass deprecation warnings**: font-awesome `@import` and `lighten()` — cosmetic,
  will become errors in Dart Sass 3.0 (future cleanup)
