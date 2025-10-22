# EMU-webApp Docker Container

## Overview

This container serves the EMU-webApp, a legacy web application for phonetic and speech analysis. The application uses outdated dependencies (AngularJS, old Node.js modules) that are no longer maintained.

## Security Model

**⚠️ IMPORTANT: This is a legacy application with known security vulnerabilities.**

### Current Security Posture

This container implements a **defense-in-depth** security model:

1. **Multi-stage build**: Separates build-time dependencies from runtime
2. **Minimal runtime image**: Uses `node:18-slim` to reduce attack surface
3. **Static file serving only**: Uses `npx serve` - a simple, audited static file server
4. **No application backend**: All logic runs client-side in the browser

### Recommended Deployment Security

When deploying this container, you **MUST** implement the following security measures:

#### Container Hardening
```bash
docker run \
  --read-only \
  --security-opt=no-new-privileges \
  --cap-drop=ALL \
  --user 1000:1000 \
  --network internal-only \
  -p 9000:9000 \
  emu-webapp
```

#### Required Security Layers

1. **Rootless container**: Prefer running Docker without root or with a controlled user; ensure container runtime is configured securely
2. **Read-only filesystem**: `--read-only` flag prevents file modifications
3. **No privilege escalation**: `--security-opt=no-new-privileges`
4. **Minimal capabilities**: `--cap-drop=ALL`
5. **Network isolation**: Only expose port 9000, no outbound internet access
6. **Reverse proxy with CSP headers**: See below

#### Content Security Policy (CSP)

Add these headers via your reverse proxy (nginx/Apache/Traefik):

```nginx
add_header Content-Security-Policy "default-src 'self'; script-src 'self' 'unsafe-eval'; style-src 'self' 'unsafe-inline'; object-src 'none'; base-uri 'self'; form-action 'self';" always;
add_header X-Content-Type-Options "nosniff" always;
add_header X-Frame-Options "DENY" always;
add_header X-XSS-Protection "1; mode=block" always;
add_header Referrer-Policy "no-referrer" always;
```

### Known Security Issues

- **XSS vulnerabilities**: Old AngularJS version has known Cross-Site Scripting vulnerabilities
- **Outdated dependencies**: Multiple npm packages with disclosed CVEs
- **Client-side attack surface**: All security risks exist in the user's browser

### Attack Surface Analysis

#### ✅ Server-Side (Very Low Risk)
- **No RCE risk**: Only static file serving, no executable application logic
- **No injection attacks**: No database, no dynamic content generation
- **Path traversal**: Mitigated by `serve` utility's built-in protections

## Build Instructions

```bash
# From the visible-speech-deployment root directory
cd docker/emu-webapp

# Place EMU-webApp source code in this directory
# (Already done if following standard deployment)

# Build the image
docker build -t emu-webapp:latest .
```

## Container Architecture

### Stage 1: Builder
- Base: `node:18-bookworm` (full Debian environment)
- Installs: Ruby, Sass, Compass (legacy build tools)
- Process: `npm ci` for reproducible builds
- Output: Compiled static files in `/app/dist`

### Stage 2: Final
- Base: `node:18-slim` (minimal Debian)
- Contents: Only production node_modules and compiled dist
- Server: `npx serve` static file server
- Port: 9000

## Why This Approach?

### The "Vending Machine" Model

Unlike a traditional web application (bank teller), this container is a **vending machine**:

- It dispenses pre-packaged products (static HTML/CSS/JS)
- It has no complex logic to exploit
- Server-side attacks (RCE, SQLi) are virtually impossible
- All security concerns are client-side (user's browser)

### Why Not Update the Code?

Updating EMU-webApp dependencies is not feasible because:

1. AngularJS is end-of-life (no security patches)
2. Build toolchain uses deprecated Ruby gems (Compass, Sass)
3. Original codebase is unmaintained

## Maintenance

### Updating the Container

```bash
# Rebuild with latest Node.js security patches
docker build --no-cache -t emu-webapp:latest .
```

### Monitoring

Monitor logs for:
- Unusual access patterns
- Path traversal attempts
- High request volumes (potential DoS)

```bash
docker logs -f emu-webapp
```

## Files in This Directory

- `Dockerfile` - Multi-stage container build
- `README.md` - This file
- `EMU-webApp/` - **DO NOT COMMIT** (cloned from external repository)

## References

- [EMU-webApp Repository](https://github.com/IPS-LMU/EMU-webApp)
- [npx serve Documentation](https://github.com/vercel/serve)
- [Docker Security Best Practices](https://docs.docker.com/engine/security/security/)

## License

EMU-webApp is licensed under Apache License 2.0. See the upstream repository for details.
