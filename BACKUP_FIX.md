# Backup System Fix Plan

## Problem Statement

The current backup system (`./visp.py backup` / `./visp.py restore`) only backs up
MongoDB via `mongodump`. It leaves the following unprotected:

- **1.8G of user project data** (`mounts/repositories/`) — the primary data asset
- **`.env.secrets`** — without this, you cannot authenticate to restore the mongodump
- **`.env`** — BASE_DOMAIN and feature toggles needed to regenerate all derived configs
- **Matomo database** (`mounts/matomo-db/mysql/`) — separate MariaDB, completely ignored
- **Matomo configuration** (`mounts/matomo/config/`) — salt, trusted hosts, plugin state
- **Queued transcription audio** (`mounts/transcription-queued/`) — in-progress work
- **In-flight uploads** (`mounts/apache/apache/uploads/`) — user-uploaded content
- **Container images** — if host storage is lost, rebuilding all images takes 20-40 minutes
- **Production Shibboleth/SWAMID config** — without it, SSO authentication fails on prod
- **Let's Encrypt certificates** — without them, prod HTTPS is broken until re-issue

The restore command is additionally unsafe: `mongorestore --drop` destroys the current
database with no pre-restore safety net, no version compatibility check, and no service
stop/start orchestration.

---

## Current Architecture

### What `visp.py backup` Does

File: `vispctl/backup.py` — `BackupManager` class

1. Extracts `MONGO_ROOT_PASSWORD` from `.env.secrets` via `SecretManager`
2. Detects MongoDB version inside the container
3. Runs `mongodump` inside the `mongo` container to `/tmp/`
4. Compresses to `.tar.gz` inside the container
5. Copies archive to host via `podman cp`
6. Cleans up temp files

Output filename: `visp_mongodb_{version}_{timestamp}.tar.gz`

### What `visp.py restore` Does

1. Copies archive into mongo container
2. Extracts to `/tmp/`
3. Runs `mongorestore --drop` (destroys all existing collections first)
4. Cleans up temp files

### Third-Party Images (pulled, not built)

| Service | Image | Size |
|---------|-------|------|
| mongo | `docker.io/library/mongo:6.0.27` | 778 MB |
| matomo | `docker.io/library/matomo:5.8.0` | 618 MB |
| matomo-db | `docker.io/library/mariadb:10.11.16` | 337 MB |
| local-idp | `docker.io/cirrusid/simplesamlphp:v2.4.2` | 779 MB |
| mongo-express | `docker.io/library/mongo-express` | 199 MB |

**Total third-party: ~2.7 GB** — freely pullable, never back up.

### VISP-Built Images (from Dockerfiles)

| Service | Image | Size | Rebuild Time |
|---------|-------|------|--------------|
| jupyter-session | `localhost/visp-jupyter-session:latest` | **9.32 GB** | 20-30 min |
| whisperx | `localhost/visp-whisperx:latest` | **8.66 GB** | 15-25 min |
| session-manager | `localhost/visp-session-manager:latest` | **2.19 GB** | 5-10 min |
| apache | `localhost/visp-apache:latest` | 799 MB | 3-5 min |
| emu-webapp-server | `localhost/visp-emu-webapp-server:latest` | 393 MB | 3-5 min |
| wsrng-server | `localhost/visp-wsrng-server:latest` | 214 MB | 2-3 min |
| octra | `localhost/visp-octra:latest` | 185 MB | 2-3 min |
| podman-socket-proxy | `localhost/visp-podman-socket-proxy:latest` | 165 MB | 1-2 min |
| artic | `localhost/visp-artic:latest` | 82 MB | 1-2 min |
| session-proxy | `localhost/visp-session-proxy:latest` | 12 MB | <1 min |

**Total VISP: ~22.7 GB** (~12 GB compressed with gzip). Legacy images
(`visp-rstudio-session` 13.6 GB, `visp-operations-session` 6.57 GB, `visp-emu-webapp`
82 MB, `visp-arctic` 82 MB) should be pruned — they are not in `BUILD_CONFIGS`.

---

## Design Decisions

### D1: Split Archives, Not Monolithic Tarball

A single tar.gz containing 1.8G of repositories + 201M Matomo DB + MongoDB dump has
severe problems: no resumability, no partial extraction, memory pressure during
compression, and must fully decompress to verify deep entries.

**Chosen approach:** Each backup target is a separate compressed file within a
timestamped directory. The outer directory can optionally be wrapped in a single
tar.gz for transport.

```
visp_backup_20260708_120000/
├── metadata.json          # manifest with checksums
├── mongodb.tar.gz         # ~1M
├── env.tar.gz             # ~4K
├── repositories.tar.gz    # ~1.8G (gzipped)
├── matomo-db.tar.gz       # ~201M (gzipped)
├── matomo-config.tar.gz   # ~4K
├── transcription-queued.tar.gz  # ~46M
├── uploads.tar.gz         # ~556K
├── swamid-config.tar.gz   # ~4K (prod only)
└── certs.tar.gz           # ~48K
```

This allows independent backup/restore of each target, resumability, and selective
extraction. If one target fails, the others are already saved.

**Single-archive mode:** `--single-archive` wraps the directory into one tar.gz
for easy transport to offsite storage.

### D2: Matomo DB via mysqldump, Not File Copy

Copying `mounts/matomo-db/mysql/` (MariaDB data directory) as raw files is unsafe:
InnoDB buffer pool may have unflushed data, and raw data files are not portable
across MariaDB versions.

**Chosen approach:** Use `podman exec matomo-db mysqldump` for backup and
`podman exec matomo-db mysql` for restore. Credentials come from `.env.secrets`
(`MATOMO_DB_ROOT_PASSWORD`, `MATOMO_DB_USER`, `MATOMO_DB_PASSWORD` via `secrets.py:38-44`).

### D3: Class Split — No God Class

The current `BackupManager` handles MongoDB dump/restore. Expanding it to also handle
file archival, service orchestration, manifest management, and image save/load would
create an unmaintainable god class.

**Chosen approach:**

| Class | File | Responsibility |
|-------|------|---------------|
| `BackupManager` | `vispctl/backup.py` | MongoDB dump/restore only (keep existing) |
| `ArchiveManager` | `vispctl/archive.py` (new) | tar.gz creation/extraction, manifest handling |
| `RestoreOrchestrator` | `vispctl/restore.py` (new) | Service lifecycle + restore sequencing |
| `ImageBackupManager` | `vispctl/images.py` (extend) | Image save/load/verify |
| `BackupManifest` | `vispctl/manifest.py` (new) | Manifest creation, validation, checksums |

### D4: Checksums Only for Small Files

Computing SHA-256 for 1.8G of repositories adds 30-60 seconds to every backup.

**Chosen approach:** Compute checksums only for files under 100M (MongoDB dump, env
files, configs). For large targets (repositories, Matomo DB), record file count and
total size in the manifest. Full checksums available via `backup verify`.

### D5: Image Backup — Compressed, VISP-Only, Separate from Data

- **Compression:** Always use `.tar.gz` — 40-60% size reduction vs plain tar
- **VISP images only:** Third-party images are freely pullable; `--third-party` flag removed
- **Enumerated from `BUILD_CONFIGS`:** Don't glob `localhost/visp-*` (catches legacy images)
- **Separate from data backup:** Images change infrequently; default excludes them
- **Air-gapped recovery:** Out of scope — requires local mirror, not tar files

---

## Fix 1: Expand Backup Scope

### 1.1 Split Archive Layout

See Design Decision D1. Each target is a separate `.tar.gz` within a timestamped
directory. The manifest (`metadata.json`) sits at the top level.

### 1.2 Backup Targets — Full Inventory

**Dev vs Prod note:** Several targets differ between deployment modes. The backup
system should detect the current mode (`./visp.py mode`) and include the appropriate
targets. Prod-specific targets are marked below.

**CRITICAL (must include):**

| Target | Path | Quadlet Source | Size | Notes |
|--------|------|---------------|------|-------|
| MongoDB dump | (mongodump output) | `mongo.container` | ~1M | Existing, keep |
| `.env.secrets` | project root | N/A | 1.2K | All passwords, tokens, salts |
| `.env` | project root | N/A | ~2K | BASE_DOMAIN, feature toggles |
| User repositories | `mounts/repositories/` | 4 quadlets | 1.8G | **Primary data asset** |
| Matomo DB | (mysqldump output) | `matomo-db.container` L13 | 201M | MariaDB — use mysqldump (D2) |
| Matomo config | `mounts/matomo/config/` | `matomo.container` L14 | ~4K | Salt, trusted hosts, plugins |
| Let's Encrypt certs | `certs/letsencrypt/` | `apache.container` L55/58 | varies | **PROD ONLY** — HTTPS certs |
| SWAMID signing cert | `certs/md-signer2.crt` | `prod/apache.container` L40 | ~1K | **PROD ONLY** — federation cert |
| SWAMID config | `mounts/apache/saml/swamid/` | `prod/apache.container` L37-39 | ~4K | **PROD ONLY** — per-domain shibboleth2.xml |

**IMPORTANT (should include):**

| Target | Path | Quadlet Source | Size | Notes |
|--------|------|---------------|------|-------|
| Transcription queue | `mounts/transcription-queued/` | `session-manager.container` L28 | 46M | Audio awaiting Whisper processing |
| In-flight uploads | `mounts/apache/apache/uploads/` | `apache.container` L59, `session-manager.container` L20 | 556K | Staged file uploads |
| Unimported audio | `mounts/session-manager/unimported_audio/` | `session-manager.container` L22 | ~0 | Failed imports awaiting retry |
| Transcription outputs | `mounts/whisper/outputs/`, `mounts/whisperx/outputs/` | Not mounted (legacy?) | varies | SRT/JSON/TXT results — investigate if actively used |
| EMU env file | `mounts/emu-webapp-server/.env` | `emu-webapp-server.container` L11/12 | ~1K | Service-specific config |
| versions.json | project root | N/A | ~1K | Locked component versions |

**OPTIONAL (exclude by default, include with `--full`):**

| Target | Path | Size | Notes |
|--------|------|------|-------|
| Whisper models | `mounts/whisper/models/` | 16G | Re-downloadable, large |
| Session state | `mounts/sessions/` | 16K | Transient, tied to running containers |
| Logs (all) | `mounts/*/logs/` + `mounts/session-manager/session-manager.log` | ~167M | Operational data |
| Matomo GeoIP DB | `mounts/matomo/DBIP-City.mmdb` | 130M | Re-downloadable |
| Repository template | `mounts/repository-template/` | 44K | Customizable project template |

**EXCLUDED (legacy, explicitly skipped):**

| Target | Path | Notes |
|--------|------|-------|
| Traefik data | `mounts/traefik/` | Legacy reverse proxy, no longer referenced by any quadlet |

### 1.3 Backup Manifest (`metadata.json`)

Each backup includes a manifest at the top level:

```json
{
  "version": 1,
  "timestamp": "2026-07-08T00:00:00+02:00",
  "hostname": "visp-server",
  "deployment_mode": "prod",
  "mongodb_version": "6.0.27",
  "components": {
    "webclient": "abc123...",
    "session-manager": "def456..."
  },
  "images": {
    "visp-apache": "sha256:...",
    "visp-session-manager": "sha256:..."
  },
  "targets": [
    {"name": "mongodb", "file": "mongodb.tar.gz", "checksum": "sha256:..."},
    {"name": "env", "file": "env.tar.gz", "checksum": "sha256:..."},
    {"name": "repositories", "file": "repositories.tar.gz", "file_count": 1234, "total_size": 1932735283},
    {"name": "matomo-db", "file": "matomo-db.tar.gz", "checksum": "sha256:..."}
  ]
}
```

Checksums are computed only for files under 100M (Design Decision D4). Large
targets record file count and total size instead.

This enables:
- Verification of backup integrity before restore
- MongoDB version compatibility check at restore time
- Tracking of which component versions were active
- Selective restore (restore only MongoDB, only env, etc.)

### 1.4 CLI Changes

```
./visp.py backup                         # Full backup (all critical + important)
./visp.py backup --db-only               # MongoDB only (current behavior, for speed)
./visp.py backup --full                  # Everything including models (16G+)
./visp.py backup --output DIR            # Output directory (created inside)
./visp.py backup --exclude TARGET        # Skip a target (e.g., --exclude repositories)
./visp.py backup --single-archive        # Wrap output dir into one tar.gz for transport
./visp.py backup --dry-run               # Show planned actions

./visp.py backup list                    # List available backups with metadata
./visp.py backup verify PATH             # Verify backup integrity without extracting
```

---

## Fix 2: Safe Restore

### 2.1 Pre-Restore Safety

Before destroying the current database, the restore command must:

1. **Validate archive** — Verify the directory/tar.gz is valid and contains a
   `metadata.json` (new format) or a `visp_mongodb_*` directory (legacy format).
2. **MongoDB version check** — Compare the MongoDB version in the manifest (or
   extracted from the archive filename for legacy backups) against the running
   MongoDB version. Warn on mismatch, allow `--force` to override.
3. **Stop dependent services** — Stop writers before restore. Use
   `ServiceManager` from `vispctl/service_manager.py` with a configurable timeout
   (default 30s). If a service fails to stop gracefully, offer `--force-stop`
   (SIGKILL).
4. **Auto-backup current state** — Create a quick mongodump of the current database
   before `--drop`, named `visp_mongodb_prerestore_{timestamp}.tar.gz`. If the restore
   fails, the user can fall back to this.

### 2.2 Restore Order

```
1. Verify archive integrity (checksums for small files, structure check for large)
2. Stop matomo, session-manager, apache, emu-webapp-server, wsrng-server (all writers)
3. Auto-backup current MongoDB state
4. Restore MongoDB (mongorestore --drop)
5. Stop matomo, matomo-db
6. Restore Matomo DB (mysql import from mysqldump)
7. Restore .env and .env.secrets (with user confirmation, these overwrite current)
8. Restore mounts/repositories/ (merge mode: don't delete existing files not in backup)
9. Restore mounts/matomo/config/
10. Restore mounts/transcription-queued/
11. Restore mounts/apache/apache/uploads/
12. Restore mounts/session-manager/unimported_audio/
13. [Prod only] Restore certs/letsencrypt/, certs/md-signer2.crt, mounts/apache/saml/swamid/
14. Start matomo-db, matomo
15. Start mongo, session-manager, apache, emu-webapp-server, wsrng-server
16. Run health checks (MongoDB connections, key collection counts)
```

### 2.3 CLI Changes

```
./visp.py restore PATH                   # Full restore with safety checks
./visp.py restore PATH --db-only         # Restore only MongoDB
./visp.py restore PATH --selective mongodb env   # Restore specific targets
./visp.py restore PATH --force           # Skip all confirmation prompts
./visp.py restore PATH --force-stop      # SIGKILL services that don't stop gracefully
./visp.py restore PATH --no-restart      # Don't restart services after restore
./visp.py restore PATH --dry-run         # Show what would be restored (uses tar --list, no extraction)
```

### 2.4 Service Stop/Start Integration

The restore command needs to orchestrate service lifecycle. This requires:

- `RestoreOrchestrator` class (new, `vispctl/restore.py`) that uses
  `ServiceManager` from `vispctl/service_manager.py` to stop/start services
- Stop order: `matomo`, `session-manager`, `apache`, `emu-webapp-server`,
  `wsrng-server`, `matomo-db` (writers first, then databases)
- Start order: `mongo`, `matomo-db`, `session-manager`, `apache`,
  `emu-webapp-server`, `wsrng-server`, `matomo`
- Timeout handling: 30s default for graceful stop, `--force-stop` sends SIGKILL
- Health check after restore: verify MongoDB is accepting connections, verify
  key collections exist with expected document counts

---

## Fix 3: Container Image Backup

### 3.1 The Problem

If the host disk fails completely, all locally-built Podman images are lost.
Rebuilding requires:

1. `external/` repos to be available (git clone via `deploy update`)
2. All Dockerfiles/Containerfiles to be correct
3. Base images to be available for pull
4. Significant time: building all images takes 20-40 minutes

For disaster recovery where you need to get the system running quickly on a new
machine, having the images saved saves this rebuild step. Image backup is an
**optimization**, not a requirement — rebuild is always available as a fallback.

### 3.2 Image Save/Load Commands

Add new subcommands to `ImageManager` in `vispctl/images.py`:

```
./visp.py images save                    # Save all VISP-built images (from BUILD_CONFIGS)
./visp.py images save --output DIR       # Output directory
./visp.py images save --select SERVICE   # Save only specific image(s)
./visp.py images load DIR                # Load images from backup directory
./visp.py images list                    # List saved image archives
./visp.py images verify DIR              # Check saved tars against stored digests
```

**Note:** Third-party images are NOT backed up. They are freely pullable from
Docker Hub and adding them provides no benefit for 2.7 GB of extra backup size.
Air-gapped recovery is out of scope — it requires a local mirror, not tar files.

### 3.3 Implementation Details

Each VISP-built image is saved as a separate compressed file. Images are
enumerated from `BUILD_CONFIGS` in `vispctl/build.py`, NOT from globbing
`localhost/visp-*` (which would catch legacy images).

```
visp_images_20260708_120000/
├── metadata.json                # Image digests, timestamps
├── visp-apache.tar.gz           # 799 MB → ~320 MB compressed
├── visp-session-manager.tar.gz  # 2.19 GB → ~876 MB compressed
├── visp-artic.tar.gz
├── visp-emu-webapp-server.tar.gz
├── visp-octra.tar.gz
├── visp-wsrng-server.tar.gz
├── visp-whisperx.tar.gz         # 8.66 GB → ~3.5 GB compressed
├── visp-jupyter-session.tar.gz  # 9.32 GB → ~3.7 GB compressed
├── visp-session-proxy.tar.gz
└── visp-podman-socket-proxy.tar.gz
```

**Total compressed: ~12 GB** (from 22.7 GB uncompressed, ~47% ratio).

Commands:
- Save: `podman save localhost/visp-apache:latest -o - | gzip > visp-apache.tar.gz`
- Load: `gunzip -c visp-apache.tar.gz | podman load`

### 3.4 Image Backup Priority

| Image | Size | Compressed | Priority | Reason |
|-------|------|-----------|----------|--------|
| `visp-session-manager` | 2.19 GB | ~876 MB | **Critical** | Complex build, custom packages |
| `visp-apache` | 799 MB | ~320 MB | **Critical** | Baked-in webclient + PHP API |
| `visp-jupyter-session` | 9.32 GB | ~3.7 GB | High | Saves 20-30 min rebuild |
| `visp-whisperx` | 8.66 GB | ~3.5 GB | High | Heavy pip install |
| `visp-emu-webapp-server` | 393 MB | ~157 MB | Medium | Rebuildable |
| `visp-wsrng-server` | 214 MB | ~86 MB | Medium | Rebuildable |
| `visp-octra` | 185 MB | ~74 MB | Medium | Rebuildable |
| `visp-podman-socket-proxy` | 165 MB | ~66 MB | Low | Trivial rebuild |
| `visp-artic` | 82 MB | ~33 MB | Low | Trivial rebuild |
| `visp-session-proxy` | 12 MB | ~5 MB | Low | Trivial rebuild |

### 3.5 Integration with Unified Backup

Image backups are kept **separate** from data backups. Images change infrequently
(only on code updates), while data changes constantly. The unified backup (Fix 1)
offers `--with-images` to include image archives, but this is opt-in and requires
confirmation (images add ~12 GB).

### 3.6 Image Backup Frequency Recommendation

- **Data backup**: Daily (or before any significant operation)
- **Image backup**: Weekly, or after each production `./visp.py build`
- **Config backup** (.env, .env.secrets): After any config change
- **Retention**: Keep last 4 weekly image backups (images change infrequently)

---

## Fix 4: Backup Verification

### 4.1 Archive Verification

`./visp.py backup verify PATH` should:

1. Validate directory structure or tar.gz structure
2. Check `metadata.json` exists and is valid JSON
3. Verify checksums for small targets (<100M)
4. For MongoDB backups: verify the mongodump directory structure contains
   expected collections (`users`, `projects`, etc.)
5. Report backup age and MongoDB version compatibility with current system
6. For large targets: verify file count and total size match manifest

### 4.2 Restore Dry Run

`./visp.py restore PATH --dry-run` should:

1. Use `tar --list` to show archive contents (no extraction needed)
2. Show what would be restored and which files would be overwritten
3. Compare MongoDB version
4. For legacy archives: show warning about missing safety features
5. Clean up (nothing was extracted, nothing to clean)

---

## Fix 5: Automated Backup

### 5.1 Cron Integration

The existing automated backup script in `docs/BACKUP_RESTORE.md` (lines 207-229)
only backs up MongoDB and repositories. It should be updated to use the new
unified backup command:

```bash
#!/bin/bash
BACKUP_DIR=/home/visp/backups
DATE=$(date +%Y%m%d_%H%M%S)
mkdir -p "$BACKUP_DIR"

cd /home/visp/Projects/visible-speech-deployment

# Unified backup (daily, critical + important targets)
./visp.py backup --output "$BACKUP_DIR/visp_$DATE"

# Weekly image backup (Sunday at 3 AM)
if [ "$(date +%u)" = "7" ]; then
    ./visp.py images save --output "$BACKUP_DIR/images_$DATE"
fi

# Keep last 7 daily backups, last 4 weekly image backups
find "$BACKUP_DIR" -maxdepth 1 -name "visp_*" -type d -mtime +7 -exec rm -rf {} +
find "$BACKUP_DIR" -maxdepth 1 -name "images_*" -type d -mtime +28 -exec rm -rf {} +

echo "[$(date)] Backup complete" >> "$BACKUP_DIR/backup.log"
du -sh "$BACKUP_DIR"/* >> "$BACKUP_DIR/backup.log"
```

### 5.2 Post-Build Image Backup Hook

Add a hook inside `vispctl/build.py` (not `visp.py`) that, when
`BUILD_AUTO_IMAGE_BACKUP=true` is set in `.env`, automatically saves the newly
built image after a successful build:

```python
# Inside vispctl/build.py, after successful build of a container image:
if env.get("BUILD_AUTO_IMAGE_BACKUP") == "true":
    # Skip in development mode — builds are too frequent
    if env.get("DEVELOPMENT_MODE", "false") == "true":
        return
    image_backup_dir = Path(env.get("IMAGE_BACKUP_DIR", "./backups/images"))
    image_backup_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    image_tag = f"localhost/visp-{svc_name}:latest"
    tar_path = image_backup_dir / f"visp-{svc_name}_{timestamp}.tar.gz"
    # Compress with gzip
    runner.run(["podman", "save", image_tag, "-o", "-"],
               pipe_to=["gzip", "> ", str(tar_path)])
    # Update latest symlink
    latest_link = image_backup_dir / f"visp-{svc_name}.tar.gz"
    latest_link.unlink(missing_ok=True)
    latest_link.symlink_to(tar_path.name)
```

Key design choices:
- **Always compressed** (`.tar.gz`)
- **Timestamped filenames** with `latest` symlink
- **Skipped in dev mode** — builds are too frequent during development
- **Opt-in only** — requires explicit env var

---

## Fix 6: Update Documentation

### 6.1 `docs/BACKUP_RESTORE.md`

The existing guide needs to be rewritten to reflect the new unified backup approach.
Current issues:

- Line 7-13: Says "Only 2 things need backup" — incomplete (misses .env.secrets,
  .env, Matomo, transcription queue, SWAMID config, certs)
- Line 242: Says "Config not backed up" — dangerous, .env.secrets is critical
- Line 243: Says "Secrets auto-generated" — misleading, losing them means you
  cannot authenticate to the restored database
- Line 86-91: Restore steps reference old image names (`operations-session`,
  `rstudio-session`) that no longer exist
- Missing: Image backup instructions
- Missing: Verification steps
- Missing: Service stop/start during restore
- Missing: Dev vs prod backup differences

### 6.2 `vispctl/backup.py` Disclaimer

The disclaimer at lines 156-160 should be removed (or updated) once the unified
backup is implemented, since it will cover all targets.

---

## Implementation Priority

| Priority | Fix | Effort | Risk if Not Done |
|----------|-----|--------|-----------------|
| **P0** | Fix 1.1-1.2: Split-archive backup with all critical targets | Medium | Complete data loss on disk failure |
| **P0** | Fix 2.1-2.3: Safe restore with pre-backup, service stop, version check | Medium | Data corruption during failed restore |
| **P1** | Fix 3: Image save/load commands (compressed, VISP-only) | Low | 20-40 min rebuild time on disaster recovery |
| **P1** | Fix 2.4: Service orchestration with timeout handling | Medium | Stale data from concurrent writes |
| **P2** | Fix 4: Backup verification + dry-run | Medium | Silent corruption goes undetected |
| **P2** | Fix 5: Automated backup improvements | Low | Manual backup forgotten |
| **P2** | Fix 6: Documentation update | Low | Users follow outdated instructions |

---

## Implementation Details

### File Changes Required

| File | Changes |
|------|---------|
| `vispctl/backup.py` | Keep MongoDB dump/restore only (narrowed scope per D3) |
| `vispctl/archive.py` (new) | tar.gz creation/extraction, split-archive management |
| `vispctl/restore.py` (new) | `RestoreOrchestrator` — service lifecycle + restore sequencing |
| `vispctl/manifest.py` (new) | `BackupManifest` — creation, validation, checksums |
| `vispctl/images.py` | Add `save_images()`, `load_images()`, `verify_images()` methods |
| `visp.py` | CLI changes: nested subparsers for backup/restore/images |
| `vispctl/service_manager.py` | Ensure stop timeout and force-stop are available |
| `tests/vispctl/test_backup.py` | Rewrite tests for split-archive format and safety features |
| `tests/vispctl/test_archive.py` (new) | Tests for ArchiveManager |
| `tests/vispctl/test_restore.py` (new) | Tests for RestoreOrchestrator |
| `docs/BACKUP_RESTORE.md` | Complete rewrite to match new system |

### Backward Compatibility

- Legacy `visp_mongodb_*_*.tar.gz` archives must still be restorable
- `./visp.py backup` without flags should do the full split-archive backup
  (breaking change in output format, but safer default)
- `./visp.py backup --db-only` provides the old behavior for scripts that expect
  a MongoDB-only archive
- `./visp.py restore` should auto-detect legacy vs new format archives
- Legacy format restore should warn about missing safety features

### BackupManifest Class

```python
class BackupManifest:
    def __init__(self, version: int = 1): ...
    def add_target(self, name: str, file: str,
                   checksum: str | None = None,
                   file_count: int | None = None,
                   total_size: int | None = None): ...
    def save(self, backup_dir: Path): ...
    def load(backup_dir: Path) -> BackupManifest: ...
    def verify(backup_dir: Path) -> list[str]: ...  # returns list of issues
```

### Archive Format (Split)

```
visp_backup_20260708_120000/
├── metadata.json          ← BackupManifest
├── mongodb.tar.gz         ← mongodump output (compressed)
├── env.tar.gz             ← .env + .env.secrets
├── repositories.tar.gz    ← user project data
├── matomo-db.tar.gz       ← mysqldump output
├── matomo-config.tar.gz   ← config.ini.php
├── transcription-queued.tar.gz
├── uploads.tar.gz
├── unimported-audio.tar.gz
├── swamid-config.tar.gz   ← prod only
└── certs.tar.gz           ← letsencrypt + md-signer2.crt
```

Selective extraction is straightforward:
```bash
tar -xzf visp_backup_20260708_120000/env.tar.gz          # Extract only config
tar -xzf visp_backup_20260708_120000/mongodb.tar.gz      # Extract only MongoDB
```

### Disaster Recovery Scenario

**Complete host disk failure, new machine:**

```
1. Deploy repo: git clone + cd
2. Restore .env + .env.secrets from backup
3. ./visp.py install --mode prod (generates configs, secrets)
4. ./visp.py deploy update (populates external/)
5. Start mongo, load mongodump
6. EITHER: ./visp.py images load backups/images_latest/  (faster)
   OR:     ./visp.py build all                            (20-40 min, needs network)
7. Restore mounts/repositories/, mounts/matomo-db/, etc.
8. ./visp.py start all
9. Verify: ./visp.py status + ./visp.py backup verify
```
