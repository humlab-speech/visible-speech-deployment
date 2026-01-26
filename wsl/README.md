# WSL Development Bridge

**⚠️ WSL DEVELOPMENT ONLY - DO NOT USE IN PRODUCTION**

This directory contains a lightweight nginx reverse proxy to bridge Windows networking
to WSL2 Podman containers. This solves the issue where Windows `127.0.0.1` cannot reach
containers running in WSL2.

## What It Does

- Runs nginx container via **Docker Desktop** (uses its Windows networking magic)
- Docker Desktop forwards ports 80/443 to Windows `127.0.0.1`
- nginx proxies to Podman containers via WSL IP
- Allows `visp.local` in Windows hosts file to work with `127.0.0.1`

## Architecture

```
Windows Browser (127.0.0.1:443)
    ↓ (Docker Desktop port forwarding)
Docker: nginx container (--add-host systemd-apache:172.29.x.x)
    ↓ (WSL IP)
Podman: Apache container (systemd-apache:80)
```

Why Docker for the bridge?
- Docker Desktop automatically forwards container ports to Windows localhost
- Podman in WSL2 doesn't have this feature
- Main services still use Podman (production parity)

## Usage

```bash
# Start the bridge
./wsl/start-wsl-bridge.sh

# Stop the bridge
docker stop wsl-nginx-bridge
docker rm wsl-nginx-bridge
```

**Prerequisites:**
- Docker Desktop for Windows installed and running
- Main VISP services running on Podman (via systemd or quadlets)

## Production Deployment

**On production Linux servers**, this bridge is NOT needed:
- Use Quadlets directly (already configured in `quadlets/`)
- Use native nginx on the host OS as reverse proxy
- Containers can bind directly to 80/443

## Architecture

```
Windows Browser (127.0.0.1:443)
    ↓
WSL2: nginx container (0.0.0.0:443 → systemd-apache:80)
    ↓
WSL2: Apache Podman container (systemd-apache:80)
```
