# VISP Deployment TODO

> Completed items have been removed — see `git log` for implementation details.
> Key completions: Podman quadlet migration (phases 1–3g), WhisperVault integration,
> netavark migration, Matomo analytics (ad-blocker-safe proxy rename), Angular 18→20
> upgrade, Podman secrets, orphaned bundle detection, version drift tracking, disk leak
> fix, path traversal protection (session-manager), SWAMID certificate deployment +
> login verified, `deploy status --strict`, upload permission fixes, whisperx removed
> from Jupyter image (9.3 GB), RStudio + VSCode session types dropped, notebook
> transcription via `api.sock` + `visp_transcribe.py` + `Transcription.ipynb`, Jupyter
> UDS network isolation (`--network=none`), operations sessions network-isolated,
> session doctor `api.sock` tracking, octra upgraded to humlab-2.2.2.

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

### Build & Images

- [x] **Audit Dockerfiles for version consistency**
  - `docker/octra/Dockerfile`: pinned to commit hash ✅
  - `docker/whisper/Dockerfile`: pinned to commit hash, marked NOT USED ✅
  - `docker/session-manager/build-context/Dockerfile`: marked NOT USED ✅
  - External repo Dockerfiles (floating `git clone`) tracked as upstream PRs needed

### CLI / Operations UX

- [ ] **Simplify the update-and-apply workflow in `visp.py`**
  - Current problem: applying a quadlet change requires knowing 3–4 internal steps
    (`install --force` → `reload` → `restart <svc>`), and applying an image rebuild
    requires a different set (`build` → `restart`). The user's actual intent is almost
    always just "bring everything up to the latest state".
  - Proposal: add a high-level `apply [<service>|all]` command that does
    install-if-needed + reload + restart in one step; for images, `build --restart`.
    Keep the primitives (`install`, `reload`, `restart`) for debugging but de-emphasise
    them in docs/help text.
  - Also consider making `install` automatically run `reload` at the end — it already
    prints "Run reload to apply", it could just do it.

- [ ] **Merge `status` and `deploy status` into one coherent picture**
  - `status` covers runtime health (services running, quadlet drift)
  - `deploy status` covers source freshness (image built from current commit,
    repos ahead/behind remote)
  - Neither alone answers "is everything up to date and running?"
  - Option A: add a fast image-staleness summary section to `status` (no remote fetch)
  - Option B: add `--full` flag to `status` that also runs `deploy status`
  - Option C: rename `deploy status` to `audit` and document the two as complementary

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

- [x] **Document Apache vhost configuration** — added to AGENTS.md: two-directory layout, prod vs dev routing, graceful reload, ServerName syntax, and the "must update both" warning

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
