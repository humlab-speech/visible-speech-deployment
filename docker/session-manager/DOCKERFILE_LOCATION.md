# Session Manager Build Support

**Note**: The Dockerfile for building the session-manager service container is located in the **session-manager repository itself** (`external/session-manager/Dockerfile`), not here.

## Why?

Following the "single source of truth" principle: services we control should own their build process. This allows:
- Independent development without this deployment project
- Standalone builds: `cd external/session-manager && docker build .`
- Version control of Dockerfile with the code it builds
- Reuse by other projects

## What's Here?

This directory contains session management **support files**:
- `jupyter-session/` - Jupyter notebook session template
- `rstudio-session/` - RStudio session template
- `vscode-session/` - VS Code session template
- `operations-session/` - Operations session template
- `build-*.sh` - Scripts for building session images
- Supporting configuration files

## Docker Compose Configuration

```yaml
session-manager:
  build:
    context: ./external/session-manager
    dockerfile: Dockerfile  # Uses external/session-manager/Dockerfile
```

## Legacy Note

The old Dockerfile that used to be here (which cloned from GitHub during build) has been archived to `ARCHIVE/docker-legacy/session-manager-Dockerfile`. It is no longer used.

## See Also

- `docs/FOLDER_STRUCTURE.md` - Explanation of directory structure
- `docs/DOCKERFILE_AUDIT.md` - Complete Dockerfile analysis
- `external/session-manager/` - The actual service code and Dockerfile
