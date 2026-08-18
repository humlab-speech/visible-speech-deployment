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

## CLI Fixes — visp.py evaluation (2026-08-17/18)

Full evaluation of `visp.py`: every command group was hands-on tested on a live dev
deployment (including the full `uninstall → install → reload → start all` cycle, a real
build, backup+restore, and a users round-trip) plus code review by 5 parallel
subagents. The core lifecycle, build, backup/restore, users, doctor, and deploy status
all fundamentally work and do what they claim. This section lists the confirmed
defects, ordered for fixing. Branch: `fix/visp-cli-evaluation` (from master @ 13f267b).

**How to work this list:**

- Work **top to bottom**, one item per commit (conventional-commit style).
- Run `pre-commit run --all-files` before each commit.
- Items marked ⛔ **DECISION** must **NOT** be started until the decision is made and
  recorded next to the item. They are listed first so they stay visible, but they are
  not part of the ordered work — skip them and continue with Phase 1.
- "Verify:" lines are the minimum acceptance check for the item.
- Tick the box only after the fix is committed and verified.

### ⛔ Decisions needed — do NOT start until decided

- [ ] **D1 — `status` vs `deploy status`: naming, scope & visual language**
  (supersedes the older "Merge `status` and `deploy status`" item further down)
  **DECIDED 2026-08-18:** No rename, no merge, no `health` command. Keep `status`
  (runtime) and `deploy status` (source/build drift) as separate commands. Restyle
  `status` to the tabulate-grid + symbol look used by `deploy status` so both share
  one visual dialect. Container list filtered to VISP-only, with `--all` to show all
  host containers (sub-decision a). Keep the `debug` alias (sub-decision b).
  - They check orthogonal things (runtime state vs source/build drift) and use two
    incompatible visual dialects (`=== Cyan ===` + ●○✓! symbols vs emoji + tabulate
    grid tables; `deploy.py` is the only emoji module in the codebase).
  - Options:
    - A: rename `deploy status` → `deploy drift` (or `deploy check`), keep `status`
      for runtime, restyle `deploy status` to the house `=== ===` format
    - B: add a composed `visp.py health` (runtime + drift + stale images, `--strict`
      exit code) as the single "is my deployment healthy?" entry point, keep both
      existing commands
    - C: merge per the older item's options A/B/C
  - Sub-decisions: (a) should `status`'s container list be filtered to VISP
    containers only (it currently dumps every host container)? Recommend yes, or
    add `--all`. (b) keep or remove the `debug` alias (it is a pure wrapper of
    `logs --debug`)?
- [ ] **D2 — `logs <single-service>` follows by default**
  **DECIDED 2026-08-18:** Keep follow-by-default for single-service logs; document it
  more prominently (help text + README). `logs all` stays non-follow.
  - Documented in the `--no-follow` help, but surprising; `logs all` does not follow.
  - Decide: keep the documented behavior (and document it harder) or make all log
    viewing non-follow by default.
- [ ] **D3 — `fix-permissions --path` default ownership**
  **DECIDED 2026-08-18:** Change the default to host-owner — i.e. chown to the host
  user who owns the VISP root folder (the deploying user; a service user in prod).
  This is the current `--host-owner` behavior, now made the default.
  - The default chowns to the host uid *interpreted inside* the unshare namespace
    (→ container-owned on this host); `--host-owner` is the intuitive behavior but
    is not documented as the one you usually want.
  - Decide: change the default to host-owner, or document the namespace mapping
    in the help text.
- [ ] **D4 — `restore` semantics**
  **DECIDED 2026-08-18:** Require an explicit `--drop` flag (default: no drop). Add
  stop-services guidance to the help text and strengthen the confirmation prompt.
  - Runs `mongorestore --drop` unconditionally; the help text and prompt never
    mention `--drop` or that services should be stopped first.
  - Decide: require an explicit `--drop` flag (default: no drop), add stop-services
    guidance, strengthen the confirmation prompt.
- [ ] **D5 — `deploy update` and pinned versions**
  **DECIDED 2026-08-18:** "Track latest branch" is the intended semantics for
  `update`; document it. Pinned checkout is `deploy rollback`'s job (item 5).
  Doc note: "update tracks the latest branch for unlocked components; pin with
  `deploy lock`, check out a pinned version with `deploy rollback`."
  - `update` only `git pull`s unlocked repos; a pinned `version` (commit SHA/tag) in
    versions.json is never checked out by any command.
  - Decide: should `update` check out the configured version, or is "track latest
    branch" the intended semantics (in which case document it)?
  - Related: item 5 (rollback) implements the checkout that `update` currently lacks.
- [ ] **D6 — Exit-code policy for `status` / `images`**
  **DECIDED 2026-08-18:** Add a `--strict` flag to `status` and `images`: non-zero
  exit on any health problem (down service / stale image). Default stays exit 0.
  - Everything exits 0 regardless of health (only `deploy status --strict` can fail).
  - Decide: add `--strict` to `status`/`images`? Non-zero on any down service?

### Phase 1 — Safety-critical (wrong-target destruction / data loss)

- [x] **1. `session-doctor` false-positive orphan proxies (destructive)**
  - `vispctl/session_doctor.py:111-131` — proxy discovery uses substring
    `name=-proxy` + `endswith("-proxy")`, so it flags the core VISP service
    `podman-socket-proxy` *and* non-VISP host containers (e.g. `kiwix-proxy`) as
    orphaned sidecars; `--clean -y` would `podman rm -f` them.
  - Fix: only treat a container as a session sidecar if its name matches
    `visp-session-*-proxy` / `hsapp-session-*-proxy` (or it carries a
    `visp.proxyFor` label). Never put other `-proxy` containers in the cleanup plan.
  - Verify: `./visp.py session-doctor --problems` no longer lists
    `podman-socket-proxy`; the cleanup plan contains only real session sidecars.
- [x] **2. `uninstall <service>` removes ALL podman secrets**
  - `visp.py:283-291` — `list_secrets()` returns every `visp_*` secret and all are
    removed even for `uninstall mongo`, breaking other services' secret injection
    until the next install.
  - Fix: scope secret removal to the requested service(s) (map service → its
    secrets); `uninstall all` keeps the current behavior.
  - Verify: `podman secret ls` before/after `./visp.py uninstall mongo
    --keep-running` — only mongo's secrets are gone.
- [ ] **3. `restore` can restore from a stale extracted directory**
  - `vispctl/backup.py:250-273` — `find /tmp -maxdepth 1 -name "visp_mongodb_*"` +
    `splitlines()[0]` picks an arbitrary match if a previously interrupted restore
    left a dir in the container's /tmp.
  - Fix: derive the expected top-level dir from the tarball name (deterministic),
    verify it exists, and clean up leftover `visp_mongodb_*` dirs in /tmp.
  - Verify: pre-create a decoy dir in the mongo container's /tmp, restore a real
    backup, confirm the correct dir is used.
- [ ] **4. `backup`/`restore` crash with raw tracebacks on failure**
  - `vispctl/backup.py` — `Runner.run` defaults to `check=True`, so a failing
    mongodump/tar/podman-cp/mongorestore raises `CalledProcessError` instead of the
    friendly "✗ Backup failed" / "✗ Restore failed" path (dead code today).
  - Fix: `check=False` + explicit returncode handling at each step.
  - Verify: a missing backup file and a corrupt archive both print the friendly
    error and exit 1, no traceback. Extend `tests/vispctl/test_backup.py` (currently
    mocks success only) to cover failure paths.

### Phase 2 — Broken features (silent no-ops / dead code)

- [ ] **5. `deploy rollback` never checks out the locked version**
  - `vispctl/deploy.py:936-990` — only rewrites versions.json
    (`version := locked_version`), then instructs `deploy update` — which *skips
    locked components* (`deploy.py:1042-1046`). `GitRepository.checkout`
    (`vispctl/git_repo.py:81`) is never called from anywhere in deploy.py. Rollback
    is a working-tree no-op.
  - Fix: after updating versions.json, check out `locked_version` in each
    component's repo (fetch first if the SHA is not local). Coordinate with D5.
  - Verify: lock webclient, move `external/webclient` to another commit,
    `./visp.py deploy rollback webclient` → repo is back at the locked SHA.
- [ ] **6. `apply` stale-image detection is dead (wrong container name)**
  - `vispctl/images.py:122` — inspects `systemd-<name>`, but quadlet containers are
    named `<name>` (only networks get the `systemd-` prefix). Always returns `[]`,
    so `apply` prints "all containers are running the latest images" while
    `deploy status` shows STALE images, and never restarts stale containers.
  - Fix: drop the `systemd-` prefix.
  - Verify: rebuild a quadlet service image (e.g. `./visp.py build wsrng-server`),
    then `./visp.py apply wsrng-server` → detects the stale container and restarts it.
- [ ] **7. `images` "Container Network Connections" always empty (same prefix bug)**
  - `vispctl/images.py:96` — filters `podman ps` names by `startswith("systemd-")`,
    which matches no quadlet container.
  - Fix: iterate the known VISP container service names instead. (May be superseded
    by D1 if this section moves to `network`.)
- [ ] **8. `debug` / `logs --debug`: "Service Status:" section always empty**
  - `vispctl/logs.py:143-144` + `vispctl/runner.py:47-48` — `systemctl status`
    output is captured (`capture=True`) and the `CompletedProcess` is discarded.
  - Fix: print the captured stdout+stderr under the label.
  - Verify: `./visp.py debug mongo` shows unit state, main PID, and recent journal.
- [ ] **9. `stop all` / `uninstall` / `restart <network>`: bogus `.service` errors**
  - `vispctl/service_manager.py:18-19` always appends `.service`; network units are
    `<name>-network.service` and don't need stopping anyway. `stop all` and
    `uninstall` print two guaranteed "Unit … not loaded" errors; `start <network>`
    silently no-ops; `restart <network>` errors — inconsistent across the five
    lifecycle commands.
  - Fix: skip `type == "network"` services in all start/stop paths (networks come
    up via `Requires=` from the containers) and make the commands consistent.
  - Verify: `./visp.py stop all` and `./visp.py uninstall --keep-running` produce
    no red errors.
- [ ] **10. `exec`/`shell` swallow failures (always exit 0)**
  - `visp.py:368,374` — `check=False` discards podman's exit code; no validation
    that the container name is a known VISP service either.
  - Fix: propagate the exit code; warn (not hard-fail) for unknown container names.
  - Verify: `./visp.py exec nosuch echo hi; echo $?` → non-zero exit.

### Phase 3 — UX & consistency

- [ ] **11. Piped output reordering (systemic)**
  - `vispctl/runner.py:36-40` — non-captured subprocesses inherit the pipe while
    Python `print` is block-buffered, so raw output appears *before* the headers
    when piped (affects `status`, `images`, `logs`, `debug`, `backup`).
  - Fix: `sys.stdout.flush()` before non-captured `subprocess.run` (or capture and
    echo).
  - Verify: `./visp.py status | head -5` starts with the cyan header, not raw
    `podman ps` output.
- [ ] **12. `status`: duplicate header + stale "(PoC)" label**
  - `visp.py:129` + `vispctl/service_manager.py:112` — two near-identical headers;
    the first has no body; "(PoC)" is a leftover from the quadlet-migration PoC.
  - Fix: keep one header. Update `tests/vispctl/test_service_manager.py`, which
    asserts the "(PoC)" string.
- [ ] **13. `uninstall` leaves `90-visp-autostart.conf` drop-ins behind**
  - `visp.py:272-279` — after uninstall→install→reload, a previously `down`ed
    service stays disabled across reboot until `visp.py up` is run.
  - Fix: remove the drop-in (and its empty dir) for each uninstalled service.
- [ ] **14. `install --mode prod` leaves dev-only quadlets installed**
  - `vispctl/install.py:554-573` — cleanup only covers `.env`-disabled optionals,
    not `dev_only` services (`mongo-express`, `local-idp`) on a mode switch.
  - Fix: also remove dev-only units when installing in prod mode.
- [ ] **15. `cleanup-containers`: blind confirm + uncaught EOFError**
  - `vispctl/cleanup_containers.py:46` — the prompt doesn't list which containers
    will be removed; without `-y` in a non-interactive context `input()` raises an
    uncaught `EOFError` (only OSError/RuntimeError/ValueError are caught,
    `visp.py:391`).
  - Fix: itemize the matched containers in the prompt (like session-doctor's plan);
    catch EOFError → abort cleanly.
- [ ] **16. Mongo password on the process command line (`users`, `doctor`)**
  - `vispctl/mongo.py:60-78` — passes `-p <pw>` to `mongosh` (visible in `ps`),
    while `backup.py:44-49` deliberately uses a 0600 `--config` file for the same
    credential.
  - Fix: reuse the backup config-file pattern in `vispctl/mongo.py`.
- [ ] **17. `deploy status` summary drops non-STALE problem rows**
  - `vispctl/deploy.py:688` — the recommended-actions filter matches only
    `startswith("⚠ STALE")`, so `DIRTY BUILD` / `NO LABEL` / `NO TIMESTAMP` rows are
    visible in the table but never summarized.
- [ ] **18. `deploy status`: no notice when versions.json is missing**
  - Everything silently shows "UNLOCKED (tracking latest)".
  - Fix: print a one-line note when the lock file is absent.
- [ ] **19. `build --config` silently ignored for non-webclient builds**
  - `vispctl/build.py:349-354` — warn (or error) when `--config` is given for a
    target whose build template has no `{config}` placeholder.
- [ ] **20. `up` "Already enabled" false positive**
  - `vispctl/service_manager.py:62-70` — enabled is inferred from drop-in absence
    only; a manual `systemctl --user disable` would be misreported as enabled.
  - Fix: cross-check with `systemctl --user is-enabled`.
- [ ] **21. `doctor` nits**
  - `--only` doesn't strip whitespace around IDs (`visp.py:576`).
  - `--apply` without `--fix` is a silent no-op (`vispctl/doctor.py:544`) → warn.
  - Unguarded `iterdir()`/`stat()` can traceback on a permission-denied project
    dir (`vispctl/doctor.py:61-90`) → report as an issue instead.

### Phase 4 — Docs & polish

- [ ] **22. Stale "link" wording for install/uninstall**
  - Help says "Link quadlet files to systemd" / "Overwrite existing links" /
    "Remove quadlet links", but the code renders templates and copies files
    (`vispctl/install.py:541-545`). The `=== Quadlet Links ===` table and its dead
    symlink branch (`vispctl/status.py:35-49`) are the same stale vocabulary.
  - Fix: reword help + table (e.g. "Quadlet Units" / "installed"); drop or fix the
    symlink branch.
- [ ] **23. `build --list` output inconsistencies**
  - Prints `visp-*:latest` without the `localhost/` prefix that the quadlets and
    AGENTS.md mandate (`vispctl/build.py:768`).
  - "Available configs" omits the default `visp.dev` and the whitelist's
    `production`/`development` (`:789` vs `:20-29`).
- [ ] **24. Stale docs**
  - AGENTS.md still references `visp-users.py` (repo layout + user-management
    section) although the file no longer exists — the tool is `./visp.py users`.
  - `visp.py` module docstring (`visp.py:5-22`) omits users, doctor,
    session-doctor, fix-permissions, images, cleanup-containers, and deploy.
  - Top-level `--help` epilog examples still say `visp-ctl` (`visp.py:648-663`).
- [ ] **25. Nits**
  - `images`: headerless "Backend:" line floating between sections
    (`vispctl/images.py:248-258`).
  - `images base`: multi-stage Dockerfiles listed once per FROM with no stage
    label; pinning heuristic counts any digit-containing tag as pinned (`:307`).
  - `mode`: `dev` rendered in the warning color, `prod` green (`visp.py:353`).
  - `vispctl/build.py:263` redundant `import subprocess`; `:160` uses
    `Path.cwd()` where `DeployManager` uses `basedir`.
  - `BackupManager.list_backups` has no CLI exposure (`vispctl/backup.py:33-37`).

## High Priority

- [x] **Verify uploads work end-to-end in Podman** — confirmed working 2026-05-13

## Medium Priority

### Security

- [ ] **Review 777 permissions on container-writable bind mounts**
  - `visp.py install` sets `chmod 777` on uploads, repositories, log directories
  - Consider `podman unshare chown`, `--userns=keep-id`, or ACLs for tighter permissions
  - Low risk on single-user server, problematic on shared systems

- [x] **Custom body-inspecting socket proxy for session-manager**
  - Implemented in `docker/podman-socket-proxy/` — pure Node.js, zero npm deps
  - Deployed as `localhost/visp-podman-socket-proxy:latest` via `podman-socket-proxy.container` quadlet
  - session-manager now mounts `mounts/podman-proxy/podman.sock` instead of the real socket
  - Enforces on every `POST .../containers/create`: image allowlist (`localhost/visp-*`), mount path allowlist (under `ABS_ROOT_PATH`), `Privileged=false`, capability allowlist, no host netns/pid/ipc
  - All other calls (list, inspect, exec, start, stop) pass through transparently
  - Build: `./visp.py build podman-socket-proxy`

### Build & Images

- [x] **Audit Dockerfiles for version consistency**
  - `docker/octra/Dockerfile`: pinned to commit hash ✅
  - `docker/whisper/Dockerfile`: pinned to commit hash, marked NOT USED ✅
  - `docker/session-manager/build-context/Dockerfile`: marked NOT USED ✅
  - External repo Dockerfiles (floating `git clone`) tracked as upstream PRs needed

### CLI / Operations UX

- [ ] **Merge `status` and `deploy status` into one coherent picture**
  - ⛔ Now tracked as **D1** in the "CLI Fixes — visp.py evaluation (2026-08-17/18)"
    section above (which extends this with the visual-language split and the
    container-list scoping sub-decision). Do not start until D1 is decided.
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
