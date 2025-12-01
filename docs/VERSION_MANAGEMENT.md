# Version Management Guide

## Overview
The `versions.json` file controls which versions of external components are used. Each component has:
- `version`: The version to checkout (can be "latest", a commit SHA, or a git tag)
- `locked_version`: The last known stable commit SHA (for rollback/reference)
- `url`: Git repository URL
- Build flags: `npm_install`, `npm_build`, `containerized`

## Current Locked Versions (as of 2025-12-01)
```
webclient:         e7d7b780 - removed lock file
webapi:            127e3630 - refactor: remove GitLab integration
container-agent:   d98ef9da - security updates
wsrng-server:      a8dcb4d2 - Fix: set correct file ownership for node user
session-manager:   ee3bf558 - Refactor: improve code organization
emu-webapp-server: d6026f65 - docs: remove GitLab format reference
EMU-webApp:        1434f7c5 - Merged changes from 1.5.1
```

## Usage Workflows

### 1. Lock to Current Versions (Stability)
When you want to freeze all versions for production stability:

```bash
# For each component, set version to locked_version
# Edit versions.json:
"webclient": {
  "version": "e7d7b780e0142dd87ac7cd9cb4be91fcd581c5fa",  # Changed from "latest"
  "locked_version": "e7d7b780e0142dd87ac7cd9cb4be91fcd581c5fa",
  ...
}

# Then update to checkout locked versions
python3 visp_deploy.py update
```

### 2. Test a New Version (Single Component)
When you want to try a newer version of one component:

```bash
# 1. Check what's new in the repo
cd webclient
git fetch
git log --oneline HEAD..origin/main  # See what commits are newer

# 2. Choose a commit to test
# Edit versions.json:
"webclient": {
  "version": "abc123def456...",  # New commit SHA you want to test
  "locked_version": "e7d7b780...",  # Keep the old stable version here
  ...
}

# 3. Update just that component
python3 visp_deploy.py update  # Will checkout the new version
docker compose build webclient  # Rebuild if needed
docker compose up -d           # Test it

# 4a. If it works, update locked_version to the new SHA
# 4b. If it breaks, revert version back to locked_version
```

### 3. Update All to Latest (Development/Testing)
When you want to pull latest versions of everything:

```bash
# This is the default - versions.json already has "latest" for all
python3 visp_deploy.py update

# After testing, record the working commits as locked_version:
for dir in webclient webapi container-agent wsrng-server session-manager emu-webapp-server EMU-webApp; do
  if [ -d "$dir/.git" ]; then
    echo "$dir: $(cd $dir && git rev-parse HEAD)"
  fi
done

# Update locked_version fields in versions.json with these SHAs
```

### 4. Rollback After Bad Update
If an update breaks something:

```bash
# Option A: Revert to locked versions in versions.json
# Set all "version" fields to their "locked_version" values
python3 visp_deploy.py update

# Option B: Manual git rollback for single component
cd session-manager
git checkout ee3bf558  # Use locked_version SHA
cd ..
docker compose build session-manager
docker compose up -d session-manager
```

## Version String Formats

### "latest" (Default)
```json
"version": "latest"
```
- Pulls from main/master branch
- Always gets newest commits on update
- Good for: Development, testing upstream changes
- Risk: Breaking changes without warning

### Commit SHA (Recommended for Production)
```json
"version": "a8dcb4d23bd50a82586a5838a2ff27d7e3c94fbb"
```
- Locks to exact commit
- Reproducible builds
- Good for: Production stability, debugging
- Update manually when ready to upgrade

### Git Tag (If Repos Use Them)
```json
"version": "v1.2.3"
```
- Locks to tagged release
- Semantic versioning if maintained
- Good for: Following official releases
- Note: Most humlab-speech repos don't use tags currently

## Best Practices

### Production Deployment
1. **Use locked commit SHAs** in `version` field
2. Test updates in staging/dev first
3. Update `locked_version` only after verifying stability
4. Document why each update was done (commit message in visp deployment repo)

### Development Workflow
1. **Use "latest"** to stay current with team changes
2. Run `visp_deploy.py update` regularly
3. Test after each update
4. Record working commit SHAs if you need to share setup

### Updating Locked Versions
When you've tested a new version and want to make it the new stable:

```bash
# 1. Test with version set to new SHA
# 2. Verify everything works
# 3. Update both fields in versions.json:
"component": {
  "version": "new_sha_here",        # Active version
  "locked_version": "new_sha_here",  # Record as new stable
}
# 4. Commit the versions.json change with good description
git add versions.json
git commit -m "Update webclient to abc123 - fixes XYZ issue"
```

## Automation Ideas (Future)

### Lock Script
```bash
#!/bin/bash
# lock-versions.sh - Lock all components to current commits
for dir in webclient webapi container-agent wsrng-server session-manager emu-webapp-server EMU-webApp; do
  if [ -d "$dir/.git" ]; then
    sha=$(cd $dir && git rev-parse HEAD)
    msg=$(cd $dir && git log -1 --format="%s")
    echo "Locking $dir to $sha - $msg"
    # Would need jq to actually update versions.json programmatically
  fi
done
```

### Update Checker
```bash
#!/bin/bash
# check-updates.sh - See what's new in each repo
for dir in webclient webapi container-agent wsrng-server session-manager emu-webapp-server EMU-webApp; do
  if [ -d "$dir/.git" ]; then
    cd $dir
    git fetch -q
    ahead=$(git rev-list --count HEAD..origin/main 2>/dev/null || git rev-list --count HEAD..origin/master 2>/dev/null || echo "0")
    if [ "$ahead" != "0" ]; then
      echo "=== $dir: $ahead new commits ==="
      git log --oneline HEAD..origin/main 2>/dev/null || git log --oneline HEAD..origin/master 2>/dev/null
      echo
    fi
    cd ..
  fi
done
```

## Related Files
- `versions.json` - Version configuration
- `visp_deploy.py` - Reads versions.json, clones/updates repos
- `TODO.md` - Lists version management improvements needed
