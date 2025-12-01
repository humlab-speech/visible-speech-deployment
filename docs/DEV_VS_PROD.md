# Development vs Production Mode

**Date**: December 1, 2025
**Project**: VISP Deployment

This document explains the key differences between development and production deployment modes.

---

## Quick Reference

| Aspect | Development (`dev`) | Production (`prod`) |
|--------|---------------------|---------------------|
| **Compose File** | `docker-compose.dev.yml` | `docker-compose.prod.yml` |
| **Reverse Proxy** | Traefik (with dashboard) | Direct Apache/Nginx on host |
| **Source Code** | Mounted from `external/` | Baked into images |
| **Hot Reload** | ✅ Yes (nodemon, etc.) | ❌ No |
| **Code Changes** | Instant (on save) | Requires rebuild + restart |
| **SSL/TLS** | Self-signed certs | Let's Encrypt / proper certs |
| **Authentication** | `TEST_USER_LOGIN_KEY` bypass | SWAMID/Shibboleth required |
| **Logging** | Verbose (LOG_LEVEL=debug) | Production level |
| **Container Restart** | `unless-stopped` | `always` |
| **Permissions** | Run as regular user OK | May need sudo |
| **External Folder** | Required at runtime | Only needed for build |
| **Performance** | Lower (mounted volumes) | Higher (baked code) |
| **Debugging** | Easy (live code edit) | Harder (requires rebuild) |

---

## Detailed Differences

### 1. Source Code Handling

#### Development Mode
```yaml
# docker-compose.dev.yml
session-manager:
  volumes:
    # Source code mounted for hot-reload
    - "./external/session-manager:/session-manager:Z"
```

**Behavior**:
- Source code in `external/session-manager/` is **mounted** into the container
- Edit `external/session-manager/src/index.js` → changes reflected immediately
- Nodemon/file watchers restart the service automatically
- No rebuild needed for code changes

**Workflow**:
```bash
# Make changes
vim external/session-manager/src/index.js

# Changes take effect immediately (nodemon restarts)
# No rebuild needed!
```

#### Production Mode
```yaml
# docker-compose.prod.yml
session-manager:
  volumes:
    # Only data/log mounts - NO source code
    - "./mounts/session-manager/session-manager.log:/session-manager/logs/session-manager.log:Z"
```

**Behavior**:
- Source code is **copied** from `external/session-manager/` during `docker compose build`
- Code is frozen inside the Docker image
- Editing `external/session-manager/` has no effect on running containers
- Must rebuild image and recreate containers to deploy changes

**Workflow**:
```bash
# Make changes
vim external/session-manager/src/index.js

# Rebuild image with new code
docker compose -f docker-compose.prod.yml build session-manager

# Recreate container
docker compose -f docker-compose.prod.yml up -d session-manager

# Or rebuild and restart everything
docker compose -f docker-compose.prod.yml build
docker compose -f docker-compose.prod.yml up -d
```

**Important**: After production images are built, you could delete the entire `external/` folder and everything would still run (though you'd lose the ability to rebuild without re-cloning).

---

### 2. Reverse Proxy / Routing

#### Development Mode
```
Internet → Nginx (host) → Traefik (Docker) → Apache (Docker) → Services
```

**Components**:
- **Traefik** runs as a service in Docker Compose
- Provides dashboard at port 8080 (if enabled)
- Handles routing rules via Docker labels
- SSL/TLS with self-signed certificates
- Hot-reload of routing configuration

**Access**:
```bash
# Traefik dashboard
http://localhost:8080

# Services routed through Traefik
https://visp.local/
```

#### Production Mode
```
Internet → Nginx (host) → Apache (Docker) → Services
```

**Components**:
- **No Traefik** - direct routing to Apache
- Apache handles all routing and SSL/TLS
- Let's Encrypt certificates via host Nginx
- More efficient (one less proxy layer)

**Access**:
```bash
# Production domain
https://visp.pdf-server.humlab.umu.se/
```

---

### 3. Authentication

#### Development Mode

**Authentication Bypass Available**:
```bash
# .env
TEST_USER_LOGIN_KEY=dev-secret-key-12345
```

**Usage**:
```bash
# Login without Shibboleth
curl -X POST https://visp.local/api/login \
  -d "testUserLoginKey=dev-secret-key-12345" \
  -d "email=test@example.com" \
  -d "givenName=Test" \
  -d "sn=User"
```

**Purpose**:
- Bypass SWAMID/Shibboleth for local development
- Test features without authentication complexity
- Faster development iteration

**Security**: Only enable in development, never in production!

#### Production Mode

**Full Shibboleth Authentication**:
- SWAMID federation integration required
- Apache mod_shib handles authentication
- Users must authenticate via their institution
- No bypass available (TEST_USER_LOGIN_KEY ignored)

**Setup Requirements**:
- Register service with SWAMID
- Configure Shibboleth SP metadata
- Install valid SSL certificates
- Configure attribute release policies

---

### 4. Environment Configuration

#### Development Mode

**Key Settings**:
```bash
# .env or docker-compose.dev.yml
DEVELOPMENT_MODE=true
LOG_LEVEL=debug
SESSION_MANAGER_KEEP_CONTAINERS=true
TEST_USER_LOGIN_KEY=dev-secret-key-12345
```

**Characteristics**:
- Verbose logging for debugging
- Keep session containers after tests
- Authentication bypass enabled
- Relaxed security for convenience

#### Production Mode

**Key Settings**:
```bash
# .env or docker-compose.prod.yml
DEVELOPMENT_MODE=false
LOG_LEVEL=info
# SESSION_MANAGER_KEEP_CONTAINERS - not set (defaults to false)
# TEST_USER_LOGIN_KEY - must not be set
```

**Characteristics**:
- Minimal logging (info/warn/error only)
- Automatic cleanup of test containers
- Strict authentication required
- Production-grade security

---

### 5. Container Restart Policies

#### Development Mode
```yaml
restart: unless-stopped
```
- Containers restart automatically if they crash
- Won't restart if you manually stop them (`docker compose stop`)
- Good for development - keeps things running but respects manual stops

#### Production Mode
```yaml
restart: always
```
- Containers restart automatically if they crash
- Restart even if manually stopped (after system reboot)
- Ensures services are always available
- Critical for production uptime

---

### 6. SSL/TLS Certificates

#### Development Mode

**Self-signed certificates**:
```bash
# Generated by visp_deploy.py
openssl req -x509 -nodes -days 365 -newkey rsa:2048 \
  -keyout certs/visp.local/cert.key \
  -out certs/visp.local/cert.crt
```

**Characteristics**:
- Browser security warnings (must accept)
- Not trusted by browsers by default
- Quick to generate
- Fine for local development

#### Production Mode

**Let's Encrypt certificates**:
- Managed by host Nginx or Certbot
- Automatically renewed
- Trusted by all browsers
- Required for SWAMID federation

**Setup**:
```bash
# On production server
certbot --nginx -d visp.pdf-server.humlab.umu.se
```

---

### 7. File Permissions

#### Development Mode

**Relaxed permissions**:
- Can run as regular user (no sudo)
- Warning shown but installation continues
- File permissions are more lenient
- Good enough for single-user development

**visp_deploy.py output**:
```
⚠️  Running as regular user - some operations may be skipped
   This is fine for development/demo deployments
```

#### Production Mode

**Strict permissions**:
- Should run with sudo for proper ownership
- Container users (www-data, node, etc.) need correct permissions
- Mounted volumes need specific ownership
- Important for multi-user environments

**Recommended**:
```bash
sudo python3 visp_deploy.py install --mode=prod
```

---

### 8. Database Configuration

Both modes use the same MongoDB setup, but:

#### Development Mode
- MongoDB exposed on `127.0.0.1:27017` for local access
- Mongo Express available at `127.0.0.1:8081` for database browsing
- Easier to inspect and debug database

#### Production Mode
- MongoDB **NOT** exposed externally (only Docker network)
- Mongo Express disabled or restricted
- Database only accessible from within Docker network
- More secure

---

### 9. External Dependencies at Runtime

#### Development Mode

**Required**:
- `external/` folder must exist
- Source code must be present
- Changes to `external/` immediately affect running services

**Can modify**:
```bash
# Edit code
vim external/session-manager/src/index.js

# See changes immediately
docker compose logs -f session-manager
```

#### Production Mode

**Not required after build**:
- `external/` folder only needed during `docker compose build`
- After images are built, `external/` can be deleted
- Running containers don't access `external/` at all

**Workflow**:
```bash
# Build images
docker compose -f docker-compose.prod.yml build

# At this point, external/ is copied into images
# Could delete external/ now and services would still run

# Start services
docker compose -f docker-compose.prod.yml up -d

# external/ folder not mounted or accessed
```

---

### 10. Performance Differences

#### Development Mode

**Overhead sources**:
- Volume mounts (host → container filesystem access)
- File watchers (nodemon, webpack-dev-server)
- Verbose logging
- Traefik proxy layer

**Impact**: 10-30% slower than production, but development features are worth it

#### Production Mode

**Optimizations**:
- No volume mounts (code is in image filesystem)
- No file watchers
- Minimal logging
- One less proxy layer (no Traefik)
- Optimized builds (minified JS, production npm packages)

**Impact**: Full production performance

---

## Switching Between Modes

### Starting Development Mode
```bash
# Use default docker-compose.yml (symlink to dev) or explicit:
docker compose -f docker-compose.dev.yml up -d

# Or if docker-compose.yml is symlinked to dev:
docker compose up -d
```

### Starting Production Mode
```bash
# Must use explicit file
docker compose -f docker-compose.prod.yml build
docker compose -f docker-compose.prod.yml up -d
```

### Using visp_deploy.py
```bash
# Development installation (default)
python3 visp_deploy.py install
python3 visp_deploy.py install --mode=dev

# Production installation
python3 visp_deploy.py install --mode=prod
```

---

## Configuration Files by Mode

### Development
```
docker-compose.yml → docker-compose.dev.yml (symlink)
.env (with TEST_USER_LOGIN_KEY set)
certs/visp.local/ (self-signed)
```

### Production
```
docker-compose.prod.yml (explicit use required)
.env (without TEST_USER_LOGIN_KEY)
certs/letsencrypt/ (proper certificates)
/etc/shibboleth/ (on host for Apache mod_shib)
```

---

## Best Practices

### Development
1. ✅ Use `docker-compose.dev.yml` (or default `docker-compose.yml`)
2. ✅ Enable `TEST_USER_LOGIN_KEY` for auth bypass
3. ✅ Set `LOG_LEVEL=debug` for detailed logs
4. ✅ Keep source code in `external/` for editing
5. ✅ Use Traefik dashboard to debug routing
6. ✅ Run as regular user (no sudo needed)
7. ✅ Accept self-signed certificate warnings

### Production
1. ✅ Use `docker-compose.prod.yml` explicitly
2. ✅ Never set `TEST_USER_LOGIN_KEY`
3. ✅ Use `LOG_LEVEL=info` or `warn`
4. ✅ Run with `sudo` for proper permissions
5. ✅ Register with SWAMID federation
6. ✅ Use Let's Encrypt certificates
7. ✅ Disable Mongo Express or restrict access
8. ✅ Don't expose MongoDB port externally
9. ✅ Rebuild images for code changes
10. ✅ Test in dev before deploying to prod

---

## Troubleshooting

### "My code changes aren't showing up!"

**Dev mode**: Check that volume mount exists in `docker-compose.dev.yml` and nodemon is running
**Prod mode**: This is expected! You must rebuild the image and recreate the container

### "Authentication isn't working!"

**Dev mode**: Make sure `TEST_USER_LOGIN_KEY` is set in `.env`
**Prod mode**: Ensure Shibboleth is configured and SWAMID metadata is correct

### "Services won't start after reboot!"

**Dev mode**: `unless-stopped` policy won't restart manually stopped containers
**Prod mode**: `always` policy should restart everything - check `docker compose ps`

### "Performance is slow!"

**Dev mode**: This is expected due to volume mounts and file watchers
**Prod mode**: If slow, check resource limits and optimize Docker images

---

## Migration Path: Dev → Prod

1. **Test in dev first**: Ensure everything works with `docker-compose.dev.yml`
2. **Remove test auth**: Delete `TEST_USER_LOGIN_KEY` from `.env`
3. **Update certificates**: Install Let's Encrypt certs
4. **Configure Shibboleth**: Set up SWAMID integration
5. **Build production images**: `docker compose -f docker-compose.prod.yml build`
6. **Update Nginx/Apache**: Point to production domain
7. **Deploy**: `docker compose -f docker-compose.prod.yml up -d`
8. **Monitor**: Check logs for errors
9. **Test authentication**: Verify SWAMID login works
10. **Verify services**: Test all functionality

---

## Summary

The key philosophical difference:

- **Development**: Optimize for **speed of iteration** - quick changes, easy debugging, convenience over security
- **Production**: Optimize for **stability and security** - frozen code, strict auth, performance, uptime

Choose the right mode for your needs, and never use development shortcuts in production!
