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

- [ ] **Remove whisperx from Jupyter/RStudio images; use WhisperVault via whisper-script**

  **Problem:** The Jupyter image is **19.6 GB** and RStudio is **13.6 GB**, heavily
  bloated by whisperx + PyTorch + pyannote.audio + speechbrain (~8–10 GB of ML
  dependencies). Users don't run whisperx locally inside notebooks — transcription
  goes through session-manager → WhisperVault. The local whisperx install is dead
  weight that also causes slow image builds and excessive disk usage.

  **Current state (what's in Jupyter/RStudio today):**
  - `pip install whisperx` + full PyTorch + pyannote.audio + speechbrain + torchaudio
  - `whisperx_link_script.sh` — symlinks `/whisper_models/` → HuggingFace cache
  - `/whisper_models` bind-mount (ro) with model files
  - `/hf_cache` directory + `HF_HOME` env var
  - None of this is used by the standard VISP transcription flow

  **Proposed approach — give users whisper-script as a WhisperVault client:**

  1. **Install whisper-script + its deps** (httpx, pandas, tqdm — ~50 MB total) into
     session images instead of the full whisperx stack
  2. **Mount the WhisperVault UDS** (`mounts/whisper/api/whisperx.sock`) into Jupyter
     and RStudio containers at `/run/whisperx/whisperx.sock`
  3. **Configure whisper-script** to use the UDS instead of HTTP — needs a small change
     in `WhisperVaultTranscriber` to support `httpx.Client(transport=httpx.HTTPTransport(uds=...))`
  4. **Remove** from Dockerfiles: whisperx, PyTorch, pyannote.audio, speechbrain,
     torchaudio, pyreaper, pysptk, torchcrepe, pyworld, amfm_decompy
  5. **Remove** whisperx_link_script.sh, `/hf_cache`, `/whisper_models` volume mount

  **Issues and considerations:**
  - **whisper-script currently uses HTTP, not UDS.** `WhisperVaultTranscriber` uses
    `httpx.Client(base_url=...)` with a TCP URL. Must add UDS transport support:
    `httpx.HTTPTransport(uds="/run/whisperx/whisperx.sock")`. Small change.
  - **Authentication:** whisper-script supports optional HTTP Basic Auth via env vars.
    Session containers would need the WhisperVault credentials injected (env or secret).
    Currently session containers don't receive any WhisperVault secrets.
  - **Network isolation:** WhisperVault runs `--network=none` and only speaks UDS.
    Mounting the socket into session containers works but means **any user in any
    Jupyter session can directly call WhisperVault** — bypassing session-manager's
    queue and rate limiting. Options:
    - Accept it (users can only transcribe their own files)
    - Mount socket read-only? (won't help — UDS is bidirectional)
    - Add per-user auth tokens to WhisperVault (new feature)
    - Keep the session-manager queue path as default, expose whisper-script as a
      "power user" option only
  - **Concurrent access:** WhisperVault is single-model, single-request. If a user
    calls whisper-script from a notebook while the VISP UI queue is also running a
    transcription, one will block the other. WhisperVault handles this (FastAPI
    serializes), but the user experience may be confusing.
  - **Model reloads:** whisper-script can trigger `/reload` (model switching). If a
    notebook user reloads a different model while the UI queue expects the current one,
    session-manager's `currentPackage` tracking goes stale. Could cause unnecessary
    re-reloads or unexpected model switches mid-queue.
  - **Some researchers may genuinely want local whisperx** for custom pipelines,
    alignment experiments, or offline use. Consider making it an opt-in build variant
    (e.g. `visp-jupyter-session:ml` vs `visp-jupyter-session:latest`).
  - **Image size savings:** removing the ML stack should drop Jupyter from ~19.6 GB
    to ~8–9 GB (close to base datascience-notebook + R libraries + container-agent).
    RStudio similarly from 13.6 GB to ~7–8 GB.

  **Implementation order:**
  1. Add UDS transport support to whisper-script's `WhisperVaultTranscriber`
  2. Add socket mount + whisper-script install to Dockerfiles (alongside existing)
  3. Test: run whisper-script from inside Jupyter notebook → verify transcription works
  4. Remove whisperx + ML deps from Dockerfiles
  5. Remove whisperx_link_script.sh, /hf_cache, /whisper_models volume
  6. Rebuild images, verify size savings, test VISP UI transcription still works

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


- [ ] **MAYBE: Reconsider dev mode build strategy for webclient**
  - Current: external build via `visp.py build webclient`, mount `dist/`
  - Alternative: restore `ng build --watch` for hot-reload (had permission issues)

## Notes

- **Sass deprecation warnings**: font-awesome `@import` and `lighten()` — cosmetic,
  will become errors in Dart Sass 3.0 (future cleanup)
