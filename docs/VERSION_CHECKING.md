# Version Checking and Build Status Tracking

This document explains how VISP tracks version consistency across git repositories, built images, and deployed containers. The system provides comprehensive status checking to ensure your deployment is consistent and up-to-date.

## Table of Contents

1. [Overview](#overview)
2. [How It Works](#how-it-works)
3. [Git Repository Checking](#git-repository-checking)
4. [Image Build Tracking](#image-build-tracking)
5. [Status Check Output](#status-check-output)
6. [Component-to-Image Mapping](#component-to-image-mapping)
7. [Verification Examples](#verification-examples)
8. [Troubleshooting](#troubleshooting)

## Overview

The VISP deployment system tracks three key aspects of version control:

1. **Git Repository Status**: What version of source code is on disk
2. **Image Build Status**: What version each container image was built from
3. **Remote Synchronization**: Whether local repos are ahead/behind remotes

This gives you a complete view of your deployment state from source code through built images.

## How It Works

### Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│ Git Repository (external/session-manager)                       │
│                                                                  │
│  Current Commit: abc123de  ←──┐                                 │
│  Uncommitted Changes: Yes      │                                │
│  Ahead of Remote: 2 commits    │ Compared                       │
└────────────────────────────────┼──────────────────────────────────┘
                                 │
                                 ↓
┌─────────────────────────────────────────────────────────────────┐
│ Container Image (visp-session-manager:latest)                   │
│                                                                  │
│  Labels:                                                         │
│    git.commit: 66f4ca5a      ←──┘                               │
│    build.timestamp: 2026-02-08T10:30:00                         │
│                                                                  │
│  Status: ⚠️ STALE (source has changed)                          │
└─────────────────────────────────────────────────────────────────┘
```

### The Status Check Process

When you run `./visp.py deploy status`:

1. **Checks the main deployment repository** (visible-speech-deployment)
   - Current branch
   - Uncommitted changes
   - Ahead/behind origin

2. **For each external component** (webclient, session-manager, etc.):
   - Reads current git commit from `external/<component>/`
   - Checks versions.json for locked/recommended version
   - Queries the corresponding container image for its `git.commit` label
   - Compares all three to determine status
   - Checks if local repo is ahead/behind remote

## Git Repository Checking

### What Gets Checked

For **every external repository** in `external/`:

#### 1. Current Commit
```python
# Implementation: vispctl/git_repo.py
repo = GitRepository("external/webclient")
current_commit = repo.get_current_commit()
# Returns: "f23595ffb943ff624e5b15cd85696043fca0af79"
```

This is the actual commit currently checked out on disk.

#### 2. Uncommitted Changes (Dirty State)
```python
has_changes = repo.is_dirty()
# Returns: True if there are modified/staged/untracked files
```

Uses `git status --porcelain` to detect any local modifications.

#### 3. Ahead/Behind Remote
```python
branch = repo.get_current_branch()  # e.g., "main"

# How many commits ahead
ahead = repo.count_commits_between("HEAD", f"origin/{branch}")

# How many commits behind
behind = repo.count_commits_between(f"origin/{branch}", "HEAD")
```

Uses `git rev-list --count` to calculate commit distances.

#### 4. Locked Version Comparison
```python
from vispctl.versions import ComponentConfig

config = ComponentConfig("versions.json")
locked = config.get_locked_version("webclient")
# Returns: "f23595ffb943..." (recommended production version)

# Compare with current
if current_commit == locked:
    # On recommended version
else:
    # Drifted from recommended version
```

### Main Deployment Repository

The main `visible-speech-deployment` repo is also checked:

- **Purpose**: Ensures deployment scripts are up-to-date
- **Warning**: If behind remote, you might be missing bug fixes or features
- **Action**: `git pull origin <branch>` to update

## Image Build Tracking

### How Git Commits are Embedded in Images

During `./visp.py build <component>`:

```python
# In vispctl/build.py - build_image()

# 1. Resolve the build context (e.g., external/session-manager)
context_path = Path(context).resolve()

# 2. Get current git commit from that directory
commit_result = subprocess.run(
    ["git", "rev-parse", "HEAD"],
    cwd=context_path,
    capture_output=True,
    text=True
)
commit_hash = commit_result.stdout.strip()

# 3. Add as label to podman build command
cmd.extend(["--label", f"git.commit={commit_hash}"])
cmd.extend(["--label", f"build.timestamp={datetime.now().isoformat()}"])

# 4. Build with labels embedded in image metadata
# podman build --label git.commit=abc123... -t visp-session-manager:latest
```

The labels are stored in the **OCI image manifest**, not in files inside the container.

### Retrieving Image Labels

```python
# In vispctl/deploy.py - _get_image_label()

# Query the label from image metadata
rc, stdout, _ = runner.run_quiet([
    "podman", "inspect", "visp-session-manager:latest",
    "--format", "{{.Labels.git.commit}}"
])

image_commit = stdout.strip()  # "66f4ca5a1234..."
```

### Build Status States

The system compares the **image commit** with the **current source commit**:

| Status | Condition | Meaning | Action |
|--------|-----------|---------|--------|
| ✅ **UP TO DATE** | `image_commit == current_commit` | Image matches current source | None needed |
| ⚠️ **STALE** | `image_commit != current_commit` | Source changed since build | Rebuild recommended |
| ❌ **NOT BUILT** | Image doesn't exist | No image available | Must build |
| ⚠️ **UNKNOWN** | No git.commit label | Old image, pre-labeling | Rebuild to add label |
| **N/A** | Component not buildable | Not a container image | Not applicable |

## Status Check Output

### Command

```bash
./visp.py deploy status [--no-fetch]
```

- Default: Fetches from remotes before checking (accurate but slower)
- `--no-fetch`: Uses cached remote state (faster)

### Output Sections

#### 1. Main Deployment Repository

```
🔧 DEPLOYMENT REPOSITORY (visible-speech-deployment)
┌──────────────────┬──────────────┬─────────────┬───────────────┬──────────────┐
│ Repository       │ Branch       │ Has Changes │ Behind Remote │ Ahead Remote │
├──────────────────┼──────────────┼─────────────┼───────────────┼──────────────┤
│ THIS REPO        │ main         │ ✅ NO       │ ✅ 0          │ ✅ 0         │
└──────────────────┴──────────────┴─────────────┴───────────────┴──────────────┘
```

**Columns:**
- **Has Changes**: Uncommitted modifications
- **Behind Remote**: Commits on remote not yet pulled
- **Ahead Remote**: Local commits not yet pushed

#### 2. External Component Repositories

```
📚 EXTERNAL COMPONENT REPOSITORIES
┌───────────────┬──────────────┬────────────┬────────────────┬────────┬──────────────┬─────────────┐
│ Repository    │ Lock Status  │ Current    │ Locked Version │ Status │ Build Status │ Sync Status │
│               │              │ Commit     │                │        │              │             │
├───────────────┼──────────────┼────────────┼────────────────┼────────┼──────────────┼─────────────┤
│ webclient     │ 🔓 UNLOCKED  │ f23595ff   │ f23595ff       │ ✅ CLEAN│ ✅ UP TO DATE│ ✅ SYNCED   │
│               │ (tracking    │            │                │        │              │             │
│               │  latest)     │            │                │        │              │             │
├───────────────┼──────────────┼────────────┼────────────────┼────────┼──────────────┼─────────────┤
│ session-mgr   │ 🔒 LOCKED    │ 66f4ca5a   │ 66f4ca5a       │ ⚠️ HAS │ ⚠️ STALE     │ 🚀 AHEAD    │
│               │ (at 66f4ca5a)│            │                │ CHANGES│              │ 2 ahead     │
└───────────────┴──────────────┴────────────┴────────────────┴────────┴──────────────┴─────────────┘
```

**Columns:**
- **Lock Status**: 🔒 LOCKED (fixed version) or 🔓 UNLOCKED (tracking latest)
- **Current Commit**: What's checked out on disk right now
- **Locked Version**: Recommended production version from versions.json
- **Status**: Git working tree state (✅ CLEAN or ⚠️ HAS CHANGES)
- **Build Status**: Does image match current source? (NEW in this system)
- **Sync Status**: Relationship with remote (✅ SYNCED, 🚀 AHEAD, ⬇️ BEHIND)

#### 3. Summary Section

```
====================================
⚠️ Components need rebuild (source changed): session-manager
   Run: ./visp.py build session-manager

⚠️ Repositories with uncommitted changes: webclient
   Total: 2 repo(s) have local changes

🚀 Repositories ahead of remote: webclient
   Total: 1 repo(s) need to push
====================================
```

**Actionable summaries:**
- Which components need rebuilding
- Which repos have uncommitted changes
- Which repos need push/pull

## Component-to-Image Mapping

The system knows which external repo builds into which container image:

| External Repository | Container Image | Notes |
|---------------------|-----------------|-------|
| `webclient` | `visp-apache:latest` | Built into Apache image |
| `session-manager` | `visp-session-manager:latest` | Direct mapping |
| `wsrng-server` | `visp-wsrng-server:latest` | Direct mapping |
| `emu-webapp-server` | `visp-emu-webapp:latest` | Direct mapping |
| `EMU-webApp` | `visp-emu-webapp:latest` | Same image as server |
| `container-agent` | `visp-operations-session:latest` | Bundled into session images |

This mapping is defined in `vispctl/deploy.py` in the `_get_build_status()` method.

## Verification Examples

### Example 1: Check Current State

```bash
$ ./visp.py deploy status --no-fetch
```

Instantly shows:
- What's checked out on disk
- What's in your images
- Whether they match

### Example 2: Verify Image Contains Specific Commit

```bash
# Check what commit an image was built from
$ podman inspect visp-session-manager:latest \
  --format '{{.Labels.git.commit}}'
66f4ca5a1234567890abcdef...

# Compare to current source
$ cd external/session-manager && git rev-parse HEAD
abc123de4567890abcdef...

# Different → Image is stale, needs rebuild
```

### Example 3: After Making Changes

```bash
# 1. Make changes to session-manager
$ cd external/session-manager
$ vim src/container-manager.js
$ git add . && git commit -m "Fix cleanup bug"

# 2. Check status
$ cd ../..
$ ./visp.py deploy status

# Output shows:
# session-manager:
#   Current Commit: <new commit>
#   Build Status: ⚠️ STALE (image from old commit)
#   Status: ✅ CLEAN
#   Sync Status: 🚀 AHEAD (1 commit)

# 3. Rebuild to match source
$ ./visp.py build session-manager

# 4. Verify
$ ./visp.py deploy status
# Now shows: Build Status: ✅ UP TO DATE
```

### Example 4: Production Lock Workflow

```bash
# 1. Lock current version for production
$ ./visp.py deploy lock webclient
✓ webclient: Locked to f23595ff
  Date: 2026-02-08
  Commit: Update Angular to v17

# 2. Verify everything is consistent
$ ./visp.py deploy status
# Should show:
# - Current Commit matches Locked Version
# - Build Status: ✅ UP TO DATE
# - Status: ✅ CLEAN

# 3. If not, rebuild from locked version
$ ./visp.py build webclient
```

## Troubleshooting

### Problem: Build Status shows "⚠️ UNKNOWN"

**Cause**: Image was built before git labeling feature was added.

**Solution**: Rebuild the image to add labels:
```bash
./visp.py build <component>
```

### Problem: Build Status shows "⚠️ STALE" but source hasn't changed

**Possible causes:**
1. Image was built from a different commit
2. Git repo was reset or rebased
3. Building from wrong branch

**Diagnosis:**
```bash
# Check image commit
podman inspect <image>:latest --format '{{.Labels.git.commit}}'

# Check current commit
cd external/<component> && git rev-parse HEAD

# Check current branch
git branch
```

**Solution:** If intentional, rebuild. If not, checkout correct commit.

### Problem: "Behind Remote" shows non-zero but you just pulled

**Cause**: Cached remote state (happens with `--no-fetch`)

**Solution**: Run without `--no-fetch` to update remote tracking:
```bash
./visp.py deploy status
```

### Problem: Can't tell if rebuild is needed

**Solution**: The status command now shows this explicitly:

- **✅ UP TO DATE**: No rebuild needed
- **⚠️ STALE**: Rebuild recommended
- **❌ NOT BUILT**: Must build
- **⚠️ UNKNOWN**: Rebuild recommended (to add labels)

### Problem: Image exists but Build Status is "N/A"

**Cause**: Component is not mapped to an image.

**Solution**: This is expected for non-containerized components.

## Implementation Details

### Files Involved

- **vispctl/deploy.py**: Status checking logic
  - `DeployManager.check_status()`: Main status check
  - `_get_build_status()`: Image vs source comparison
  - `_get_image_label()`: Label retrieval

- **vispctl/git_repo.py**: Git operations
  - `GitRepository.get_current_commit()`: Current HEAD
  - `GitRepository.is_dirty()`: Uncommitted changes check
  - `GitRepository.count_commits_between()`: Ahead/behind calculation

- **vispctl/versions.py**: versions.json management
  - `ComponentConfig.get_locked_version()`: Read recommended versions
  - `ComponentConfig.is_locked()`: Check lock state

- **vispctl/build.py**: Build with labels
  - `BuildManager.build_image()`: Adds git.commit label during build

### Git Commands Used

```bash
# Get current commit
git rev-parse HEAD

# Check for uncommitted changes
git status --porcelain

# Count commits between two refs
git rev-list --count <from>..<to>

# Get current branch
git rev-parse --abbrev-ref HEAD

# Check if commit is in git repo
git rev-parse --git-dir
```

### Podman Commands Used

```bash
# Check if image exists
podman image exists <image>:latest

# Get image label
podman inspect <image>:latest --format '{{.Labels.git.commit}}'

# Build with labels
podman build --label git.commit=<hash> \
             --label build.timestamp=<time> \
             -t <image>:latest .
```

## Best Practices

1. **Always check status before building**
   ```bash
   ./visp.py deploy status
   ./visp.py build <component>
   ```

2. **Lock versions in production**
   ```bash
   ./visp.py deploy lock --all
   ```

3. **Rebuild after significant source changes**
   ```bash
   # After git pull
   ./visp.py deploy status  # Check what's stale
   ./visp.py build all      # Rebuild affected
   ```

4. **Use version checks in build command**
   ```bash
   # Build will warn about version mismatches in production
   ./visp.py build session-manager
   ```

5. **Keep local repos clean**
   ```bash
   # Commit or stash changes before updating
   git stash
   ./visp.py deploy update
   git stash pop
   ```

## Related Documentation

- [VERSION_MANAGEMENT.md](VERSION_MANAGEMENT.md) - Lock/unlock/rollback workflows
- [BACKUP_RESTORE.md](BACKUP_RESTORE.md) - Backup and restore procedures

## Summary

The version checking system provides **forensic-level tracking** of your deployment:

✅ **Know exactly** what commit each image contains
✅ **See immediately** if source and images are out of sync
✅ **Track precisely** which repos are ahead/behind remotes
✅ **Verify easily** that production matches locked versions

This eliminates the guesswork from "do I need to rebuild?" and ensures consistent, reproducible deployments.
