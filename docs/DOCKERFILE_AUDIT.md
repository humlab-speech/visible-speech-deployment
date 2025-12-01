# Dockerfile Audit - December 1, 2025

## Summary

We have Dockerfiles in two locations:
1. **`docker/service-name/Dockerfile`** - Part of this deployment project
2. **`external/service-name/Dockerfile`** - Part of external repos (where they exist)

Docker Compose configurations reference different locations depending on the service.

---

## Current State by Service

### Services Using `external/` Dockerfiles ✅

#### 1. **session-manager**
- **Docker Compose**: `context: ./external/session-manager, dockerfile: Dockerfile`
- **Active Dockerfile**: `external/session-manager/Dockerfile` (multi-stage, modern)
- **Legacy Dockerfile**: `docker/session-manager/Dockerfile` (old, git clones)
- **Status**: ⚠️ **DUPLICATE** - docker/ version is NOT used
- **Recommendation**: Archive or delete `docker/session-manager/Dockerfile`

**Differences**:
- `docker/`: Single-stage, clones from GitHub, 35 lines
- `external/`: Multi-stage build, uses COPY from context, 96 lines, optimized

#### 2. **wsrng-server**
- **Docker Compose**: `context: ./external/wsrng-server, dockerfile: Dockerfile`
- **Active Dockerfile**: `external/wsrng-server/Dockerfile` (multi-stage, Alpine)
- **Legacy Dockerfile**: `docker/wsrng-server/Dockerfile` (old, Debian)
- **Status**: ⚠️ **DUPLICATE** - docker/ version is NOT used
- **Recommendation**: Archive or delete `docker/wsrng-server/Dockerfile`

**Differences**:
- `docker/`: Debian-based, clones from GitHub, runs as root
- `external/`: Alpine-based, multi-stage, runs as node user (security), optimized

#### 3. **webclient** (not directly in compose, used by apache)
- **Has Dockerfile**: `external/webclient/Dockerfile`
- **Not used**: Apache Dockerfile clones and builds webclient internally
- **Status**: Available but not currently utilized

---

### Services Using `docker/` Dockerfiles ✅

#### 4. **emu-webapp-server**
- **Docker Compose**: `build: "./docker/emu-webapp-server"`
- **Active Dockerfile**: `docker/emu-webapp-server/Dockerfile`
- **External Dockerfile**: ❌ Does not exist
- **Status**: ✅ **CORRECT** - Only one location, no duplication
- **Volume mount**: `./external/emu-webapp-server:/home/node/app:Z` in dev mode

#### 5. **apache**
- **Docker Compose**: `build: "./docker/apache"`
- **Active Dockerfile**: `docker/apache/Dockerfile`
- **Status**: ✅ **CORRECT** - Clones webclient internally during build
- **Note**: Could potentially use `external/webclient` instead of git clone

#### 6. **emu-webapp**
- **Docker Compose**: `context: ./external, dockerfile: ../docker/emu-webapp/Dockerfile`
- **Active Dockerfile**: `docker/emu-webapp/Dockerfile`
- **Build context**: Points to `external/` so Dockerfile can find `EMU-webApp/`
- **Status**: ✅ **CORRECT** - Unique setup, references external repo via context

#### 7. **octra**
- **Docker Compose**: `build: "./docker/octra"`
- **Active Dockerfile**: `docker/octra/Dockerfile`
- **Status**: ✅ **CORRECT** - Self-contained, no external repo

#### 8. **whisper**
- **Docker Compose**: `build: "./docker/whisper"`
- **Active Dockerfile**: `docker/whisper/Dockerfile`
- **Status**: ✅ **CORRECT** - Self-contained

#### 9. **whisperx**
- **Docker Compose**: `build: "./docker/whisperx"`
- **Active Dockerfile**: `docker/whisperx/Dockerfile`
- **Status**: ✅ **CORRECT** - Self-contained

#### 10. **labjs**
- **Active Dockerfile**: `docker/labjs/Dockerfile`
- **Status**: ✅ Available but not in current compose files

#### 11. **hs-wsr-client**
- **Active Dockerfile**: `docker/hs-wsr-client/Dockerfile`
- **Status**: ✅ Available but not in current compose files

---

## Problem: Duplicate Dockerfiles

### 1. session-manager
```
docker/session-manager/Dockerfile        ← NOT USED (legacy)
external/session-manager/Dockerfile      ← ACTIVE (used by docker-compose)
```

**Evidence**:
```yaml
# docker-compose.dev.yml
session-manager:
  build:
    context: ./external/session-manager
    dockerfile: Dockerfile  # Uses external/session-manager/Dockerfile
```

### 2. wsrng-server
```
docker/wsrng-server/Dockerfile           ← NOT USED (legacy)
external/wsrng-server/Dockerfile         ← ACTIVE (used by docker-compose)
```

**Evidence**:
```yaml
# docker-compose.dev.yml
wsrng-server:
  build:
    context: ./external/wsrng-server
    dockerfile: Dockerfile  # Uses external/wsrng-server/Dockerfile
```

---

## Recommendations

### Immediate Actions

1. **Archive unused Dockerfiles**:
   ```bash
   mkdir -p ARCHIVE/docker-legacy
   mv docker/session-manager/Dockerfile ARCHIVE/docker-legacy/session-manager-Dockerfile
   mv docker/wsrng-server/Dockerfile ARCHIVE/docker-legacy/wsrng-server-Dockerfile
   ```

2. **Document in docker/ subdirectories**:
   - Add README in `docker/session-manager/` explaining that the Dockerfile is now in the external repo
   - Add README in `docker/wsrng-server/` with same explanation

3. **Keep supporting files**:
   - `docker/session-manager/` has session templates (jupyter, rstudio, etc.) - **KEEP THESE**
   - Only remove the main Dockerfile, not the entire directory

### Long-term Considerations

#### Option A: All Dockerfiles in external repos (if they exist)
**Pros**:
- Repo is self-contained
- Can build externally without this deployment project
- Version-controlled with the code they build

**Cons**:
- This deployment project doesn't control build process
- Need to coordinate Dockerfile changes with external repos

#### Option B: All Dockerfiles in docker/ directory
**Pros**:
- Centralized build control
- Easier to maintain consistency
- Deployment project owns the build process

**Cons**:
- External repos can't be built standalone
- More complex references in docker-compose

#### Option C: Hybrid (current approach)
**Pros**:
- Flexibility - use what makes sense per service
- External repos that need standalone builds can have their own

**Cons**:
- Can create confusion (like this audit!)
- Need clear documentation

**Recommendation**: Stick with **Option C (Hybrid)** but:
1. Document clearly which Dockerfile is used
2. Remove/archive unused duplicates
3. Add READMEs explaining the choice

---

## Documentation Updates Needed

### In `docker/session-manager/README.md`
```markdown
# Session Manager Build Context

**Note**: The Dockerfile for building the session-manager container is located in the
`external/session-manager/` repository, not here.

This directory contains:
- Session templates (RStudio, Jupyter, VS Code, Operations)
- Build scripts for session images
- Supporting files for session management

The main service Dockerfile is maintained in the session-manager repository itself
for standalone building capability.
```

### In `docker/wsrng-server/README.md`
```markdown
# WSRNG Server Build Context

**Note**: The Dockerfile for building the wsrng-server container is located in the
`external/wsrng-server/` repository, not here.

The Dockerfile is maintained in the wsrng-server repository for standalone building.
```

### In `docs/FOLDER_STRUCTURE.md`
Add section explaining the Dockerfile location decision:
- Services with external repos may have Dockerfiles in the external repo
- Docker Compose points to the appropriate location
- Legacy Dockerfiles may exist in docker/ but are not used

---

## Testing Commands

To verify which Dockerfile is actually used:

```bash
# Build with verbose output to see which Dockerfile is read
docker compose build --progress=plain session-manager 2>&1 | head -50

# Check resolved build context
docker compose config | grep -A 5 "session-manager:"
```

---

## Migration Path (if we wanted to standardize)

### If moving ALL to docker/ (centralized):
1. Copy `external/session-manager/Dockerfile` to `docker/session-manager/Dockerfile`
2. Update docker-compose to use `build: "./docker/session-manager"`
3. Dockerfile would need to reference `../../external/session-manager/` for source
4. Test builds

### If moving ALL to external/ (distributed):
1. Ensure all external repos have Dockerfiles
2. Update docker-compose for apache, emu-webapp-server, etc. to point to external/
3. Services without external repos (octra, whisper) stay in docker/
4. Test builds

**Current recommendation**: Don't migrate. Just clean up duplicates and document clearly.
