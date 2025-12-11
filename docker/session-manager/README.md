# Docker Session Containers

This directory contains Dockerfiles for **user session containers** that are launched by the session-manager service.

## What it builds:

- `visp-operations-session` - Base operations/analysis environment (built first, contains R libraries)
- `visp-rstudio-session` - RStudio environment (inherits from operations-session)
- `visp-jupyter-session` - Jupyter Notebook environment (copies from operations-session)

## Important Distinction:

This is **NOT** for building the session-manager service itself. The session-manager service is built from:
- **Source**: `external/session-manager/` directory
- **Dockerfile**: `docker/session-manager/Dockerfile`

## Usage:

Use `visp_deploy.py` to build session images:

```bash
# Build all session images (no cache by default)
python3 visp_deploy.py build

# Build all with cache
python3 visp_deploy.py build --cache

# Build specific images
python3 visp_deploy.py build operations
python3 visp_deploy.py build rstudio jupyter

# Build is also part of update
python3 visp_deploy.py update
```

**Note**: Operations session must be built first since rstudio and jupyter depend on it. The build system handles this automatically.

## Directory Structure:

- `operations-session/` - Base session with R libraries and container-agent
- `rstudio-session/` - RStudio interface (inherits from operations-session)
- `jupyter-session/` - Jupyter notebook interface (copies from operations-session)
- `files/` - Shared files used by session containers
- `project-template-structure/` - Default project structure for new sessions

## Build Process:

1. **Prepare**: Copy `external/container-agent` into build context
2. **Build operations-session**: Builds container-agent with Node.js, installs R packages
3. **Build rstudio-session**: Inherits from operations-session, adds RStudio
4. **Build jupyter-session**: Starts from Jupyter base, copies R libs and container-agent from operations-session
5. **Cleanup**: Remove temporary container-agent copy
