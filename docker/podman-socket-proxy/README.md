# visp-podman-socket-proxy

A lightweight, zero-dependency Node.js proxy that sits between `session-manager` and
the real rootless Podman socket. It inspects every `POST .../containers/create` request
and rejects anything that falls outside a tight policy, while passing everything else
through transparently.

## Why it exists

`session-manager` needs the Podman socket to spawn, start, stop, and exec into session
containers on behalf of users. That is a large amount of trust: a compromised (or
buggy) `session-manager` could use the same socket to spawn an arbitrary container —
mounting any path on the host, using any image, adding dangerous Linux capabilities,
or running with host networking.

The proxy is the last line of defence. Even if `session-manager` is fully compromised,
an attacker can only create containers that match the policy below.

## Policy enforced on `containers/create`

Applies to both the Docker-compat (`POST /containers/create`) and the libpod
(`POST /libpod/containers/create`) API paths.

| Rule | What is checked |
|------|----------------|
| **Image allowlist** | `Image` must match `^localhost/visp-` or `^docker.io/library/alpine` |
| **No privileged** | `Privileged` / `HostConfig.Privileged` must be absent or `false` |
| **Mount allowlist** | Every bind-mount `Source` must start with `ABS_ROOT_PATH` (the project root) |
| **Cap allowlist** | `CapAdd` / `HostConfig.CapAdd` entries must be in: `CHOWN DAC_OVERRIDE FOWNER SETGID SETUID` |
| **No host networking** | `NetNS.nsmode` must be `"none"` or `"bridge"`; `HostPid`, `HostIpc`, `HostUts` must be absent or `false` |

Everything else (start, stop, exec, inspect, image pulls, etc.) passes through unchanged.

## Architecture

```
session-manager container
  │
  │  /var/run/docker.sock  (bind-mount from mounts/podman-proxy/podman.sock)
  ▼
podman-socket-proxy container      ← this service
  │  validates containers/create
  │  passes everything else through
  │  /run/podman/podman.sock  (real Podman socket, read-only bind-mount)
  ▼
rootless Podman daemon (host)
```

**Key security properties of the proxy container itself:**

- `Network=none` — no network access whatsoever
- `DropCapability=ALL` — no Linux capabilities
- `NoNewPrivileges=true` — cannot escalate
- Runs as container-root (UID 0 inside container = UID 1000 on host in rootless Podman),
  which is required to read the real Podman socket owned by the host user

## Environment variables

| Variable | Default | Description |
|----------|---------|-------------|
| `PODMAN_SOCKET` | `/run/user/1000/podman/podman.sock` | Path to the real Podman socket (inside container) |
| `PROXY_SOCKET` | `/run/podman-proxy/podman.sock` | Path where the proxy socket is created (inside container) |
| `ABS_ROOT_PATH` | _(required)_ | Absolute host path to the project root; all bind-mounts must be under this |
| `LOG_LEVEL` | `info` | `debug` logs every request; `info` logs only blocked requests and startup |

## Build

```bash
./visp.py build podman-socket-proxy
```

## Running / integration

The service is managed as a systemd Quadlet unit:

```bash
./visp.py install [--mode dev|prod]   # renders and installs all quadlets
./visp.py reload                      # systemctl --user daemon-reload
./visp.py start podman-socket-proxy

# Status and logs
./visp.py status
./visp.py logs podman-socket-proxy -f

# Restart (e.g. after a rebuild)
./visp.py restart podman-socket-proxy
```

`session-manager` has `After=podman-socket-proxy.service` and
`Requires=podman-socket-proxy.service` in its quadlet, so it will not start until the
proxy socket is ready.

## Testing the proxy

After installation you can exercise the policy rules directly with `curl`:

```bash
SOCK=mounts/podman-proxy/podman.sock

# Should pass through (list containers)
curl --unix-socket $SOCK http://d/v4.0.0/libpod/containers/json | jq length

# Should be BLOCKED — bad image
curl --unix-socket $SOCK -X POST http://d/v4.0.0/libpod/containers/create \
  -H 'Content-Type: application/json' \
  -d '{"image":"docker.io/library/ubuntu:latest"}' | jq .

# Should be BLOCKED — privileged
curl --unix-socket $SOCK -X POST http://d/v4.0.0/libpod/containers/create \
  -H 'Content-Type: application/json' \
  -d '{"image":"localhost/visp-jupyter-session:latest","privileged":true}' | jq .

# Should be BLOCKED — mount outside project root
curl --unix-socket $SOCK -X POST http://d/v4.0.0/libpod/containers/create \
  -H 'Content-Type: application/json' \
  -d '{"image":"localhost/visp-jupyter-session:latest","mounts":[{"type":"bind","source":"/etc/passwd","destination":"/etc/passwd"}]}' | jq .
```

## Source

`proxy.js` — ~285 lines of pure Node.js (built-in `http` and `net` modules only, no npm
dependencies). The `Dockerfile` uses `node:22.22.3-alpine3.22` and does **not** call
`npm install`.
