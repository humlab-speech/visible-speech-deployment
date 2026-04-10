# Session Manager Build Support

**Note**: The Dockerfile for building the session-manager service container is located in the **session-manager repository itself** (`external/session-manager/Dockerfile`), not here.

## Why?

Following the "single source of truth" principle: services we control should own their build process. This allows:
- Independent development without this deployment project
- Standalone builds: `cd external/session-manager && podman build .`
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

## Build Configuration

Built via `visp.py` using the Dockerfile in the session-manager repository:

```bash
./visp.py build session-manager
```

The build context is `external/session-manager/` with `external/session-manager/Dockerfile`.

## Legacy Note

The old Dockerfile that used to be here (which cloned from GitHub during build) has been archived to `ARCHIVE/docker-legacy/session-manager-Dockerfile`. It is no longer used.

## See Also

- `AGENTS.md` - Comprehensive project architecture reference
- `external/session-manager/` - The actual service code and Dockerfile
