# VISP Deployment TODO

## Immediate / Blocking

- [x] **DISK LEAK: Uploaded audio files accumulate forever in `mounts/apache/apache/uploads/`** ✅
  - **Fixed** in session-manager commit `f7603b3` (pushed to GitHub).
  - **Option A** (delete on success): After all container-agent commands complete and the
    operations session is torn down, the `formContextId` upload directory is removed via
    `fs.removeSync()`.
  - **Option B** (lazy cleanup): At the start of each `saveProjectEmuDb()` call, stale
    upload directories (>24h old, not the current formContextId) are scanned and removed.
  - Both are non-fatal — failures logged as warnings.
  - **Note**: Existing accumulated uploads (2.2 GB on dev) will be cleaned up on the next
    `saveProject` call per Option B, or can be manually removed:
    `rm -rf mounts/apache/apache/uploads/*/`

- [x] **Remove stale `whisper.container` symlink from `~/.config/containers/systemd/`** ✅
  - `quadlets/dev/whisper.container` was renamed to `whisper.container.old` (via git mv) but
    the symlink in `~/.config/containers/systemd/whisper.container` was never removed
  - Fixed: Cleaned up during `visp.py uninstall`/`install` cycles

- [x] **Build missing `visp-emu-webapp-server` image** ✅
  - Built and running. Also committed WebSocket cookie fix (commit `3b73022`).

## High Priority

- [ ] **Replace visp-whisper with WhisperVault** 🔄
  - **Repo**: https://github.com/humlab-speech/WhisperVault
  - **Why**: WhisperVault is purpose-built for offline/airgap deployment, runs WhisperX with
    `--network=none` (zero internet access after model download), communicates over a Unix
    Domain Socket (UDS) instead of TCP, and has a clean HTTP API. The current `visp-whisper`
    image (Whisper-WebUI/Gradio) is heavier, exposes a full Gradio UI, and isn't designed
    for programmatic use.

  ### Architecture: the socket problem
  - `session-manager` (a container) currently calls whisper over the `whisper-net` Podman
    network: `GRADIO_WHISPERX_ENDPOINT=http://whisper:7860`
  - WhisperVault's transcription container runs with `--network=none` — it **cannot** be on
    any network. The only IPC path is a Unix Domain Socket at
    `mounts/whisper/api/whisperx.sock` on the host.
  - `session-manager` is also a container — it cannot reach a host socket directly unless
    the socket directory is bind-mounted into it.

  ### Chosen architecture: direct socket mount (no sidecar)

  The nginx sidecar (`whisperx-nginx`) is **not needed** and has been dropped.
  Node.js `http.request()` natively supports Unix Domain Sockets via the `socketPath`
  option — no extra packages, no extra container, no extra moving part.

  ```
  [whisperx]   --network=none  ←→  mounts/whisper/api/whisperx.sock
                                          ↑ bind-mounted into session-manager
  [session-manager]  →  http.request({ socketPath: '/run/whisperx/whisperx.sock',
                                        path: '/transcribe', method: 'POST', ... })
  ```

  - `whisperx` stays `Network=none` — fully air-gapped
  - `mounts/whisper/api` on the host is bind-mounted into both containers
    (`/run/api` in whisperx, `/run/whisperx` in session-manager)
  - No network interface involved at any point; socket never leaves the host
  - `whisper-net` Podman network is no longer needed for whisper
  - `whisperx-nginx.container` quadlets are archived/not installed

  **⚠️ Note**: This is an optional service — the rest of the system (apache, session-manager,
  mongo, wsrng-server, etc.) must work without whisperx running. Do not add hard
  `Requires=whisperx.service` to session-manager; transcription should fail gracefully.

  ### What needs to change

  1. **Add WhisperVault to `external/` and `versions.json`** — same pattern as all other
     components. `deploy update` will keep it current, `deploy status` will show when
     the image is stale vs source, `deploy lock` pins it for production.

     The one wrinkle: WhisperVault has `whisperx/` as a git submodule. `git_repo.py`'s
     `clone()` currently does a plain `git clone` with no `--recurse-submodules`, and
     `pull()` doesn't run `git submodule update --init --recursive` afterwards. This needs
     a small fix before adding WhisperVault:

     - Add a `submodules: true` field to the `versions.json` entry for WhisperVault
     - In `git_repo.py`, extend `clone()` to accept a `recurse_submodules` flag and pass
       `--recurse-submodules` to git when set
     - In `deploy.py` `update_components()`, after a successful pull, run
       `git submodule update --init --recursive` if the component's `versions.json` entry
       has `submodules: true`
     - This keeps the fix scoped — no impact on the other components that have no submodules

     `versions.json` entry:
     ```json
     "WhisperVault": {
       "url": "https://github.com/humlab-speech/WhisperVault",
       "version": "latest",
       "submodules": true
     }
     ```

  2. **Update `BUILD_CONFIGS` in `visp.py`**:
     - Remove/replace `"whisper"` entry
     - Add `"whisperx"` entry pointing to `external/WhisperVault` with
       `dockerfile: "container/Containerfile"`, `image: "visp-whisperx"`
     - Add `"whisperx-nginx"` entry for the sidecar

  3. **New quadlets** (both dev and prod):
     - `whisperx.container` — replaces `whisper.container`:
       ```ini
       [Container]
       ContainerName=whisperx
       Image=localhost/visp-whisperx:latest
       Network=none                          # air-gapped
       Volume=%h/.../mounts/whisper/models:/models/extra:ro,Z
       Volume=%h/.../mounts/whisper/api:/run/api:Z   # socket dir
       Environment=WHISPERX_MODEL=/models/extra/kb-whisper-large-ct2
       Environment=WHISPERX_LANGUAGE=sv
       Environment=WHISPERX_DEVICE=cpu
       Environment=WHISPERX_COMPUTE_TYPE=float32
       ```
     - `whisperx-nginx.container` — the TCP sidecar (Option A):
       ```ini
       [Container]
       ContainerName=whisperx-nginx
       Image=localhost/visp-whisperx-nginx:latest
       Network=whisper-net.network
       Volume=%h/.../mounts/whisper/api:/run/api:ro,z  # read-only access to socket
       ```

  4. **Update `session-manager` quadlet**:
     - Change `GRADIO_WHISPERX_ENDPOINT=http://whisper:7860`
       → `WHISPERX_ENDPOINT=http://whisperx-nginx:8088`
     - (Check what env var name session-manager actually reads — may need updating in
       `external/session-manager` config too)
     - Update `After=` and `Requires=` to reference `whisperx.service` and
       `whisperx-nginx.service`

  5. **Model directory layout** — WhisperVault expects:
     ```
     mounts/whisper/models/
       kb-whisper-large-ct2/        (Swedish ASR, 2.9 GB)
       wav2vec2-large-voxrex-swedish/ (alignment, 2.4 GB)
       pyannote-speaker-diarization/  (diarization, 32 MB)
       pyannote-segmentation/         (32 MB)
       paraphrase-multilingual-MiniLM-L12-v2/ (embeddings, 926 MB)
       cache/torch/hub/checkpoints/   (English alignment, 361 MB)
     ```
     Check whether existing `mounts/whisper/models/` already has any of these from
     the previous whisper setup.

  6. **`visp.py deploy update`** — add WhisperVault to `versions.json` component list
     so `deploy update` clones it and `deploy status` tracks its version.

  7. **Startup ordering note** — WhisperVault loads the model at container start and the
     socket only appears once the model is ready (~15–60s). The quadlet `TimeoutStartSec`
     should be set to at least 300s. Consider a health check or `ExecStartPost` script that
     polls the socket before marking the service ready, similar to what `manage.py start`
     does.

  ### Files to create/change
  - `docker/whisper/Dockerfile` → can be deleted (replaced by WhisperVault Containerfile)
  - `quadlets/dev/whisper.container` → replaced by `whisperx.container` + `whisperx-nginx.container`
  - `quadlets/prod/whisper.container` → same
  - `visp.py` → BUILD_CONFIGS update
  - `external/WhisperVault/` → new clone
  - `mounts/whisper/` → model directory structure update

- [ ] **Update session-manager to use WhisperVault endpoint** 🔌
  - **Blocked by**: WhisperVault integration above (needs whisperx-nginx running)
  - **Problem**: `session-manager` currently calls the old Gradio UI endpoint:
    `GRADIO_WHISPERX_ENDPOINT=http://whisper:7860`
    The new WhisperVault API is a clean HTTP REST API over a **Unix Domain Socket** — no
    nginx sidecar, session-manager speaks directly to the socket.
  - **What to change in `external/session-manager/src/WhisperService.class.js`**:
    - Remove: `@gradio/client` import, `Client.connect()`, `gradioConn`/`gradioReady` state,
      `upload_files()`, `predict("/transcribe_file", {param_1...param_53})`, URL-based result download
    - Replace with: `http.request({ socketPath: process.env.WHISPERX_SOCKET_PATH, path: '/transcribe',
      method: 'POST', ... })` using multipart body (`audio=<fileBuffer>` + `params=JSON`)
    - Result: inline JSON — `response.outputs.srt` / `response.outputs.txt` (no second HTTP download)
    - Ready-check: `GET /health` on the socket → `{"ready": true}` replaces Gradio polling
  - **Quadlet changes** ✅ **DONE** — both dev and prod `session-manager.container` updated:
    ```ini
    After=... whisperx.service mongo.service
    # No Requires= for whisperx — transcription must fail gracefully if whisper not running
    Environment=WHISPERX_SOCKET_PATH=/run/whisperx/whisperx.sock
    Volume=%h/.../mounts/whisper/api:/run/whisperx:ro,Z
    ```
    Removed `GRADIO_WHISPERX_ENDPOINT`, `BASIC_AUTH_USERNAME`, `BASIC_AUTH_PASSWORD`.
    Note: session-manager quadlets still have the old `whisperx-nginx` references from the
    previous plan — update them when implementing this item.
  - **API mapping** (old Gradio → new WhisperVault):
    - Transcribe: `POST /transcribe` with multipart `audio` + `params` JSON string
    - Response: `{ "outputs": { "txt": "...", "srt": "..." }, "segments": [...] }`
    - Health check: `GET /health` → `{ "ready": true, ... }`
    - See full API docs in `external/WhisperVault/README.md`

- [x] **Integrate version checking into visp.py build** ✅
  - Implemented: `visp.py build` checks versions.json, warns/fails on drift
  - `visp.py deploy status` shows build staleness
    - For Node.js builds: Check if dist/ is older than source code timestamp
    - Add `--force` flag to skip checks
  - **Acceptance criteria**:
    - `./visp.py build webclient` checks git HEAD vs versions.json
    - Production mode (when locked): Fails if version mismatch
    - Dev mode (when unlocked): Warns but continues
    - Add to status command: Show version drift

- [ ] **Uploads failing in Podman (high priority)** ⚠️
  - **Why**: PHP upload handler fails to create/move uploaded files into the mounted upload staging path (`/tmp/uploads` → host `mounts/apache/apache/uploads`). Observed `mkdir(): Permission denied` and `move_uploaded_file(): Unable to move ...` in Apache PHP error logs (example: 2026-01-29 10:07:32 for upload id `p1aLa0PMC8UEsjcwMsE8o`). Probe tests show the Apache worker (`www-data`) cannot write into per-upload directories (owner-only directories and some created with owner UID `100032`). After a temporary `chmod -R 777` retry, files still didn't appear (likely a new upload id or client-side failure), so uploads are effectively failing in this Podman environment.
  - **Where to look (logs & paths)**:
    - Apache PHP error log: `mounts/apache/apache/logs/apache2/visp.local-error.log` (search for `mkdir(): Permission denied` / `move_uploaded_file`)
    - Apache container logs show `chown: changing ownership of '/var/log/shibboleth/*': Operation not permitted` which can cause 500 errors if Shibboleth fails to start.
    - Apache access log: `mounts/apache/apache/logs/apache2/visp.local-access.log` (search for `POST /api/v1/upload`)
    - Upload staging path: `mounts/apache/apache/uploads/<gitlabUserId>/<upload-id>/...` (bind-mounted into container at `/tmp/uploads`)
    - Session-manager logs: use `./visp-logs.sh session-manager` — look for `Converting all files in /tmp/uploads` and `No audio files found` messages
    - ~~**Note:** `./visp.py logs <service>` previously had an "unhashable type: 'list'" argument error — **fixed**.~~
  - **Immediate mitigation / recommended fix**:
    - Make upload tree group-owned by `www-data` and group-writable (e.g., `chgrp -R www-data mounts/apache/apache/uploads && find mounts/apache/apache/uploads -type d -exec chmod 2775 {} + && find mounts/apache/apache/uploads -type f -exec chmod 664 {} +`)
    - Ensure PHP api.php creates directories with group-write (e.g., `mkdir(..., 0o2775)` or set proper umask) and consider using setgid for inheritance
    - Improve api.php error propagation so move failures return meaningful errors to the UI instead of silent failures
  - **Recent improvements** ✅ (commits c703ab3, f79bc70, 7944407):
    - Added `set-unshare.sh` script for fixing UID/GID in mounted directories
    - Expanded `fix-permissions` with comprehensive ownership/mode fixes (now `./visp.py fixperm`)
    - Fixed file permissions on Apache vhost configs and SAML configs
    - **Still needed**: Run `./visp.py fixperm --apply` post-deploy and verify uploads work
  - **Acceptance / next steps**:
    - Reproduce an upload while tailing Apache access+error logs and session-manager logs; capture the new upload id and verify files are present on the host
    - Add an automated test/CI check to verify uploads succeed under Podman with the configured mounts
    - Prioritize as **High** — blocks user uploads in Podman deployments

- [x] **Document netavark requirement in installation guides** 📝 (PARTIAL)
  - **Status**: ✅ Infrastructure mostly complete, docs partially done
  - **Completed**:
    - [x] README.md - Added netavark prerequisite and migration info
    - [x] visp.py - Added automatic netavark detection and configuration
    - [x] visp.py - Added automatic network creation (quadlets don't auto-create)
    - [x] Automation Features: Detects CNI, configures netavark, auto-creates networks, validates backend
  - **Still needed**:
    - [ ] DEPLOYMENT_GUIDE.md - Add detailed migration procedure
    - [ ] docs/QUICK_REFERENCE.md - Update network troubleshooting section
    - [ ] Testing: Fresh install with netavark, migration from CNI, network auto-creation

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

- [ ] **Review security of 777 permissions on container-writable bind mounts** 🔒
  - **Introduced by**: `201ab6a` (visp.py install auto-fix),
    `78b51ec` (api.php `createDirectory()` mkdir 0777), and the `fix-permissions`
    command default paths update (same branch, covers uploads + logs + repositories)
  - **Context**: `visp.py install` now sets `chmod 777` on several bind-mounted
    directories (`mounts/apache/apache/uploads/`, `mounts/repositories/`, log directories)
    so that Apache's `www-data` (UID 33) and session-manager's `root` (UID 0, mapped from
    host user) can both write to them. The api.php `createDirectory()` also uses `mkdir 0777`.
  - **Concern**: World-writable directories on the host could be exploited by other local
    users or processes. On a single-user server this is low risk, but on shared systems
    it could be problematic.
  - **Possible solutions**:
    - Use `podman unshare chown 33:33` to make directories owned by www-data inside the
      user namespace, then use `770` or `775` instead of `777`. session-manager (running as
      root in its container, mapped from the host user) would still have access.
    - Use Podman's `--userns=keep-id` or `uidmap`/`gidmap` options to align UIDs so the
      host user directly maps to www-data, avoiding the permission gap entirely.
    - Use named Podman volumes instead of bind mounts for ephemeral data (uploads, logs)
      — Podman manages ownership automatically for named volumes.
    - Set a restrictive umask on the host and rely on ACLs (`setfacl`) to grant www-data
      write access without making directories world-writable.
  - **Action**: Research which approach is best for both dev (WSL) and prod (Linux server)
    environments and implement a more restrictive permission scheme.

- [ ] **Per-project network isolation for session containers** 🔒
  - **Current behaviour**: `Session.class.js` attaches every spawned container (Jupyter,
    RStudio, etc.) to the shared `systemd-visp-net` network. This means containers from
    different projects and different users can reach each other over the internal network.
  - **Desired behaviour**: Each project (or each session) should get its own isolated Podman
    network so that a container in project A cannot talk to a container in project B.
    session-manager already communicates with spawned containers through the Docker API
    (not through the network), so isolation at the network level is feasible without breaking
    the management plane.
  - **Approach**:
    1. In `session-manager`, when creating a session container, call
       `docker.createNetwork({ Name: 'visp-project-<projectId>' })` (if it doesn't already
       exist) and attach the container to that network instead of `systemd-visp-net`.
    2. The project network only needs to be reachable by the Apache proxy for the user's
       WebSocket/HTTP session — Traefik or Apache would need to be able to reach it too, or
       alternatively the session container gets a port exposed on `systemd-visp-net` only for
       the proxy tunnel.
    3. On session teardown, remove the container's network if no other containers use it
       (reference-count by project).
    4. Consider whether containers within the *same* project should be able to talk to each
       other (probably yes — a Jupyter notebook calling an R script, etc.) or whether
       per-session isolation is needed.
  - **Blocking concern**: The current `VISP_NETWORK_NAME` env var selects a single shared
    network. This needs to become dynamic, not a static environment variable.
  - **Note**: This is a deeper change to `session-manager` source code in `external/` — needs
    coordination with the upstream repo.

- [x] **Merge visp.py into visp.py as subcommand** ✅
  - **Completed 2026-04-08**: All functionality migrated to `visp.py deploy` + `vispctl/`
  - `visp.py` replaced with thin deprecation wrapper (original archived to `ARCHIVE/`)
  - `setup_service_env_files()` ported to `_setup_service_env_files()` in `visp.py install`
  - Docker Compose legacy code (install_system, docker-compose mode) dropped
  - All documentation references updated

### Security Improvements
- [ ] **Implement socket proxy for container management**
  - **Current**: Direct socket mount (`/run/user/1000/podman/podman.sock:/var/run/docker.sock`)
    - session-manager and traefik have full access to Podman API
    - Security risk: compromised container = full control over host containers
  - **Recommendation**: Use socket proxy with restricted permissions
    - Example: [tecnativa/docker-socket-proxy](https://github.com/Tecnativa/docker-socket-proxy)
    - Or create custom proxy for Podman socket
    - Restrict API access to only required operations:
      - session-manager needs: `POST /containers/create`, `POST /containers/{id}/start`, `DELETE /containers/{id}`, `GET /containers/json`
      - traefik needs: `GET /containers/json` (read-only container discovery)
  - **Benefits**:
    - Principle of least privilege
    - Limits blast radius of container compromise
    - Can log/audit socket API calls
    - Standard security hardening for production
  - **Implementation**:
    - Deploy socket-proxy as separate container
    - Configure allowed API endpoints via environment variables
    - Update session-manager and traefik to use proxy socket instead
    - Test all container operations still work

- [x] **~~Consider moving emu-webapp-server Dockerfile to external repo~~** ✅ Done in master (commit 4e5a3f0)
  - ~~Current: Dockerfile is in `docker/emu-webapp-server/`~~
  - ~~Inconsistency: Other Humlab services (session-manager, wsrng-server) have Dockerfiles in their repos~~
  - ~~Recommendation: Follow "single source of truth" principle~~
  - ~~Benefits: Standalone buildability, version alignment with code~~
  - ~~See: `docs/DOCKERFILE_AUDIT.md` for analysis~~

- [ ] **Pin base Docker images to specific versions** 🔒
  - **Current**: Most Dockerfiles use mutable tags (debian:bookworm, node:20, rocker/rstudio:4)
  - **Risk**: Rebuilding same Dockerfile tomorrow gives different results due to upstream updates
  - **Impact**: Non-reproducible builds, potential breakage from upstream changes
  - **Where versions are stored**: Currently hardcoded in each Dockerfile (no centralized config)
  - **Options for centralization**:
    1. **ARG in Dockerfile + build-args from visp.py** (flexible, requires code change)
       ```dockerfile
       ARG DEBIAN_VERSION=bookworm-20240101
       FROM debian:${DEBIAN_VERSION}
       ```
       Then: `podman build --build-arg DEBIAN_VERSION=bookworm-20240201`
    2. **Hardcode in Dockerfile** (simple, current approach, just update to pinned versions)
    3. **New docker-versions.json** (like versions.json but for base images)
    4. **.env file** (could work but mixing config types is not ideal)
  - **Recommendation**: Start with option 2 (hardcode pinned versions), consider option 1 later for flexibility
  - **Priority levels**:
    - 🔴 **Critical**: `rocker/rstudio:4` → `rocker/rstudio:4.3.2` (R version changes break packages)
    - 🟡 **High**: debian:bookworm, node:20-bookworm-slim (add dated snapshots)
    - 🟢 **Done**: octra (node:20.5.1 ✅), jupyter (r-4.3.3 ✅), wsrng-server (node:20.20.0-alpine3.22 ✅)
  - **Implementation steps**:
    1. Document current working versions: `podman inspect <image> | grep -A5 RepoDigests`
    2. Test builds with pinned versions
    3. Update Dockerfiles with specific versions/digests
    4. Document update procedure in docs/
  - **Example change**:
    ```dockerfile
    # Before
    FROM debian:bookworm
    FROM node:20-bookworm-slim
    FROM docker.io/rocker/rstudio:4

    # After (dated tags - readable)
    FROM debian:bookworm-20240101
    FROM node:20.11.1-bookworm-slim
    FROM docker.io/rocker/rstudio:4.3.2

    # Or (digests - most secure but less readable)
    FROM debian@sha256:abc123...
    FROM node@sha256:def456...
    ```

- [ ] **Rename Dockerfile → Containerfile** 📝
  - **Why**: Containerfile is the OCI-standard generic name (not Docker-specific)
  - **Status**: Podman fully supports both names, Docker support is TBD
  - **Risk**: Docker CLI might not recognize Containerfile (need to verify Docker version support)
  - **Note**: As of 2024, Docker officially supports Containerfile, but verify on target systems
  - **Implementation**:
    - Rename all Dockerfile → Containerfile (`git mv` to preserve history)
    - Update visp.py BUILD_CONFIGS: `"dockerfile": "Containerfile"`
    - Update all documentation references
    - Test with both podman and docker (if docker is still used anywhere)
  - **Alternative**: Keep Dockerfile for now, rename when Docker support is universal
  - **Links**:
    - [Containerfile spec](https://github.com/containers/common/blob/main/docs/Containerfile.5.md)
    - [Docker Containerfile support](https://docs.docker.com/engine/reference/builder/)

- [x] **Add version drift detection to deploy status** ✅
  - Implemented in `visp.py deploy status`
  - Shows repository status, uncommitted changes, ahead/behind remote
  - Build staleness tracking via git.commit image labels
  - [ ] TODO: Add `--strict` flag to fail on any drift (for CI/CD)

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

- [ ] **Harden container security in quadlets** ⚠️ Important for production
  - **When**: After all services are verified working
  - **Goal**: Apply principle of least privilege to all containers
  - **Hardening checklist per service**:
    - [ ] Drop unnecessary Linux capabilities
      - Add `DropCapability=ALL` then selectively add back only needed caps
      - Example: Most containers don't need `CAP_NET_ADMIN`, `CAP_SYS_ADMIN`
      - session-manager needs container creation caps (keep current permissions)
    - [ ] Make filesystems read-only where possible
      - Add `ReadOnly=true` for containers that don't write to filesystem
      - Use `Volume=` with `:ro` suffix for read-only mounts
      - Example: whisper, octra likely don't need write access to container FS
    - [ ] Remove network access where not needed
      - Review which containers actually need network access
      - Example: mongo only needs internal network access (already isolated)
    - [ ] Add resource limits
      - `Memory=2G` for containers that may consume excessive memory
      - `CPUQuota=200%` to prevent CPU starvation
    - [ ] Run as non-root user where possible
      - Add `User=<uid>` to run container as specific user
      - Many images already support this (check Dockerfile USER directive)
  - **Testing approach**:
    - Apply hardening incrementally, one container at a time
    - Test full workflow after each change
    - Document any capabilities/permissions actually required

- [ ] **MAYBE: Proxy session container network traffic** 🤔 Consider for high-security environments
  - **Problem**: Jupyter/RStudio containers can access host LAN
    - Users could potentially probe internal network from session containers
    - Security risk in environments with sensitive internal services
  - **Proposed solution**: Network isolation proxy
    - Allow outgoing internet access (for pip/CRAN installs)
    - Block access to private IP ranges (10.0.0.0/8, 172.16.0.0/12, 192.168.0.0/16)
    - Block access to host machine and other containers
  - **Implementation options**:
    - **Option 1**: squid proxy with ACL rules
    - **Option 2**: Dedicated network with iptables/nftables filtering
    - **Option 3**: Podman network plugin with policy enforcement
  - **Trade-offs**:
    - ✅ Pro: Stronger isolation, defense in depth
    - ✅ Pro: Prevents lateral movement in case of container compromise
    - ❌ Con: More complex networking setup
    - ❌ Con: May break legitimate use cases (accessing internal APIs)
    - ❌ Con: Performance overhead from proxy
  - **Decision criteria**:
    - **High priority** if: Deployment on network with sensitive internal services
    - **Low priority** if: Development environment or isolated network segment
    - **Skip** if: Sessions are only used by trusted administrators

- [ ] **MAYBE: Reconsider dev mode build strategy for webclient** 🔨
  - **Current**: External build via `visp.py build webclient`, mount dist/ directory
  - **Previous**: In-container `ng build --watch` for hot-reload during development
  - **Issue**: Permission conflicts between mounted files and container build process
    - Container running as root couldn't overwrite nobody:nogroup owned files
    - Caused 20-second delays on startup as build repeatedly failed
  - **Options**:
    1. Keep external build (current) - cleaner separation, explicit rebuilds
    2. Fix permissions and restore watch mode - better DX for active frontend dev
    3. Hybrid: env var to enable/disable watch mode based on workflow
  - **Decision**: Keeping external for now (removed watch from Dockerfile)

## High Priority

### Infrastructure Migration
- [ ] **Migrate to Podman Quadlets** (In Progress - branch: feature/podman-migration)
  - Benefits: systemd integration, rootless by default, better for production
  - Current Status: Phase 1 complete, investigating systemd on WSL
  - See: `dev-notes/BUILD_STRATEGY.md`

  **Environment Strategy**:
  - **Production deployment**: Use Podman Quadlets with systemd (standard approach)
  - **WSL development**: Two options:
    1. Enable systemd in WSL via `/etc/wsl.conf` to use Quadlets locally
    2. Use manual `podman` commands or shell scripts for development
  - **Note**: Quadlets require systemd running as PID 1 (not available in default WSL)

  **Docker Dependencies Found:**

  1. **Node.js session-manager (external/session-manager/)**
     - File: [src/SessionManager.class.js](external/session-manager/src/SessionManager.class.js#L18)
       - Line 18: `this.docker = new Docker({ socketPath: '/var/run/docker.sock' })`
       - Uses node-docker-api library to create/list/inspect/delete session containers
     - File: [src/Session.class.js](external/session-manager/src/Session.class.js#L30)
       - Line 30: `this.docker = new Docker({ socketPath: '/var/run/docker.sock' })`
       - Methods: createContainer(), delete(), runCommand(), commit()
     - File: [package.json](external/session-manager/package.json)
       - Dependency: `"node-docker-api": "^1.1.22"`
     - **Action**: Test node-docker-api compatibility with Podman socket
       - Podman provides Docker socket emulation at `/run/podman/podman.sock`
       - May need to update socket path or create compatibility symlink
       - Verify container operations: create, list, inspect, delete, exec

  2. **Docker Compose socket mounts**
     - Files: docker-compose.yml, docker-compose.dev.yml, docker-compose.prod.yml
     - Traefik service: `/var/run/docker.sock:/var/run/docker.sock` (for auto-config)
     - session-manager service: `/var/run/docker.sock:/var/run/docker.sock:Z` (for spawning sessions)
     - **Action**: Update socket paths for Podman
       - Option 1: Mount `/run/podman/podman.sock:/var/run/docker.sock`
       - Option 2: Enable Podman Docker-compatible socket and keep paths
       - Option 3: Use Podman socket API directly (requires code changes)

  3. **Shell scripts using docker CLI**
     - File: [cleanup-session-containers.sh](cleanup-session-containers.sh)
       - Line 19: `docker ps -a --filter "name=hsapp-session-" --filter "name=visp-session-"`
       - Multiple `docker ps`, `docker exec` commands
       - **Action**: Replace with `podman` commands or use `docker` alias
  4. **Python deployment script (visp.py)** — ✅ DONE
     - Replaced with thin deprecation wrapper; original archived to `ARCHIVE/`
     - All functionality migrated to `visp.py` + `vispctl/`

  5. **Documentation and user messaging**
     - Multiple files reference "docker compose", "Docker images", etc.
     - README.md, docs/*.md contain Docker CLI examples
     - **Action**: Update documentation to reflect Podman usage
     - **Action**: Consider maintaining Docker compatibility notes

  **Migration Steps:**

  - [x] Phase 1: Socket compatibility testing ✅ COMPLETE
    - ✅ Tested on WSL (no systemd required)
    - ✅ Socket started with: `podman system service --time=0 unix://$HOME/.podman/podman.sock &`
    - ✅ node-docker-api 1.1.22 fully compatible with Podman socket
    - ✅ All operations verified: list, create, start, stop, inspect, exec, logs, delete
    - ✅ Test scripts created: [test-podman-socket.sh](test-podman-socket.sh), [test-podman-socket.js](test-podman-socket.js)
    - **Conclusion**: Session-manager code requires ZERO changes for Podman compatibility

  - [x] Phase 2: Enable systemd in WSL ✅ COMPLETE (with caveat)
    - ✅ Added systemd=true to `/etc/wsl.conf`
    - ✅ Restarted WSL - systemd now running as PID 1
    - ✅ Enabled user lingering: `sudo loginctl enable-linger $(whoami)`
    - ✅ Enabled Podman socket: `systemctl --user enable --now podman.socket`
    - ✅ Socket active at: `/run/user/1000/podman/podman.sock`
    - ⚠️  **BLOCKER**: Podman 3.4.4 does NOT support Quadlets (need 4.4+)
    - **Next**: Upgrade Podman to 4.4+ or use alternative approach

  - [x] Phase 3: Upgrade Podman ✅ COMPLETE
    - ✅ Upgraded from 3.4.4 → 4.6.2
    - ✅ Quadlet support verified: `/usr/libexec/podman/quadlet --version`
    - ✅ Test quadlet working: [test-quadlet-simple.container](test-quadlet-simple.container)
    - ✅ systemd integration confirmed
    - ⚠️  **WSL caveat**: Must use `PodmanArgs=--cgroups=disabled` due to cgroup limitations
    - **Note**: Production servers won't need --cgroups=disabled

  - [x] Phase 3b: Create initial Quadlet files ✅ IN PROGRESS
    - ✅ Test quadlet working: [test-quadlet-simple.container](test-quadlet-simple.container)
    - ✅ Created quadlets directory with .container and .network files
    - ✅ All 10 visp images migrated from Docker to Podman
    - ✅ docker-compose stopped
    - ✅ Quadlets created for: mongo, whisper, wsrng-server, session-manager
    - ✅ Networks created: visp-net, whisper-net, octra-net
    - ✅ Podman socket mounted at `/run/user/1000/podman/podman.sock:/var/run/docker.sock:Z`
    - ✅ **ALL CORE SERVICES RUNNING**:
      - ✅ mongo: Running with fresh data directory
      - ✅ whisper: Running successfully
      - ✅ wsrng-server: Running in production mode (no source mount)
      - ✅ session-manager: Running and connected to mongo, socket at :8020
    - **Solution**: Production quadlets without source mounts (use docker-compose for dev)
    - **Next**: Create remaining quadlets (apache, emu-webapp, emu-webapp-server, octra, mongo-express)

  - [x] **Phase 3c: Fix Podman Networking (CNI → Netavark)** ✅ COMPLETE - MIGRATED 2026-01-28
    - **RESOLVED**: Migrated to netavark, DNS now instant (0.02-0.07s vs 20-25s)
    - See: NETAVARK_MIGRATION_SUCCESS.md for full details
    - **What was fixed**:
      - CNI dnsname plugin DNS servers not running → netavark built-in DNS works
      - 20-25 second DNS timeouts → instant resolution
      - 1+ minute login delays → ~1 second
      - Internal=true broke DNS → now works perfectly
    - **Remaining work**:
      - [ ] Update installation guide with netavark requirement
      - [x] Add network creation to visp.py (`./visp.py network ensure`) ✅
      - [ ] Test migration procedure on fresh install
      - [ ] Document in DEPLOYMENT_GUIDE.md
    - **CRITICAL ISSUE DISCOVERED**: CNI dnsname plugin DNS servers not running
      - **Impact**: Every DNS lookup takes 20-25 seconds (timeout)
      - **Symptoms**: 1+ minute login delays, slow page loads, timeouts everywhere
      - **Root Cause**: dnsmasq processes for CNI networks (10.89.1.1, 10.89.0.1) not running
        - Session-manager connects to mongo: 20s delay
        - Session-manager connects to whisper: 25s delay
        - Apache connects to session-manager: 20s delay
      - **Temporary Workaround Applied**: Added /etc/hosts entries to running containers
        - session-manager: `10.89.1.2 mongo`, `10.89.0.2 whisper`
        - apache: `10.89.1.5 session-manager`, `10.89.1.2 mongo`
        - **Result**: Instant DNS resolution (0.07s), app now fast
        - **Limitation**: Workaround lost on container restart, IPs may change
    - **Problem**: `Internal=true` networks disable DNS resolution with CNI backend
      - **whisper-net**: Multi-homed session-manager cannot resolve `whisper` hostname
        - Session-manager connects to both `visp-net` and `whisper-net`
        - CNI backend only adds first network's DNS to `/etc/resolv.conf`
      - **octra-net**: Apache cannot resolve `octra` hostname for proxying
        - Apache needs to proxy requests to `http://octra/`
        - `Internal=true` disables DNS even for single-network containers
    - **Current Workaround**: Removed `Internal=true` from both networks
      - Trade-off: Whisper and Octra are no longer isolated from internet
      - Security risk: containers can make external connections
      - BUT: DNS still broken even without Internal=true!
    - **Proper Fix**: Migrate from CNI to Netavark backend
      ```bash
      # 1. Install netavark
      sudo apt install podman-netavark aardvark-dns

      # 2. Configure in ~/.config/containers/containers.conf
      [network]
      network_backend = "netavark"

      # 3. Reset podman (WARNING: deletes all containers, images, networks)
      podman system reset

      # 4. Rebuild images and recreate networks
      ```
    - **After Migration**: Restore `Internal=true` in whisper-net.network and octra-net.network
    - **Alternative Mitigations** (if netavark not possible):
      - [ ] Firewall rules inside whisper/octra containers (iptables)
      - [ ] Network policy via host nftables
      - [ ] Proxy-only access pattern

  - [x] Phase 3d: Create unified management tool ✅ COMPLETE
    - ✅ Created `visp.py` - Python replacement for visp-logs.sh
    - ✅ **Modularized with vispctl package** (commit 2472bf0, db12f52)
      - Extracted manager classes: ServiceManager, NetworkManager, QuadletManager
      - Created vispctl package with proper structure
      - Moved backup/restore to vispctl.commands
      - Added fix-permissions command
      - All tests passing after refactor
    - **Commands available**:
      - `visp.py status` - Show service status, quadlet links, containers, networks
      - `visp.py logs [service] [-f]` - View/follow logs
      - `visp.py start/stop/restart [service|all]` - Service control
      - `visp.py install/uninstall [service|all]` - Manage quadlet symlinks
      - `visp.py reload` - Reload systemd daemon after quadlet changes
      - `visp.py debug <service>` - Debug startup issues
      - `visp.py shell <container>` - Open bash in container
      - `visp.py exec <container> <cmd>` - Run command in container
      - `visp.py network` - Show network backend and DNS status
      - `visp.py network ensure` - Create missing networks (commit 83b5d6e)
      - `visp.py backup` - Backup MongoDB and external repos
      - `visp.py restore` - Restore from backup
      - `visp.py fix-permissions` - Fix file permissions for mounted volumes
    - [x] TODO: Add `build` command integration with visp.py ✅
    - [x] TODO: Add `update` command (`./visp.py deploy update`) ✅
    - [ ] TODO: Consider adding bash completion
    - [ ] **Optional: Remove networks during `uninstall`** (`--remove-networks`)
      - **Why**: Provide an opt-in full teardown (useful for ephemeral/dev/CI) while avoiding accidental network removal on shared hosts.
      - **Pros**:
        - Clean teardown removes VISP-created networks (good for tests/CI)
        - Convenient for ephemeral environments and full uninstall workflows
      - **Cons**:
        - Risky if networks are shared with other projects or containers
        - Requires careful checks (attached containers) and confirmation to be safe
      - **Design / Implementation Notes**:
        - CLI flag: `--remove-networks` (alias `--prune-networks`)
        - Safety checks:
          - Only target known VISP networks: `systemd-visp-net`, `systemd-whisper-net`, `systemd-octra-net`
          - Inspect networks for attached containers before removing
          - Prompt for confirmation; accept `--force` to skip prompt
          - Abort if attached containers are found unless `--force` and explicit confirmation
        - Documentation: Update `README.md`
      - **Acceptance Criteria / Tests**:
        - Manual tests:
          - `uninstall --remove-networks` removes networks when none attached
          - `uninstall --remove-networks` aborts when containers attached unless `--force`
        - Unit tests: Mock podman calls to verify behavior
        - Help text and `--help` updated

  - [x] Phase 3e: Implement Podman Secrets ✅ COMPLETE
    - ✅ Created `.env.secrets` file for sensitive credentials (separate from `.env`)
    - ✅ Added `.env.secrets.template` for documentation
    - ✅ Updated `.gitignore` to exclude `.env.secrets`
    - ✅ Implemented `load_all_env_vars()` in visp.py to merge both files
    - ✅ Created `get_derived_secrets()` to generate secrets from environment variables
    - ✅ Implemented `create_podman_secrets()` and `remove_podman_secrets()` functions
    - ✅ Updated `cmd_install()` to create Podman secrets automatically
    - ✅ Updated `cmd_uninstall()` to clean up secrets
    - **Secrets managed**:
      - `visp_mongo_root_password` → MONGO_ROOT_PASSWORD
      - `visp_api_access_token` → HS_API_ACCESS_TOKEN
      - `visp_test_user_login_key` → TEST_USER_LOGIN_KEY
      - `visp_mongo_uri` → MONGO_URI (derived secret)
      - `visp_media_file_base_url` → MEDIA_FILE_BASE_URL (derived secret)
    - ✅ All quadlets updated to use `Secret=` directives instead of hardcoded values
    - ✅ Security audit passed: no secrets hardcoded in container files
    - **Benefits**: Secure credential management, no passwords in quadlet files, Git-safe configuration

  - [x] Phase 3f: Quadlet architecture improvements ✅ COMPLETE
    - ✅ Switched from copying to symlinking quadlet files
      - `~/.config/containers/systemd/*.container` → symlinks to source files
      - Single source of truth: edit in `quadlets/dev/` or `quadlets/prod/`
    - ✅ Removed environment variable substitution (no longer needed)
    - ✅ Configuration split:
      - `.env` - Non-sensitive config (BASE_DOMAIN, ADMIN_EMAIL, etc.)
      - `.env.secrets` - Passwords and tokens (managed via Podman Secrets)
    - ✅ Updated visp.py to generate passwords to `.env.secrets`
    - ✅ All containers load config from `.env` via `EnvironmentFile=` directive
    - ✅ Secrets override config values via `Secret=` directives
    - ✅ **Enhanced security loading** (commit bb24a1f)
      - Passwords now loaded from `.env.secrets` instead of `.env`
      - Ensures secrets file is always used for sensitive data
    - **Security**: No container gets passwords it doesn't need

  - [x] Phase 3g: Naming consistency ✅ COMPLETE
    - ✅ Renamed `visp_deploy.py` → `visp.py` (consistent with visp.py)
    - ✅ Used `git mv` to preserve history
    - ✅ Updated all references in documentation and scripts

  - [ ] Phase 4: Update scripts and tooling
    - Replace docker commands in .sh scripts
    - Update visp.py subprocess calls
    - Add Podman detection/validation
    - Test full deployment workflow

  - [ ] Phase 4: Implement Quadlets (Production target)
    - **Prerequisites**:
      - ✅ systemd running (enabled in WSL)
      - ⚠️  **Podman 4.4+ required** (current: 3.4.4)
      - Options to upgrade:
        1. Use Podman PPA: https://github.com/containers/podman/blob/main/install.md
        2. Build from source
        3. Use production server with newer Podman for Quadlet development
    - **Quadlet Implementation**:
      - Convert docker-compose services to .container/.network/.volume files
      - Place in `~/.config/containers/systemd/` (user) or `/etc/containers/systemd/` (system)
      - Podman automatically generates systemd units from quadlets
      - Set up service dependencies with `After=`, `Requires=`, `Wants=`
      - Configure pod networking with .network files
      - Use `systemctl --user daemon-reload` to load changes
      - Test systemd integration: `systemctl --user start visp-session-manager.service`
      - Enable auto-restart: systemd handles this automatically
    - **Benefits over docker-compose**:
      - Native systemd integration (service management, logging, dependencies)
      - Auto-restart on failure
      - Boot-time startup
      - Better resource limits and cgroups integration
      - Standard Linux service management

  - [ ] Phase 5: Documentation and cleanup
    - Update all documentation files
    - Add Podman installation guide
    - Document socket path configuration
    - Remove Docker-specific workarounds
    - Add migration guide for existing deployments

## Medium Priority

### Data Integrity — Bundle-Level Orphan Detection & Quarantine

- [x] **Detect orphaned bundles on project load** ✅
  - **Problem**: The session-level health check catches orphaned `_ses` dirs and ghost DB
    sessions, but misses orphaned **bundles** — `_bndl` dirs on disk within a valid session
    that have no corresponding entry in `Project.sessions[].files[]` in MongoDB. These
    bundles are invisible to the UI but consume disk space and cause file-count mismatches.
  - **Example**: `Session_2_ses/my_recording_bndl/` exists on disk but `my_recording.wav` is
    not in that session's `files` array in MongoDB.
  - **Mapping**: `file.name` (e.g. `my_recording.wav`) → strip extension → `my_recording` →
    append `_bndl` → `my_recording_bndl/` on disk. Spaces are replaced with underscores.
  - **Fix**: Extended `getProjectHealthStatus()` in session-manager to list `_bndl` dirs
    per `_ses` dir and compare against `session.files[]`. Reports orphaned bundles in
    `health.orphanedBundles[]` and adds a human-readable issue string.
  - **Committed**: session-manager (not yet pushed)

- [ ] **Quarantine orphaned bundles via explicit user action** 🔄
  - **Goal**: Let users move orphaned `_bndl` dirs to a quarantine folder instead of
    deleting them, preserving data until the admin confirms it's safe to remove.
  - **Proposed approach**:
    1. Add `quarantineOrphanedBundles(projectId)` in session-manager (similar pattern
       to `cleanupOrphanedSessions`)
    2. Move orphaned `_bndl` dirs to `VISP_emuDB/_quarantine/{Session}_ses/{Bundle}_bndl/`
    3. Also remove matching entries from `BundleList.bundles[]` if present
    4. Triggered by user click on warning badge — **not** automatically on project load
    5. Webclient: add a distinct action button or extend the existing cleanup dialog
  - **Why not auto-move on load**: Risk of false positives if detection logic has a bug;
    conflates read path with write path; user would not know files were moved.
  - **Three-way sync note**: Detection currently covers disk ↔ MongoDB. The third layer
    (BundleList in MongoDB + `bundleLists/*.json` on disk + `VISP_DBconfig.json`) is a
    deeper consistency problem — a bundle could be on disk AND in `sessions[].files[]`
    but missing from `BundleList.bundles[]`, making it invisible to EMU-webApp. This
    could be tackled as a separate "full consistency audit" feature.
  - **Depends on**: bundle-level detection (done above)

### Code Organization
- [x] **Consolidate duplicate configuration** ✅ COMPLETE
  - ✅ Split `.env` into non-sensitive config and `.env.secrets` for passwords
  - ✅ Removed all password variables from `.env`
  - ✅ visp.py now generates passwords to `.env.secrets` only
  - ✅ Clean separation: configuration vs credentials

- [x] **Create .env.example template** ✅ COMPLETE
  - ✅ Created `.env.secrets.template` with all password variables documented
  - ✅ visp.py copies template to `.env.secrets` if not exists
  - ✅ All sensitive variables documented with descriptions

### Documentation
- [ ] **Document Apache vhost configuration**
  - Current issue: `ServerName https://${BASE_DOMAIN}:443` was invalid syntax
  - Fixed but should document the proper format and what variables are available

### Testing & Validation
- [ ] **Add automated deployment tests**
  - Test that all services start successfully
  - Test that authentication works (both Shibboleth and test user)
  - Test that APIs are accessible
  - Could integrate with `visp.py deploy status` command

## Low Priority

### Repair Session / Re-import Bundle
- [ ] **Add `repair-session` command to re-run emuDB import for a single session**
  - **Problem**: When a bundle's audio file goes missing from disk but the session still exists
    in MongoDB, there is no way to re-run the emuDB import without deleting and re-creating the
    session. The R/emuR import pipeline (`import_mediaFiles`, `_annot.json` generation) only
    runs during initial project creation inside a `visp-operations-session` container.
  - **Current workaround**: User must delete the broken session in the web UI, re-upload the
    audio file as a new session, and re-create. This loses the session name, any annotation
    progress, and transcription results.
  - **Desired feature**: `./visp.py repair-session <project_id> <session_name>` that:
    - Accepts a pre-placed `.wav` file in the session's bundle directory
    - Spawns a `visp-operations-session` container for just that session
    - Re-runs emuDB import (creates `_bndl/`, `_annot.json`, updates `bundleLists/`)
    - Preserves existing MongoDB session metadata
  - **Complexity**: Moderate — needs to replicate part of the `saveProjectEmuDb()` pipeline
    from session-manager, but for a single session rather than the full project
  - **Detection**: `./visp.py doctor` already flags this as
    `"Bundle '...' in session '...' in MongoDB but missing on disk"`

### Session Recovery
- [ ] **Investigate crash recovery for running sessions**
  - **Current State**: If session-manager crashes, running Jupyter/RStudio containers are orphaned with no way to reconnect
  - **Desired Feature**: Detect and import running session containers after session-manager restart
  - **Challenges**:
    - Need to reconstruct session state from container inspection
    - Session metadata (user, timestamps, URLs) stored only in memory/MongoDB
    - Container labels or naming conventions could help identify orphaned sessions
    - Would need to rebuild session-manager's internal state from discovered containers
  - **Potential Approach**:
    - Label all session containers with user ID, session type, creation timestamp
    - On startup, query Podman for containers matching session labels
    - Query MongoDB for corresponding session records
    - Reconstruct session objects and re-establish proxying
  - **Priority**: Low - session-manager crashes are rare, manual cleanup currently works

## Notes

### Git History
All completed tasks are documented in git commits. Use `git log` to review implementation details.

### Git Stashes
- "WIP: Matomo tracking implementation" - Contains tracker files and Docker mounts
- "WIP: Docker compose changes for Matomo tracker mounts" - Volume mount configs
