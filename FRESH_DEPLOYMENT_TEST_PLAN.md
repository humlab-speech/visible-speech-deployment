# Fresh Deployment Test Plan

## Context for New Session

We have recently merged deployment functionality from `visp-deploy.py` into `visp-podman.py` and added comprehensive version tracking with git commit labels. This test plan validates the entire deployment workflow from scratch.

## What Has Been Implemented

1. **Deploy Subcommand Integration**
   - `./visp-podman.py deploy status` - Show git/build status for all components
   - `./visp-podman.py deploy lock` - Lock components to current versions
   - `./visp-podman.py deploy unlock` - Unlock components to track latest
   - `./visp-podman.py deploy rollback` - Rollback to locked versions
   - `./visp-podman.py deploy update` - Update external repos to latest

2. **Version Tracking System**
   - Images are labeled with `git.commit` and `build.timestamp` during build
   - `deploy status` compares image git.commit vs current source commits
   - Build command checks version drift before building (fails in prod if mismatch)
   - Install command auto-generates passwords on first run
   - All documented in `docs/VERSION_CHECKING.md`

3. **New vispctl Modules**
   - `vispctl/git_repo.py` - Git operations without changing directories
   - `vispctl/versions.py` - Component version management (versions.json)
   - `vispctl/passwords.py` - Password generation and .env management
   - `vispctl/deploy.py` - DeployManager with comprehensive status checking

## Issues Found in Previous Test

1. **MongoDB directory creation**: Install script doesn't create `mounts/mongo/data` and `mounts/mongo/logs`
2. **Webclient build permissions**: Permission denied errors when copying files (though Angular build succeeds)
3. **Need to verify**: All steps work cleanly from complete scratch

## Pre-Test Cleanup (CRITICAL)

Complete system reset to ensure clean test:

```bash
# 0. FIRST: Uninstall quadlets/systemd services (they will auto-restart containers!)
python3 visp-podman.py uninstall

# 1. Verify containers stopped
podman ps  # Should be empty

# 2. Remove ALL existing VISP images to force clean rebuild
podman rmi -f $(podman images --filter "reference=visp-*" -q) 2>/dev/null || true
podman rmi -f $(podman images --filter "reference=*/visp-*" -q) 2>/dev/null || true
podman rmi -f $(podman images --filter "reference=localhost/visp-*" -q) 2>/dev/null || true

# 3. Remove any remaining containers
podman rm -f $(podman ps -aq) 2>/dev/null || true

# 4. Backup current state (optional)
mkdir -p backups/pre-test-$(date +%Y%m%d-%H%M%S)
cp .env .env.secrets versions.json backups/pre-test-$(date +%Y%m%d-%H%M%S)/ 2>/dev/null || true

# 5. Remove state files
rm -f .env .env.secrets versions.json

# 6. Remove MongoDB data
sudo rm -rf mounts/mongo/*

# 7. Remove cloned repositories
sudo rm -rf mounts/repositories/*

# 8. Verify clean slate
podman images | grep visp  # Should show nothing
ls -la | grep -E "\.env|versions"  # Should only show templates
```

## Test Workflow (Step by Step)

### Phase 1: Clone External Repositories

```bash
# Test 1: Clone all external component repositories
python3 visp-podman.py deploy update

# Expected Results:
# ✅ Clones external/webclient
# ✅ Clones external/session-manager
# ✅ Clones external/wsrng-server
# ✅ Clones external/container-agent
# ✅ Clones external/emu-webapp-server
# ✅ Clones external/EMU-webApp
# ✅ Creates versions.json with current commits

# Verify:
ls -d external/webclient external/session-manager external/wsrng-server external/container-agent
ls -lh versions.json  # Should exist
```

### Phase 2: Fresh Install (Quadlets & Secrets)

```bash
# Test 2: Fresh install in dev mode
python3 visp-podman.py install --mode dev

# Expected Results:
# ✅ Creates .env and .env.secrets with auto-generated passwords
# ✅ All passwords generated (MONGO_ROOT_PASSWORD, etc.)
# ✅ Podman secrets created
# ✅ Quadlets installed
# ✅ Creates mounts/mongo/data and mounts/mongo/logs directories  ⚠️ CURRENTLY FAILING

# Verify:
ls -lh .env .env.secrets
cat .env.secrets | grep MONGO_ROOT_PASSWORD  # Should have value
ls -d mounts/mongo/data mounts/mongo/logs    # Should exist
```

**ISSUE TO FIX**: Install script should create MongoDB directories if they don't exist.

### Phase 3: Build All Images

```bash
# Test 3: Build all images from scratch
python3 visp-podman.py build all

# Expected Results:
# ✅ All images build successfully
# ✅ Each image tagged with git.commit label (from external/ source)
# ✅ Each image tagged with build.timestamp label
# ✅ Webclient builds without permission errors  ⚠️ CURRENTLY HAS WARNINGS
# ✅ versions.json created with current commits

# Verify:
podman images | grep visp  # Should show all images
podman inspect visp-apache:latest --format '{{.Labels}}' | grep git.commit  # Should show commit hash
ls -lh versions.json  # Should exist if created
```

**ISSUE TO FIX**: Webclient containerized build has permission denied errors on `cp` commands.

### Phase 4: Deploy Status Check

```bash
# Test 4: Check deployment status
python3 visp-podman.py deploy status

# Expected Results:
# ✅ Shows main repo status (branch, changes, ahead/behind)
# ✅ Shows all external component repos status
# ✅ Shows Build Status column for each component
# ✅ All images show "✅ UP TO DATE" (since just built)
# ✅ Summary shows how many components need rebuilding (should be 0)
```

### Phase 5: Start System

```bash
# Test 5: Start all containers
python3 visp-podman.py reload

# Wait for startup
sleep 10

# Check status
python3 visp-podman.py status

# Expected Results:
# ✅ All containers start successfully
# ✅ MongoDB starts without directory errors  ⚠️ CURRENTLY FAILING
# ✅ All networks active
# ✅ All services healthy
```

**ISSUE TO FIX**: MongoDB fails if directories don't exist.

### Phase 6: Version Locking/Unlocking

```bash
# Test 6: Lock components to current versions
python3 visp-podman.py deploy lock webclient session-manager

# Verify:
cat versions.json  # Should show locked versions for those components

# Test 7: Check status shows locked state
python3 visp-podman.py deploy status
# Expected: Shows "🔒 LOCKED" for webclient and session-manager

# Test 8: Unlock
python3 visp-podman.py deploy unlock webclient
# Expected: versions.json updated, status shows "🔓 UNLOCKED"
```

### Phase 7: Verify Build Version Checking

```bash
# Test 9: Make a change in external repo
cd external/webclient
echo "# test change" >> README.md
git add README.md
git commit -m "Test change for version drift"
cd ../..

# Test 10: Check status shows drift
python3 visp-podman.py deploy status
# Expected: webclient shows "⚠️ STALE" build status

# Test 11: Try to build in prod mode without force (should fail)
python3 visp-podman.py build webclient --mode prod
# Expected: Error about version drift

# Test 12: Build with --force flag
python3 visp-podman.py build webclient --force
# Expected: Builds successfully

# Test 13: Verify new git.commit label
podman inspect localhost/visp-webclient:latest --format '{{.Labels}}' | grep git.commit
# Expected: Shows new commit hash
```

### Phase 8: Access Running System

```bash
# Test 14: Verify system is accessible
curl -I http://localhost:8081  # Apache should respond
curl -I http://localhost:8080  # Traefik should respond

# Check logs if needed
python3 visp-podman.py logs apache
python3 visp-podman.py logs mongo
```

## Expected Test Outcomes

### Must Pass
- [ ] Fresh install completes without errors
- [ ] All passwords auto-generated correctly
- [ ] MongoDB directories created automatically
- [ ] All images build successfully with git.commit labels
- [ ] Webclient builds without permission errors
- [ ] All containers start successfully
- [ ] `deploy status` shows accurate build status
- [ ] Version locking/unlocking works correctly
- [ ] Build refuses to proceed with version drift in prod mode
- [ ] System is accessible via HTTP

### Known Issues to Fix
1. MongoDB directories not created by install script
2. Webclient build has cp permission errors (doesn't break build but shows warnings)
3. Verify versions.json is created at the right time

## After Testing: Create Quickstart Guide

Once all tests pass, create `docs/QUICKSTART.md` with verified steps:

```markdown
# VISP Deployment Quickstart

## Prerequisites
- Linux system with Podman
- Python 3.8+
- Git

## Fresh Deployment (5 Steps)

1. Clone repository
2. Run: `./visp-podman.py install --mode dev`
3. Run: `./visp-podman.py build all`
4. Run: `./visp-podman.py reload`
5. Access: http://localhost:8081

## Verify Deployment

Check status: `./visp-podman.py deploy status`

## Next Steps
- Lock production versions: `./visp-podman.py deploy lock --all`
- Update components: `./visp-podman.py deploy update`
- See full docs: `docs/DEPLOYMENT_GUIDE.md`
```

## Files to Review

Key files involved in this functionality:
- `visp-podman.py` - Main script with deploy subcommand
- `vispctl/deploy.py` - DeployManager with status checking
- `vispctl/build.py` - Image building with git labels
- `vispctl/git_repo.py` - Git operations
- `vispctl/versions.py` - Version management
- `vispctl/passwords.py` - Password generation
- `docs/VERSION_CHECKING.md` - Complete documentation of version tracking system

## Success Criteria

Test is successful when:
1. Complete fresh deployment works without manual intervention
2. All images contain git.commit labels
3. `deploy status` accurately tracks which images need rebuilding
4. System starts and is accessible
5. Version locking workflow functions correctly
6. Build version checking prevents mistakes in production

## Questions for User After Testing

1. Does the deploy status output show everything you need?
2. Is the version tracking system clear and useful?
3. Are there any additional checks or features needed?
4. Should we proceed with creating the visp-deploy.py deprecation wrapper?
