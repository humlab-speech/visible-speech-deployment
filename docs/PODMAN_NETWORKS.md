# Podman Networks in VISP Deployment

## Environment
- **WSL2** with systemd enabled
- **Podman 4.6.2** with **CNI backend** (not netavark)
- Quadlet files for systemd service generation

## Network Architecture

```
visp-net (bridge, DNS enabled)
├── mongo
├── session-manager ──┐
├── apache           │
├── traefik          │
└── wsrng-server     │
                     │
whisper-net (bridge) │
├── whisper          │
└── session-manager ─┘  (multi-homed)
```

**Intent:** Whisper should be isolated from the internet for security (processes untrusted audio files).

## Issue: DNS Resolution Fails Across Networks

### Problem
Session-manager connects to both `visp-net` and `whisper-net` but could not resolve `whisper` hostname.

### Root Cause
1. **CNI backend limitation:** When a container connects to multiple networks, only the first network's DNS server is added to `/etc/resolv.conf`
2. **`Internal=true` disables DNS:** In CNI, setting `Internal=true` in a `.network` quadlet disables DNS resolution entirely for that network

### Symptoms
```bash
# From session-manager container:
$ getent hosts mongo
10.89.1.2       mongo.dns.podman   # ✓ Works (visp-net is first)

$ getent hosts whisper
# (empty - fails)                   # ✗ Fails (whisper-net DNS not queried)
```

### What We Tried

1. **`--network-alias` in PodmanArgs** - Did not help with CNI's DNS limitation
2. **`ContainerName=` in quadlets** - Correct approach, gives clean DNS names
3. **`--dns=10.89.0.1 --dns=10.89.1.1` in PodmanArgs** - Adds both DNS servers, but CNI still doesn't query both properly
4. **Removing `Internal=true`** - **This worked** - DNS now resolves

## Current Workaround

Removed `Internal=true` from `whisper-net.network`:

```ini
[Network]
# Internal=true disabled to enable DNS resolution with CNI backend
# Re-enable when switching to netavark
```

**Trade-off:** Whisper is no longer fully isolated from the internet. The container can make outbound connections.

## Proper Solution: Switch to Netavark

Netavark (with Aardvark-DNS) handles multi-network DNS correctly and supports `Internal=true` with DNS.

### Migration Steps

```bash
# 1. Install netavark
sudo apt install podman-netavark aardvark-dns

# 2. Configure backend in ~/.config/containers/containers.conf
[network]
network_backend = "netavark"

# 3. Reset podman (WARNING: deletes all containers, images, networks)
podman system reset

# 4. Rebuild images and recreate networks
# Networks will now use netavark and Internal=true will work with DNS
```

### After Migration

Restore `Internal=true` in `whisper-net.network`:
```ini
[Network]
Internal=true
```

With netavark:
- `Internal=true` blocks internet access
- DNS still works between containers on the same network
- Multi-homed containers can resolve names on all connected networks

## Quadlet Container Naming

Always use `ContainerName=` in `.container` files for predictable DNS names:

```ini
[Container]
ContainerName=whisper    # DNS name will be "whisper"
Image=...
Network=whisper-net.network
```

Without this, Podman generates names like `systemd-whisper` which is less clean.

## Alternative Isolation Approaches

If netavark migration isn't possible, consider:

1. **Firewall rules inside container** - Block outbound traffic with iptables
2. **Network policy via host** - Use iptables/nftables on WSL host to block whisper's IP
3. **Proxy-only access** - Route whisper through a proxy that blocks external requests

## Verification Commands

```bash
# Check network backend
podman info | grep networkBackend

# Check network DNS status
podman network inspect systemd-whisper-net --format 'DNS={{.DNSEnabled}} Internal={{.Internal}}'

# Test DNS from container
podman exec session-manager getent hosts whisper

# Test connectivity
podman exec session-manager curl -s --connect-timeout 3 http://whisper:7860/
```
