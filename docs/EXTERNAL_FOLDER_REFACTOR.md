# External Folder Refactoring Plan

## Goal
Move all external git repositories from project root to `external/` directory for better organization.

## Current Structure
```
visible-speech-deployment/
├── webclient/           # External repo
├── webapi/              # External repo
├── container-agent/     # External repo
├── wsrng-server/        # External repo
├── session-manager/     # External repo
├── emu-webapp-server/   # External repo
├── EMU-webApp/          # External repo
├── docker/              # Internal (Dockerfiles)
├── mounts/              # Internal (runtime data)
├── visp_deploy.py       # Internal (deployment script)
└── ...
```

## Proposed Structure
```
visible-speech-deployment/
├── external/
│   ├── webclient/
│   ├── webapi/
│   ├── container-agent/
│   ├── wsrng-server/
│   ├── session-manager/
│   ├── emu-webapp-server/
│   └── EMU-webApp/
├── docker/
├── mounts/
├── visp_deploy.py
└── ...
```

## Impact Analysis

### Files That Need Updates

#### 1. visp_deploy.py (LOW EFFORT)
**Location:** Clone path
**Current:** `git clone <url> <name>` (clones to project root)
**New:** `git clone <url> external/<name>`

**Changes needed:**
```python
# Line ~630 in clone_repositories():
repo_path = os.path.join(basedir, "external", name)  # Add "external"

# Line ~708 in fix_repository_permissions():
for name in versions_config.keys():
    repo_path = os.path.join(os.getcwd(), "external", name)  # Add "external"
```

**Affected functions:**
- `clone_repositories()` - Add "external" to path
- `fix_repository_permissions()` - Add "external" to path
- `update_repo()` - Already uses basedir, just pass `basedir/external`

#### 2. docker-compose.dev.yml (MODERATE EFFORT)
**Purpose:** Volume mounts for development hot-reload

**Current paths:**
```yaml
- "./webclient/dist:/var/www/html:Z"
- "./webapi:/var/www/webapi:Z"
- "./webapi/vendor:/var/www/html/api/vendor:Z"
- "./session-manager:/session-manager:Z"
- "./emu-webapp-server:/home/node/app:Z"
- "./wsrng-server:/wsrng-server:Z"
```

**New paths:** (simple prefix with `external/`)
```yaml
- "./external/webclient/dist:/var/www/html:Z"
- "./external/webapi:/var/www/webapi:Z"
- "./external/webapi/vendor:/var/www/html/api/vendor:Z"
- "./external/session-manager:/session-manager:Z"
- "./external/emu-webapp-server:/home/node/app:Z"
- "./external/wsrng-server:/wsrng-server:Z"
```

**Changes:** ~10 lines (search & replace)

#### 3. docker-compose.prod.yml (LOW EFFORT)
**Purpose:** Build contexts for containerized services

**Current paths:**
```yaml
context: ./session-manager
context: ./wsrng-server
- ./wsrng-server/.env
- "./webapi:/var/www/webapi:Z"  # Only mount in prod currently
```

**New paths:**
```yaml
context: ./external/session-manager
context: ./external/wsrng-server
- ./external/wsrng-server/.env
- "./external/webapi:/var/www/webapi:Z"
```

**Changes:** ~4 lines

#### 4. Dockerfiles (MODERATE EFFORT)
**Purpose:** Build images from external repos

**apache Dockerfile (`docker/apache/Dockerfile`):**
```dockerfile
# Current:
RUN git clone https://github.com/humlab-speech/webclient
WORKDIR /webclient
# ...
```

**No change needed!** Apache Dockerfile clones its own copy of webclient at build time, doesn't use the host checkout.

**Other Dockerfiles:** Most use multi-stage builds that COPY from context, which is handled by docker-compose.yml paths.

#### 5. .gitignore (LOW EFFORT)
**Current:** Probably ignores external repos individually
```gitignore
/webclient/
/webapi/
/wsrng-server/
...
```

**New:** Simpler - ignore whole external directory
```gitignore
/external/
```

**Changes:** 1 line (replaces ~7 lines)

### Files That DON'T Need Updates
- Individual repo Dockerfiles (they're inside external/)
- README.md (doesn't reference paths directly)
- Most mounts/ configs (runtime data, not source code)
- Shell scripts that cd into dirs by name only

## Migration Steps

### Step 1: Create Structure
```bash
mkdir -p external
```

### Step 2: Move Repos
```bash
for dir in webclient webapi container-agent wsrng-server session-manager emu-webapp-server EMU-webApp; do
  if [ -d "$dir" ]; then
    echo "Moving $dir to external/"
    git mv "$dir" "external/$dir" 2>/dev/null || mv "$dir" "external/$dir"
  fi
done
```

### Step 3: Update Configuration Files
```bash
# visp_deploy.py
sed -i 's|os.path.join(basedir, name)|os.path.join(basedir, "external", name)|g' visp_deploy.py

# docker-compose files
sed -i 's|\./webclient/|./external/webclient/|g' docker-compose.*.yml
sed -i 's|\./webapi|./external/webapi|g' docker-compose.*.yml
sed -i 's|\./session-manager|./external/session-manager|g' docker-compose.*.yml
sed -i 's|\./wsrng-server|./external/wsrng-server|g' docker-compose.*.yml
sed -i 's|\./emu-webapp-server|./external/emu-webapp-server|g' docker-compose.*.yml
```

### Step 4: Update .gitignore
```bash
# Remove old individual ignores, add /external/
# Edit .gitignore manually
```

### Step 5: Test
```bash
# Test clone (on a new machine or delete external/ first)
python3 visp_deploy.py install

# Test builds
docker compose build

# Test dev mode
docker compose -f docker-compose.dev.yml up -d

# Test prod mode
docker compose -f docker-compose.prod.yml up -d
```

## Difficulty Assessment

### Overall: **EASY to MODERATE** ⭐⭐⭐☆☆

**Why it's easy:**
- ✅ Mostly path updates (search & replace)
- ✅ No logic changes needed
- ✅ Can test incrementally
- ✅ Easy to rollback (just git revert)
- ✅ Only ~20-30 lines of code to change

**Potential gotchas:**
- ⚠️ Need to update BOTH docker-compose files
- ⚠️ Development mounts and production build contexts
- ⚠️ Any hardcoded paths in shell scripts (need to audit)
- ⚠️ Could break existing deployments (need migration doc)

**Time estimate:** 1-2 hours for changes + testing

## Benefits

### Immediate
- ✅ **Clearer project structure** - obvious what's external vs internal
- ✅ **Simpler .gitignore** - one line instead of seven
- ✅ **Better IDE experience** - external repos don't clutter root

### Long-term
- ✅ **Easier to document** - "all external code is in external/"
- ✅ **Easier to audit** - know exactly what's pulled from outside
- ✅ **Future-proof** - if more external deps added, they go in external/
- ✅ **Better for tooling** - linters/formatters can easily exclude external/

## Risks

### Low Risk
- Changes are mechanical (path updates)
- Git history preserved (using git mv)
- Easy to test (just run deploy script)

### Mitigation
- Do on a branch first
- Test both dev and prod compose modes
- Update documentation before merging
- Add migration notes to CHANGELOG

## Recommendation

**YES, do the refactor**

The effort is reasonable (few hours) and benefits are significant. The code changes are simple path updates with low risk of breakage.

**Best time to do it:**
- After current feature/backend-cleanup is merged
- Before next production deployment
- When you have time to test thoroughly

**Suggested approach:**
1. Create new branch `refactor/external-folder`
2. Do the migration in one commit (easier to review/revert)
3. Test locally with both compose modes
4. Update docs in same commit
5. Merge after testing

## Alternative: Phased Approach

If you want lower risk, do it in phases:

### Phase 1: Just the Python script
Update visp_deploy.py to clone to external/, test install works

### Phase 2: Dev compose
Update docker-compose.dev.yml, test dev mode

### Phase 3: Prod compose
Update docker-compose.prod.yml, test prod mode

### Phase 4: Cleanup
Update .gitignore, documentation

**Each phase is independently testable and can be rolled back.**
