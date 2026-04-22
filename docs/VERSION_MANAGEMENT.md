# Version Management Guide

## Overview

The `versions.json` file controls which versions of external components are used.
It is managed via `./visp.py deploy` subcommands — edit it directly only as a last resort.

Each component entry has:
- `version`: Active version to check out (`"latest"`, a commit SHA, or a git tag)
- `locked_version`: Last recorded stable SHA (used for rollback)
- `url`: Git repository URL
- Build flags: `npm_install`, `npm_build`

<<<<<<< Updated upstream
## Quick Reference
=======
## Current Locked Versions (as of 2025-12-01)
```
webclient:         e7d7b780 - removed lock file
container-agent:   d98ef9da - security updates
wsrng-server:      a8dcb4d2 - Fix: set correct file ownership for node user
session-manager:   ee3bf558 - Refactor: improve code organization
emu-webapp-server: d6026f65 - docs: remove GitLab format reference
arctic:        1434f7c5 - Merged changes from 1.5.1
```

## Usage Workflows

### 1. Lock to Current Versions (Stability)
When you want to freeze all versions for production stability:
>>>>>>> Stashed changes

```bash
./visp.py deploy status          # Check repo/image versions and drift
./visp.py deploy update          # Clone missing repos, pull latest (unlocked)
./visp.py deploy lock webclient  # Lock webclient to current commit
./visp.py deploy lock --all      # Lock all components
./visp.py deploy unlock --all    # Unlock all (track latest again)
./visp.py deploy rollback --all  # Revert to locked_version SHAs
```

---

## Workflows

### Update all to latest (development)

```bash
./visp.py deploy update
./visp.py deploy status   # review what changed
./visp.py build all       # rebuild stale images
```

### Lock for production stability

```bash
<<<<<<< Updated upstream
# Test everything first, then:
./visp.py deploy lock --all
git add versions.json
git commit -m "chore(deploy): lock all components to tested versions"
=======
# This is the default - versions.json already has "latest" for all
python3 visp.py update

# After testing, record the working commits as locked_version:
for dir in webclient container-agent wsrng-server session-manager emu-webapp-server arctic; do
  if [ -d "$dir/.git" ]; then
    echo "$dir: $(cd $dir && git rev-parse HEAD)"
  fi
done

# Update locked_version fields in versions.json with these SHAs
>>>>>>> Stashed changes
```

### Test a single component update

```bash
# 1. Unlock just that component
./visp.py deploy unlock webclient

# 2. Pull latest
./visp.py deploy update

# 3. Rebuild and test
./visp.py build webclient
./visp.py restart apache

# 4a. Looks good → lock it
./visp.py deploy lock webclient

# 4b. Broken → rollback to last locked version
./visp.py deploy rollback webclient
./visp.py build webclient
```

### Rollback after a bad update

```bash
./visp.py deploy rollback --all   # revert to locked_version SHAs
./visp.py build all               # rebuild from rolled-back source
./visp.py restart all
```

---

## Version String Formats

| Format | Example | Use case |
|--------|---------|----------|
| `"latest"` | `"latest"` | Development — always pulls newest commits |
| Commit SHA | `"a8dcb4d2"` | Production — reproducible, stable |
| Git tag | `"v1.2.3"` | If the repo uses tags (most humlab-speech repos don't) |

---


Run `./visp.py deploy lock --all` after verifying a working state to record new locked versions.

---

## Related Documentation

<<<<<<< Updated upstream
- [VERSION_CHECKING.md](VERSION_CHECKING.md) — How the status system works (image labels, drift detection)
- [BACKUP_RESTORE.md](BACKUP_RESTORE.md) — Backup and restore procedures
=======
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
for dir in webclient container-agent wsrng-server session-manager emu-webapp-server arctic; do
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
for dir in webclient container-agent wsrng-server session-manager emu-webapp-server arctic; do
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
- `visp.py` - Reads versions.json, clones/updates repos
- `TODO.md` - Lists version management improvements needed
>>>>>>> Stashed changes
