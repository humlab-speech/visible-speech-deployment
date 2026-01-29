# VISP Deployment TODO

## High Priority

- [ ] **Uploads failing in Podman (high priority)** ⚠️
  - **Why**: PHP upload handler fails to create/move uploaded files into the mounted upload staging path (`/tmp/uploads` → host `mounts/apache/apache/uploads`). Observed `mkdir(): Permission denied` and `move_uploaded_file(): Unable to move ...` in Apache PHP error logs (example: 2026-01-29 10:07:32 for upload id `p1aLa0PMC8UEsjcwMsE8o`). Probe tests show the Apache worker (`www-data`) cannot write into per-upload directories (owner-only directories and some created with owner UID `100032`). After a temporary `chmod -R 777` retry, files still didn't appear (likely a new upload id or client-side failure), so uploads are effectively failing in this Podman environment.
  - **Where to look (logs & paths)**:
    - Apache PHP error log: `mounts/apache/apache/logs/apache2/visp.local-error.log` (search for `mkdir(): Permission denied` / `move_uploaded_file`)
    - Apache access log: `mounts/apache/apache/logs/apache2/visp.local-access.log` (search for `POST /api/v1/upload`)
    - Upload staging path: `mounts/apache/apache/uploads/<gitlabUserId>/<upload-id>/...` (bind-mounted into container at `/tmp/uploads`)
    - Session-manager logs: use `./visp-logs.sh session-manager` — look for `Converting all files in /tmp/uploads` and `No audio files found` messages
  - **Immediate mitigation / recommended fix**:
    - Make upload tree group-owned by `www-data` and group-writable (e.g., `chgrp -R www-data mounts/apache/apache/uploads && find mounts/apache/apache/uploads -type d -exec chmod 2775 {} + && find mounts/apache/apache/uploads -type f -exec chmod 664 {} +`)
    - Ensure PHP/webapi creates directories with group-write (e.g., `mkdir(..., 0o2775)` or set proper umask) and consider using setgid for inheritance
    - Improve webapi error propagation so move failures return meaningful errors to the UI instead of silent failures
  - **Acceptance / next steps**:
    - Reproduce an upload while tailing Apache access+error logs and session-manager logs; capture the new upload id and verify files are present on the host
    - Add an automated test/CI check to verify uploads succeed under Podman with the configured mounts
    - Prioritize as **High** — blocks user uploads in Podman deployments

- [ ] **Document netavark requirement in installation guides** 📝 URGENT
  - **Why**: New installations need netavark, CNI has critical DNS bugs (20s timeouts)
  - **Status**: Netavark migration successful on dev (2026-01-28), automation added
  - **What to update**:
    - [x] README.md - Added netavark prerequisite and migration info
    - [x] visp-podman.py - Added automatic netavark detection and configuration
    - [x] visp-podman.py - Added automatic network creation (quadlets don't auto-create)
    - [ ] DEPLOYMENT_GUIDE.md - Add detailed migration procedure
    - [ ] docs/QUICK_REFERENCE.md - Update network troubleshooting section
  - **Automation Features**:
    - ✅ Detects CNI backend and offers migration
    - ✅ Configures netavark in containers.conf
    - ✅ Prompts for podman system reset (warns about container removal)
    - ✅ Auto-creates networks (systemd-visp-net, systemd-whisper-net, systemd-octra-net)
    - ✅ Validates network backend before installation
  - **Testing**:
    - [ ] Test fresh install with netavark pre-installed
    - [ ] Test migration from CNI (with backup/restore)
    - [ ] Verify network auto-creation works correctly

### Authentication & Access
- [ ] **Register production domain with SWAMID IdP**
  - Domain: `visp.pdf-server.humlab.umu.se`
  - Required files: `shibboleth2.xml`, `attribute-map.xml`, `swamid-idp.xml`
  - Contact: Umeå University IT department
  - Issue: Currently using TEST_USER_LOGIN_KEY workaround for authentication

### Matomo Analytics Integration
- [ ] **Re-enable Matomo tracking** (currently stashed in git)
  - Stash contains: `matomo-tracker.js`, docker volume mounts, documentation
  - Location: Check `git stash list` for "WIP: Matomo tracking implementation"
  - Approach: Add `<script src="/matomo-tracker.js"></script>` to application templates
  - See: `docs/MATOMO_INTEGRATION.md` (also in stash)
  - Alternative: Consider using Traefik middleware or individual app integration

## Medium Priority

### Security Improvements
- [ ] **Implement socket proxy for container management**
  - **Current**: Direct socket mount (`/run/user/1000/podman/podman.sock:/var/run/docker.sock`)
    - session-manager and traefik have full access to Podman API
    - Security risk: compromised container = full control over host containers
  - **Recommendation**: Use socket proxy with restricted permissions
    - Example: [tecnativa/docker-socket-proxy](https://github.com/Tecnativa/docker-socket-proxy)
    - Or create custom proxy for Podman socket
    - Restrict API access to only required operations:
      - session-manager needs: `POST /containers/create`, `POST /containers/{id}/start`, `DELETE /containers/{id}`, `GET /containers/json`
      - traefik needs: `GET /containers/json` (read-only container discovery)
  - **Benefits**:
    - Principle of least privilege
    - Limits blast radius of container compromise
    - Can log/audit socket API calls
    - Standard security hardening for production
  - **Implementation**:
    - Deploy socket-proxy as separate container
    - Configure allowed API endpoints via environment variables
    - Update session-manager and traefik to use proxy socket instead
    - Test all container operations still work

- [x] **~~Consider moving emu-webapp-server Dockerfile to external repo~~** ✅ Done in master (commit 4e5a3f0)
  - ~~Current: Dockerfile is in `docker/emu-webapp-server/`~~
  - ~~Inconsistency: Other Humlab services (session-manager, wsrng-server) have Dockerfiles in their repos~~
  - ~~Recommendation: Follow "single source of truth" principle~~
  - ~~Benefits: Standalone buildability, version alignment with code~~
  - ~~See: `docs/DOCKERFILE_AUDIT.md` for analysis~~

- [ ] **Add version drift detection to visp-deploy.py**
  - ✅ Partially implemented: `visp-deploy.py status` now checks uncommitted changes
  - ✅ Shows repository status: clean, has changes, ahead/behind remote
  - [ ] TODO: Add explicit drift warnings with --strict flag
  - Check if external repos have uncommitted local changes
    - Use `git status --porcelain` to detect modified/staged files
    - Warn: "external/session-manager has uncommitted changes - deploy may not be reproducible"
  - Check how far behind/ahead repos are from remote
    - Use `git fetch` + `git rev-list --count origin/main..HEAD`
    - Warn: "external/wsrng-server is 3 commits behind origin/main"
    - Warn: "external/webclient has 2 unpushed local commits"
  - Compare deployed version vs versions.json
    - Current checked out commit vs `locked_version` field
    - Warn if mismatch: "external/session-manager is at abc123 but versions.json specifies def456"
  - Add `--strict` flag to fail on any drift (for CI/CD)
  - Already in `visp-deploy.py status` command (partially)

- [ ] **Audit Dockerfiles for version consistency**
  - Problem: Some Dockerfiles do `git clone` without specifying version
    - `docker/apache/Dockerfile` - clones webclient from main branch
    - Need to check all Dockerfiles for unversioned git clones
  - Solution approaches:
    1. **Pass version as build arg** (recommended)
       ```dockerfile
       ARG WEBCLIENT_VERSION=latest
       RUN git clone --branch ${WEBCLIENT_VERSION} https://github.com/...
       ```
       Then in docker-compose: `args: WEBCLIENT_VERSION: ${WEBCLIENT_VERSION}`
    2. **Use external/ as build context** (already doing for some services)
       - Relies on versions.json controlling what's checked out
       - More consistent with our "single source of truth" approach
  - Action: Create audit script to find all `git clone` in Dockerfiles
  - Document which Dockerfiles are version-locked and which aren't

- [ ] **Harden container security in quadlets** ⚠️ Important for production
  - **When**: After all services are verified working
  - **Goal**: Apply principle of least privilege to all containers
  - **Hardening checklist per service**:
    - [ ] Drop unnecessary Linux capabilities
      - Add `DropCapability=ALL` then selectively add back only needed caps
      - Example: Most containers don't need `CAP_NET_ADMIN`, `CAP_SYS_ADMIN`
      - session-manager needs container creation caps (keep current permissions)
    - [ ] Make filesystems read-only where possible
      - Add `ReadOnly=true` for containers that don't write to filesystem
      - Use `Volume=` with `:ro` suffix for read-only mounts
      - Example: whisper, octra likely don't need write access to container FS
    - [ ] Remove network access where not needed
      - Review which containers actually need network access
      - Example: mongo only needs internal network access (already isolated)
    - [ ] Add resource limits
      - `Memory=2G` for containers that may consume excessive memory
      - `CPUQuota=200%` to prevent CPU starvation
    - [ ] Run as non-root user where possible
      - Add `User=<uid>` to run container as specific user
      - Many images already support this (check Dockerfile USER directive)
  - **Testing approach**:
    - Apply hardening incrementally, one container at a time
    - Test full workflow after each change
    - Document any capabilities/permissions actually required

- [ ] **MAYBE: Proxy session container network traffic** 🤔 Consider for high-security environments
  - **Problem**: Jupyter/RStudio containers can access host LAN
    - Users could potentially probe internal network from session containers
    - Security risk in environments with sensitive internal services
  - **Proposed solution**: Network isolation proxy
    - Allow outgoing internet access (for pip/CRAN installs)
    - Block access to private IP ranges (10.0.0.0/8, 172.16.0.0/12, 192.168.0.0/16)
    - Block access to host machine and other containers
  - **Implementation options**:
    - **Option 1**: squid proxy with ACL rules
    - **Option 2**: Dedicated network with iptables/nftables filtering
    - **Option 3**: Podman network plugin with policy enforcement
  - **Trade-offs**:
    - ✅ Pro: Stronger isolation, defense in depth
    - ✅ Pro: Prevents lateral movement in case of container compromise
    - ❌ Con: More complex networking setup
    - ❌ Con: May break legitimate use cases (accessing internal APIs)
    - ❌ Con: Performance overhead from proxy
  - **Decision criteria**:
    - **High priority** if: Deployment on network with sensitive internal services
    - **Low priority** if: Development environment or isolated network segment
    - **Skip** if: Sessions are only used by trusted administrators

- [ ] **MAYBE: Reconsider dev mode build strategy for webclient** 🔨
  - **Current**: External build via `visp-podman.py build webclient`, mount dist/ directory
  - **Previous**: In-container `ng build --watch` for hot-reload during development
  - **Issue**: Permission conflicts between mounted files and container build process
    - Container running as root couldn't overwrite nobody:nogroup owned files
    - Caused 20-second delays on startup as build repeatedly failed
  - **Options**:
    1. Keep external build (current) - cleaner separation, explicit rebuilds
    2. Fix permissions and restore watch mode - better DX for active frontend dev
    3. Hybrid: env var to enable/disable watch mode based on workflow
  - **Decision**: Keeping external for now (removed watch from Dockerfile)

## High Priority

### Infrastructure Migration
- [ ] **Migrate to Podman Quadlets** (In Progress - branch: feature/podman-migration)
  - Benefits: systemd integration, rootless by default, better for production
  - Current Status: Phase 1 complete, investigating systemd on WSL
  - See: `dev-notes/BUILD_STRATEGY.md`

  **Environment Strategy**:
  - **Production deployment**: Use Podman Quadlets with systemd (standard approach)
  - **WSL development**: Two options:
    1. Enable systemd in WSL via `/etc/wsl.conf` to use Quadlets locally
    2. Use manual `podman` commands or shell scripts for development
  - **Note**: Quadlets require systemd running as PID 1 (not available in default WSL)

  **Docker Dependencies Found:**

  1. **Node.js session-manager (external/session-manager/)**
     - File: [src/SessionManager.class.js](external/session-manager/src/SessionManager.class.js#L18)
       - Line 18: `this.docker = new Docker({ socketPath: '/var/run/docker.sock' })`
       - Uses node-docker-api library to create/list/inspect/delete session containers
     - File: [src/Session.class.js](external/session-manager/src/Session.class.js#L30)
       - Line 30: `this.docker = new Docker({ socketPath: '/var/run/docker.sock' })`
       - Methods: createContainer(), delete(), runCommand(), commit()
     - File: [package.json](external/session-manager/package.json)
       - Dependency: `"node-docker-api": "^1.1.22"`
     - **Action**: Test node-docker-api compatibility with Podman socket
       - Podman provides Docker socket emulation at `/run/podman/podman.sock`
       - May need to update socket path or create compatibility symlink
       - Verify container operations: create, list, inspect, delete, exec

  2. **Docker Compose socket mounts**
     - Files: docker-compose.yml, docker-compose.dev.yml, docker-compose.prod.yml
     - Traefik service: `/var/run/docker.sock:/var/run/docker.sock` (for auto-config)
     - session-manager service: `/var/run/docker.sock:/var/run/docker.sock:Z` (for spawning sessions)
     - **Action**: Update socket paths for Podman
       - Option 1: Mount `/run/podman/podman.sock:/var/run/docker.sock`
       - Option 2: Enable Podman Docker-compatible socket and keep paths
       - Option 3: Use Podman socket API directly (requires code changes)

  3. **Shell scripts using docker CLI**
     - File: [cleanup-session-containers.sh](cleanup-session-containers.sh)
       - Line 19: `docker ps -a --filter "name=hsapp-session-" --filter "name=visp-session-"`
       - Multiple `docker ps`, `docker exec` commands
       - **Action**: Replace with `podman` commands or use `docker` alias
     - File: [test-whisper-fix.sh](test-whisper-fix.sh)
       - Lines 7, 23, 36, 50: `docker ps`, `docker exec` commands
       - **Action**: Replace `docker` → `podman` in scripts

  4. **Python deployment script (visp-deploy.py)**
     - File: [visp-deploy.py](visp-deploy.py)
       - Line 1154-1162: `docker run --rm -v {comp_path}:/app -w /app node:20 ...`
       - Used for building Node.js components in temporary containers
       - Multiple references to "Docker Compose" and Docker images
       - **Action**: Replace `docker run` → `podman run` in subprocess calls
       - **Action**: Update docker-compose references to podman-compose
       - **Action**: Test build workflow with Podman containers

  5. **Documentation and user messaging**
     - Multiple files reference "docker compose", "Docker images", etc.
     - README.md, docs/*.md contain Docker CLI examples
     - **Action**: Update documentation to reflect Podman usage
     - **Action**: Consider maintaining Docker compatibility notes

  **Migration Steps:**

  - [x] Phase 1: Socket compatibility testing ✅ COMPLETE
    - ✅ Tested on WSL (no systemd required)
    - ✅ Socket started with: `podman system service --time=0 unix://$HOME/.podman/podman.sock &`
    - ✅ node-docker-api 1.1.22 fully compatible with Podman socket
    - ✅ All operations verified: list, create, start, stop, inspect, exec, logs, delete
    - ✅ Test scripts created: [test-podman-socket.sh](test-podman-socket.sh), [test-podman-socket.js](test-podman-socket.js)
    - **Conclusion**: Session-manager code requires ZERO changes for Podman compatibility

  - [x] Phase 2: Enable systemd in WSL ✅ COMPLETE (with caveat)
    - ✅ Added systemd=true to `/etc/wsl.conf`
    - ✅ Restarted WSL - systemd now running as PID 1
    - ✅ Enabled user lingering: `sudo loginctl enable-linger $(whoami)`
    - ✅ Enabled Podman socket: `systemctl --user enable --now podman.socket`
    - ✅ Socket active at: `/run/user/1000/podman/podman.sock`
    - ⚠️  **BLOCKER**: Podman 3.4.4 does NOT support Quadlets (need 4.4+)
    - **Next**: Upgrade Podman to 4.4+ or use alternative approach

  - [x] Phase 3: Upgrade Podman ✅ COMPLETE
    - ✅ Upgraded from 3.4.4 → 4.6.2
    - ✅ Quadlet support verified: `/usr/libexec/podman/quadlet --version`
    - ✅ Test quadlet working: [test-quadlet-simple.container](test-quadlet-simple.container)
    - ✅ systemd integration confirmed
    - ⚠️  **WSL caveat**: Must use `PodmanArgs=--cgroups=disabled` due to cgroup limitations
    - **Note**: Production servers won't need --cgroups=disabled

  - [x] Phase 3b: Create initial Quadlet files ✅ IN PROGRESS
    - ✅ Test quadlet working: [test-quadlet-simple.container](test-quadlet-simple.container)
    - ✅ Created quadlets directory with .container and .network files
    - ✅ All 10 visp images migrated from Docker to Podman
    - ✅ docker-compose stopped
    - ✅ Quadlets created for: mongo, whisper, wsrng-server, session-manager
    - ✅ Networks created: visp-net, whisper-net, octra-net
    - ✅ Podman socket mounted at `/run/user/1000/podman/podman.sock:/var/run/docker.sock:Z`
    - ✅ **ALL CORE SERVICES RUNNING**:
      - ✅ mongo: Running with fresh data directory
      - ✅ whisper: Running successfully
      - ✅ wsrng-server: Running in production mode (no source mount)
      - ✅ session-manager: Running and connected to mongo, socket at :8020
    - **Solution**: Production quadlets without source mounts (use docker-compose for dev)
    - **Next**: Create remaining quadlets (apache, emu-webapp, emu-webapp-server, octra, mongo-express)

  - [x] **Phase 3c: Fix Podman Networking (CNI → Netavark)** ✅ COMPLETE - MIGRATED 2026-01-28
    - **RESOLVED**: Migrated to netavark, DNS now instant (0.02-0.07s vs 20-25s)
    - See: NETAVARK_MIGRATION_SUCCESS.md for full details
    - **What was fixed**:
      - CNI dnsname plugin DNS servers not running → netavark built-in DNS works
      - 20-25 second DNS timeouts → instant resolution
      - 1+ minute login delays → ~1 second
      - Internal=true broke DNS → now works perfectly
    - **Remaining work**:
      - [ ] Update installation guide with netavark requirement
      - [ ] Add network creation to visp-podman.py (quadlet .network files don't auto-create with netavark)
      - [ ] Test migration procedure on fresh install
      - [ ] Document in DEPLOYMENT_GUIDE.md
    - See: [docs/PODMAN_NETWORKS.md](docs/PODMAN_NETWORKS.md)
    - **CRITICAL ISSUE DISCOVERED**: CNI dnsname plugin DNS servers not running
      - **Impact**: Every DNS lookup takes 20-25 seconds (timeout)
      - **Symptoms**: 1+ minute login delays, slow page loads, timeouts everywhere
      - **Root Cause**: dnsmasq processes for CNI networks (10.89.1.1, 10.89.0.1) not running
        - Session-manager connects to mongo: 20s delay
        - Session-manager connects to whisper: 25s delay
        - Apache connects to session-manager: 20s delay
      - **Temporary Workaround Applied**: Added /etc/hosts entries to running containers
        - session-manager: `10.89.1.2 mongo`, `10.89.0.2 whisper`
        - apache: `10.89.1.5 session-manager`, `10.89.1.2 mongo`
        - **Result**: Instant DNS resolution (0.07s), app now fast
        - **Limitation**: Workaround lost on container restart, IPs may change
    - **Problem**: `Internal=true` networks disable DNS resolution with CNI backend
      - **whisper-net**: Multi-homed session-manager cannot resolve `whisper` hostname
        - Session-manager connects to both `visp-net` and `whisper-net`
        - CNI backend only adds first network's DNS to `/etc/resolv.conf`
      - **octra-net**: Apache cannot resolve `octra` hostname for proxying
        - Apache needs to proxy requests to `http://octra/`
        - `Internal=true` disables DNS even for single-network containers
    - **Current Workaround**: Removed `Internal=true` from both networks
      - Trade-off: Whisper and Octra are no longer isolated from internet
      - Security risk: containers can make external connections
      - BUT: DNS still broken even without Internal=true!
    - **Proper Fix**: Migrate from CNI to Netavark backend
      ```bash
      # 1. Install netavark
      sudo apt install podman-netavark aardvark-dns

      # 2. Configure in ~/.config/containers/containers.conf
      [network]
      network_backend = "netavark"

      # 3. Reset podman (WARNING: deletes all containers, images, networks)
      podman system reset

      # 4. Rebuild images and recreate networks
      ```
    - **After Migration**: Restore `Internal=true` in whisper-net.network and octra-net.network
    - **Alternative Mitigations** (if netavark not possible):
      - [ ] Firewall rules inside whisper/octra containers (iptables)
      - [ ] Network policy via host nftables
      - [ ] Proxy-only access pattern

  - [x] Phase 3d: Create unified management tool ✅ COMPLETE
    - ✅ Created `visp-podman.py` - Python replacement for visp-logs.sh
    - **Commands available**:
      - `visp-podman.py status` - Show service status, quadlet links, containers, networks
      - `visp-podman.py logs [service] [-f]` - View/follow logs
      - `visp-podman.py start/stop/restart [service|all]` - Service control
      - `visp-podman.py install/uninstall [service|all]` - Manage quadlet symlinks
      - `visp-podman.py reload` - Reload systemd daemon after quadlet changes
      - `visp-podman.py debug <service>` - Debug startup issues
      - `visp-podman.py shell <container>` - Open bash in container
      - `visp-podman.py exec <container> <cmd>` - Run command in container
      - `visp-podman.py network` - Show network backend and DNS status
    - [ ] TODO: Add `build` command integration with visp-deploy.py
    - [ ] TODO: Add `update` command to pull latest images
    - [ ] TODO: Consider adding bash completion
    - [ ] **Optional: Remove networks during `uninstall`** (`--remove-networks`)
      - **Why**: Provide an opt-in full teardown (useful for ephemeral/dev/CI) while avoiding accidental network removal on shared hosts.
      - **Pros**:
        - Clean teardown removes VISP-created networks (good for tests/CI)
        - Convenient for ephemeral environments and full uninstall workflows
      - **Cons**:
        - Risky if networks are shared with other projects or containers
        - Requires careful checks (attached containers) and confirmation to be safe
      - **Design / Implementation Notes**:
        - CLI flag: `--remove-networks` (alias `--prune-networks`)
        - Safety checks:
          - Only target known VISP networks: `systemd-visp-net`, `systemd-whisper-net`, `systemd-octra-net`
          - Inspect networks for attached containers before removing
          - Prompt for confirmation; accept `--force` to skip prompt
          - Abort if attached containers are found unless `--force` and explicit confirmation
        - Documentation: Update `docs/PODMAN_NETWORKS.md` and `README.md`
      - **Acceptance Criteria / Tests**:
        - Manual tests:
          - `uninstall --remove-networks` removes networks when none attached
          - `uninstall --remove-networks` aborts when containers attached unless `--force`
        - Unit tests: Mock podman calls to verify behavior
        - Help text and `--help` updated

  - [x] Phase 3e: Implement Podman Secrets ✅ COMPLETE
    - ✅ Created `.env.secrets` file for sensitive credentials (separate from `.env`)
    - ✅ Added `.env.secrets.template` for documentation
    - ✅ Updated `.gitignore` to exclude `.env.secrets`
    - ✅ Implemented `load_all_env_vars()` in visp-podman.py to merge both files
    - ✅ Created `get_derived_secrets()` to generate secrets from environment variables
    - ✅ Implemented `create_podman_secrets()` and `remove_podman_secrets()` functions
    - ✅ Updated `cmd_install()` to create Podman secrets automatically
    - ✅ Updated `cmd_uninstall()` to clean up secrets
    - **Secrets managed**:
      - `visp_mongo_root_password` → MONGO_ROOT_PASSWORD
      - `visp_api_access_token` → HS_API_ACCESS_TOKEN
      - `visp_test_user_login_key` → TEST_USER_LOGIN_KEY
      - `visp_mongo_uri` → MONGO_URI (derived secret)
      - `visp_media_file_base_url` → MEDIA_FILE_BASE_URL (derived secret)
    - ✅ All quadlets updated to use `Secret=` directives instead of hardcoded values
    - ✅ Security audit passed: no secrets hardcoded in container files
    - **Benefits**: Secure credential management, no passwords in quadlet files, Git-safe configuration

  - [x] Phase 3f: Quadlet architecture improvements ✅ COMPLETE
    - ✅ Switched from copying to symlinking quadlet files
      - `~/.config/containers/systemd/*.container` → symlinks to source files
      - Single source of truth: edit in `quadlets/dev/` or `quadlets/prod/`
    - ✅ Removed environment variable substitution (no longer needed)
    - ✅ Configuration split:
      - `.env` - Non-sensitive config (BASE_DOMAIN, ADMIN_EMAIL, etc.)
      - `.env.secrets` - Passwords and tokens (managed via Podman Secrets)
    - ✅ Updated visp-deploy.py to generate passwords to `.env.secrets`
    - ✅ All containers load config from `.env` via `EnvironmentFile=` directive
    - ✅ Secrets override config values via `Secret=` directives
    - **Security**: No container gets passwords it doesn't need

  - [x] Phase 3g: Naming consistency ✅ COMPLETE
    - ✅ Renamed `visp_deploy.py` → `visp-deploy.py` (consistent with visp-podman.py)
    - ✅ Used `git mv` to preserve history
    - ✅ Updated all references in documentation and scripts

  - [ ] Phase 4: Update scripts and tooling
    - Replace docker commands in .sh scripts
    - Update visp-deploy.py subprocess calls
    - Add Podman detection/validation
    - Test full deployment workflow

  - [ ] Phase 4: Implement Quadlets (Production target)
    - **Prerequisites**:
      - ✅ systemd running (enabled in WSL)
      - ⚠️  **Podman 4.4+ required** (current: 3.4.4)
      - Options to upgrade:
        1. Use Podman PPA: https://github.com/containers/podman/blob/main/install.md
        2. Build from source
        3. Use production server with newer Podman for Quadlet development
    - **Quadlet Implementation**:
      - Convert docker-compose services to .container/.network/.volume files
      - Place in `~/.config/containers/systemd/` (user) or `/etc/containers/systemd/` (system)
      - Podman automatically generates systemd units from quadlets
      - Set up service dependencies with `After=`, `Requires=`, `Wants=`
      - Configure pod networking with .network files
      - Use `systemctl --user daemon-reload` to load changes
      - Test systemd integration: `systemctl --user start visp-session-manager.service`
      - Enable auto-restart: systemd handles this automatically
    - **Benefits over docker-compose**:
      - Native systemd integration (service management, logging, dependencies)
      - Auto-restart on failure
      - Boot-time startup
      - Better resource limits and cgroups integration
      - Standard Linux service management

  - [ ] Phase 5: Documentation and cleanup
    - Update all documentation files
    - Add Podman installation guide
    - Document socket path configuration
    - Remove Docker-specific workarounds
    - Add migration guide for existing deployments

## Medium Priority

### Code Organization
- [x] **Consolidate duplicate configuration** ✅ COMPLETE
  - ✅ Split `.env` into non-sensitive config and `.env.secrets` for passwords
  - ✅ Removed all password variables from `.env`
  - ✅ visp-deploy.py now generates passwords to `.env.secrets` only
  - ✅ Clean separation: configuration vs credentials

- [x] **Create .env.example template** ✅ COMPLETE
  - ✅ Created `.env.secrets.template` with all password variables documented
  - ✅ visp-deploy.py copies template to `.env.secrets` if not exists
  - ✅ All sensitive variables documented with descriptions

### Documentation
- [ ] **Document Apache vhost configuration**
  - Current issue: `ServerName https://${BASE_DOMAIN}:443` was invalid syntax
  - Fixed but should document the proper format and what variables are available

### Testing & Validation
- [ ] **Add automated deployment tests**
  - Test that all services start successfully
  - Test that authentication works (both Shibboleth and test user)
  - Test that APIs are accessible
  - Could integrate with `visp-deploy.py status` command

## Low Priority

### Session Recovery
- [ ] **Investigate crash recovery for running sessions**
  - **Current State**: If session-manager crashes, running Jupyter/RStudio containers are orphaned with no way to reconnect
  - **Desired Feature**: Detect and import running session containers after session-manager restart
  - **Challenges**:
    - Need to reconstruct session state from container inspection
    - Session metadata (user, timestamps, URLs) stored only in memory/MongoDB
    - Container labels or naming conventions could help identify orphaned sessions
    - Would need to rebuild session-manager's internal state from discovered containers
  - **Potential Approach**:
    - Label all session containers with user ID, session type, creation timestamp
    - On startup, query Podman for containers matching session labels
    - Query MongoDB for corresponding session records
    - Reconstruct session objects and re-establish proxying
  - **Priority**: Low - session-manager crashes are rare, manual cleanup currently works

## Notes

### Git History
All completed tasks are documented in git commits. Use `git log` to review implementation details.

### Git Stashes
- "WIP: Matomo tracking implementation" - Contains tracker files and Docker mounts
- "WIP: Docker compose changes for Matomo tracker mounts" - Volume mount configs
