# Version Management Guide

## Overview

The `versions.json` file controls which versions of external components are used.
It is managed via `./visp.py deploy` subcommands — edit it directly only as a last resort.

Each component entry has:
- `version`: Active version to check out (`"latest"`, a commit SHA, or a git tag)
- `locked_version`: Last recorded stable SHA (used for rollback)
- `url`: Git repository URL
- Build flags: `npm_install`, `npm_build`

## Quick Reference

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
# Test everything first, then:
./visp.py deploy lock --all
git add versions.json
git commit -m "chore(deploy): lock all components to tested versions"
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

- [VERSION_CHECKING.md](VERSION_CHECKING.md) — How the status system works (image labels, drift detection)
- [BACKUP_RESTORE.md](BACKUP_RESTORE.md) — Backup and restore procedures
