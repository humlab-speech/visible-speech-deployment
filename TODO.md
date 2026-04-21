# VISP Deployment TODO

> Completed items have been removed — see `git log` for implementation details.
> Key completions: Podman quadlet migration (phases 1–3g), WhisperVault integration,
> netavark migration, Matomo analytics (ad-blocker-safe proxy rename), Angular 18→20
> upgrade, Podman secrets, orphaned bundle detection, version drift tracking, disk leak
> fix, path traversal protection (session-manager), SWAMID certificate deployment +
> login verified, `deploy status --strict`, upload permission fixes, whisperx removed
> from Jupyter image (9.3 GB), RStudio + VSCode session types dropped, notebook
> transcription via `api.sock` + `visp_transcribe.py` + `Transcription.ipynb`, Jupyter
> UDS network isolation (`--network=none`), operations sessions network-isolated.

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

- [ ] **Custom body-inspecting socket proxy for session-manager**
  - session-manager has full Podman API access via socket mount; Traefik does NOT
    actually use the socket (static config only) — its socket mount can simply be removed
  - A simple body-inspecting proxy (~80 lines Python/Node) on the socket could enforce:
    - `Image` must match `^localhost/visp-` — blocks spawning attacker-controlled images
    - `HostConfig.Binds` paths must be under the project root — blocks mounting `/etc`, `~/.ssh`
    - `HostConfig.Privileged` must be `false` — blocks container escape
    - `HostConfig.NetworkMode` must be the sessions network — blocks joining `visp-net`
    - `HostConfig.CapAdd` allowlist — no `SYS_ADMIN` etc.
  - tecnativa/docker-socket-proxy is NOT sufficient — it works at HTTP path level only,
    cannot inspect the request body to check which image or mounts are being requested
  - **Honest usefulness assessment:** this is a *third* layer of defence, only meaningful
    after network isolation is done. The attack chain requiring it is: (1) remotely-exploitable
    RCE CVE in session-manager's Node.js deps, AND (2) attacker knows VISP architecture well
    enough to abuse the socket. Low probability for an academic platform. Worth doing as a
    tidy follow-up to network isolation — small effort, genuine depth-of-defence value — but
    not worth prioritising over the network isolation work itself.

- [x] **Network-isolate operations session containers** — `--network=none` added to
  `OperationsSession.getContainerConfig()`. Operations sessions only run container-agent
  R/Node scripts via `podman exec` on bind-mounted project dirs — no network needed.

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

### Git / Release

- [ ] **Merge `feature/podman-migration` into `master`**

  `pre-podman-migration` tag already pushed to origin on all repos.

  ```
  git checkout master
  git merge --no-ff feature/podman-migration -m "Merge podman migration into master"
  git push origin master
  ```

  Use `--no-ff` so the merge commit gives a clean rollback point.

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
  - Orphaned Jupyter containers after session-manager crash
  - Label containers with metadata, reconstruct state on restart

- [x] **Session doctor: account for `api.sock` in socket dir checks**
  - `api.sock` now tracked in `_collect_socket_dirs()`, warning issued if missing on a
    running Jupyter session, and displayed in the socket dir line alongside ui.sock/proxy.sock

- [ ] **`visp_transcribe`: reconnect / retry on api.sock connection failure**
  - If session-manager is restarted while a Jupyter session is running, the
    `api.sock` is recreated but the notebook kernel still has the old client
  - Could implement transparent retry in `_client()`: attempt the call, and if
    connection is refused try once more after a short delay (the socket may be
    transiently unavailable during session-manager restart)

- [ ] **Notebook transcription can starve the UI queue (low risk, worth capping)**
  - Both paths share a single boolean `transcriptionRunning` mutex in WhisperService
  - A notebook loop transcribing many files back-to-back holds the mutex continuously;
    the UI queue's 15 s polling interval finds `transcriptionRunning = true` every
    cycle and silently skips — UI transcriptions could be blocked for hours
  - Risk is low on a single-user academic platform (WhisperVault is already CPU-bounded
    at 4/16 cores) but worth a cheap safety net
  - **Recommended fix:** add a global pending notebook request counter in `WhisperService`;
    reject new `transcribeFile()` calls above ~3 with a clear error message
  - **Not recommended:** routing notebooks through the MongoDB queue — notebooks need
    a synchronous return value; polling for completion would complicate `visp_transcribe.py`
  - See `dev-notes/TRANSCRIPTION_QUEUE_ARCH.md` for full architecture and option analysis


- [ ] **MAYBE: Reconsider dev mode build strategy for webclient**
  - Current: external build via `visp.py build webclient`, mount `dist/`
  - Alternative: restore `ng build --watch` for hot-reload (had permission issues)

## Notes

- **Sass deprecation warnings**: font-awesome `@import` and `lighten()` — cosmetic,
  will become errors in Dart Sass 3.0 (future cleanup)
