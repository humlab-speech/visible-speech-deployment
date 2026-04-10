# WSRNG Server Build Support

**Note**: The Dockerfile for building the wsrng-server service container is located in the **wsrng-server repository itself** (`external/wsrng-server/Dockerfile`), not here.

## Why?

Following the "single source of truth" principle: services we control should own their build process. This allows:
- Independent development without this deployment project
- Standalone builds: `cd external/wsrng-server && podman build .`
- Version control of Dockerfile with the code it builds
- Reuse by other projects

## Build Configuration

Built via `visp.py` using the Dockerfile in the wsrng-server repository:

```bash
./visp.py build wsrng-server
```

The build context is `external/wsrng-server/` with `external/wsrng-server/Dockerfile`.

## Legacy Note

The old Dockerfile that used to be here (which cloned from GitHub during build) has been archived to `ARCHIVE/docker-legacy/wsrng-server-Dockerfile`. It is no longer used.

The modern Dockerfile in the wsrng-server repo uses:
- Multi-stage build for smaller image size
- Alpine base for efficiency
- Runs as `node` user for security
- Proper layer caching

## See Also

- `AGENTS.md` - Comprehensive project architecture reference
- `external/wsrng-server/` - The actual service code and Dockerfile
