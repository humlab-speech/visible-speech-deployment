# VISP Troubleshooting Decision Tree

Use this flowchart-style guide to quickly diagnose and fix common issues.

---

## Start Here: What's the problem?

1. [Can't access the website at all](#1-cant-access-website)
2. [Website loads but login doesn't work](#2-login-issues)
3. [Login works but page shows errors](#3-page-shows-errors)
4. [WebSocket connection fails](#4-websocket-connection-fails)
5. [API calls return errors](#5-api-errors)
6. [Docker build/startup issues](#6-docker-issues)
7. [visp_deploy.py script issues](#7-deployment-script-issues)

---

## 1. Can't Access Website

### ❓ Are you accessing via correct URL?

**Development (local):**
- URL should be: `https://visp.local`
- Did you add to `/etc/hosts`?
  ```bash
  echo "127.0.0.1 visp.local" | sudo tee -a /etc/hosts
  ```
- ✅ YES → Continue
- ❌ NO → Add to hosts file and try again

**Production (with DNS):**
- URL should be: `https://your-domain.com`
- Does DNS resolve correctly?
  ```bash
  nslookup your-domain.com
  ```
- ✅ YES → Continue
- ❌ NO → Fix DNS records first

### ❓ Are Docker containers running?

```bash
docker compose ps
```

**All services show "Up"?**
- ✅ YES → Continue
- ❌ NO → [Go to Docker Issues](#6-docker-issues)

### ❓ Is Apache responding?

```bash
docker compose logs apache | tail -50
```

**Look for errors:**
- **"Permission denied"** → [Permission Issues](#permission-issues)
- **"Address already in use"** → Another service using ports 80/443
  ```bash
  sudo lsof -i :80
  sudo lsof -i :443
  ```
- **"No such file or directory: /var/www/html"** → [Empty Apache Container](#empty-apache-container)

### ❓ Is SSL certificate valid?

**Browser shows certificate error?**
- Development: Expected for self-signed cert, click "Advanced" → "Accept Risk"
- Production: Check certificate:
  ```bash
  openssl s_client -connect your-domain.com:443 -servername your-domain.com
  ```

---

## 2. Login Issues

### ❓ Which authentication method are you using?

**A. Test User Login (/?login=password)**

```bash
# Check TEST_USER_LOGIN_KEY is set
grep TEST_USER_LOGIN_KEY .env
```

**Is it set and non-empty?**
- ✅ YES → Continue
- ❌ NO → Set in .env and restart apache

**Try accessing:**
```
https://your-domain.com/?login=<TEST_USER_LOGIN_KEY>
```

**Does it redirect to main page?**
- ✅ YES → Login successful, [check for other errors](#3-page-shows-errors)
- ❌ NO → Check apache logs:
  ```bash
  docker compose logs apache | grep -i "test.*user"
  ```

**B. Shibboleth/SWAMID Authentication**

**Is Shibboleth configured?**
```bash
ls mounts/apache/saml/swamid/$BASE_DOMAIN/shibboleth2.xml
```

**File exists?**
- ✅ YES → Check configuration matches your domain
- ❌ NO → Copy and configure Shibboleth files

**Check Shibboleth daemon logs:**
```bash
docker compose exec apache cat /var/log/shibboleth/shibd.log
```

---

## 3. Page Shows Errors

### ❓ What error do you see in browser console?

Press F12 → Console tab

**Error: "e is null" or "Cannot read property 'eppn'"**

→ **API returns data but Angular can't parse it**

**Cause:** Wrong webclient build configuration

**Fix:**
```bash
# Check configuration
python3 visp_deploy.py status

# Look for: ⚠️ MISMATCH in WEBCLIENT BUILD CONFIGURATION

# Update .env
nano .env
# Set WEBCLIENT_BUILD to match BASE_DOMAIN

# Rebuild
docker compose build apache
docker compose up -d apache
```

**Error: "Failed to load resource: 401 Unauthorized"**

→ [Go to API Errors](#5-api-errors)

**Error: "WebSocket connection failed"**

→ [Go to WebSocket Issues](#4-websocket-connection-fails)

**Error: "Mixed Content" or "Blocked loading mixed active content"**

→ **HTTP/HTTPS protocol mismatch**

**Fix:**
```bash
# Check .env
grep HTTP_PROTOCOL .env
# Should be: HTTP_PROTOCOL=https

# If wrong, fix and restart
nano .env
docker compose restart apache
```

---

## 4. WebSocket Connection Fails

### ❓ What's the WebSocket URL in browser DevTools?

Press F12 → Network tab → Filter: WS

**Look at connection URL:**

**Example 1:** Connecting to `wss://visp.humlab.umu.se/` but you're on `visp.pdf-server.humlab.umu.se`

→ **Wrong domain in webclient build**

**Fix:**
```bash
# Check status
python3 visp_deploy.py status

# Will show: ⚠️ MISMATCH (built for visp.humlab.umu.se, .env expects visp.pdf-server.humlab.umu.se)

# Update .env
nano .env
BASE_DOMAIN=visp.pdf-server.humlab.umu.se
WEBCLIENT_BUILD=visp-pdf-server-build

# Rebuild
docker compose build apache
docker compose up -d apache
```

**Example 2:** Connecting to `ws://` instead of `wss://`

→ **Protocol mismatch**

**Fix:**
```bash
# Check environment file
cat external/webclient/src/environments/environment.*.ts | grep WS_PROTOCOL
# Should be: WS_PROTOCOL: 'wss://'

# If wrong, edit file and rebuild
```

**Example 3:** Connection refused or timeout

→ **WebSocket server not running**

```bash
# Check wsrng-server is running
docker compose ps wsrng-server

# Check logs
docker compose logs wsrng-server
```

---

## 5. API Errors

### ❓ What HTTP status code?

**401 Unauthorized**

**Scenario A: After successful login**

→ **MongoDB user has loginAllowed: false**

**Fix:**
```bash
docker exec -it visp-mongo-1 mongosh -u root -p <MONGO_ROOT_PASSWORD>

use visp

# Check user
db.users.findOne({username: "testuser_at_example_dot_com"})

# Fix if loginAllowed is false
db.users.updateOne(
  {username: "testuser_at_example_dot_com"},
  {$set: {loginAllowed: true}}
)
```

**Scenario B: Before login**

→ **Expected - user not authenticated**

**403 Forbidden**

→ **Access list enabled and user not in list**

**Fix:**
```bash
# Option 1: Disable access list (dev/demo only)
nano .env
ACCESS_LIST_ENABLED=false

# Option 2: Add user to access list in MongoDB
docker exec -it visp-mongo-1 mongosh -u root -p <MONGO_ROOT_PASSWORD>

use visp
db.users.updateOne(
  {username: "user@example.com"},
  {$set: {loginAllowed: true}}
)
```

**404 Not Found**

→ **API endpoint doesn't exist or wrong URL**

**Check:**
```bash
# API should respond at /api/v1/
curl -k https://localhost/api/v1/user/info
```

**500 Internal Server Error**

→ **Backend PHP error**

**Check logs:**
```bash
# API logs
cat mounts/webapi/logs/*.log

# Apache error log
docker compose logs apache | grep -i error
```

---

## 6. Docker Issues

### ❓ What's the specific problem?

**"Cannot connect to Docker daemon"**

```bash
# Check Docker is running
sudo systemctl status docker

# Start if needed
sudo systemctl start docker

# Check permissions
groups | grep docker

# If not in docker group:
sudo usermod -aG docker $USER
# Then logout/login
```

**"Port already in use"**

```bash
# Find what's using the port
sudo lsof -i :80
sudo lsof -i :443

# Option 1: Stop conflicting service
sudo systemctl stop apache2  # or nginx, etc.

# Option 2: Change ports in .env
nano .env
HTTP_PORT=8080
HTTPS_PORT=8443
```

**"Build failed" or "npm install failed"**

```bash
# Try no-cache build
docker compose build --no-cache apache

# Check disk space
df -h

# Check Docker disk usage
docker system df
```

**"Container exits immediately"**

```bash
# Check logs for the failing container
docker compose logs <service-name>

# Common causes:
# - Missing environment variables
# - Configuration errors
# - Permission issues
```

**"Cannot start service: OCI runtime create failed"**

→ **SELinux/AppArmor conflict or resource limits**

```bash
# Check SELinux status
getenforce

# If enforcing, try permissive mode (temporarily):
sudo setenforce 0

# Or fix SELinux labels:
sudo chcon -Rt svirt_sandbox_file_t ./mounts/
```

### Empty Apache Container

**Symptom:** `/var/www/html/` is empty in apache container

**Cause:** Docker build didn't complete webclient build

**Fix:**
```bash
# Check if build actually ran
docker compose build apache 2>&1 | grep "npm run"

# Should see: "RUN npm run visp-build" (or similar)

# If not, check WEBCLIENT_BUILD in .env
grep WEBCLIENT_BUILD .env

# Rebuild with no cache
docker compose build --no-cache apache

# If still fails, check build logs for errors
docker compose build apache 2>&1 | tee build.log
grep -i error build.log
```

### Permission Issues

**Symptom:** "Permission denied" in logs

**Cause:** File ownership issues between host and container

**Fix:**
```bash
# Run install script with sudo
sudo python3 visp_deploy.py install

# Or fix manually
sudo chown -R $USER:$USER external/
sudo chown -R $USER:$USER mounts/

# For logs that need container write access
sudo chmod -R 777 mounts/*/logs/
```

---

## 7. Deployment Script Issues

### ❓ What command are you running?

**`python3 visp_deploy.py install`**

**"Missing required dependencies"**

```bash
sudo apt install -y curl git openssl docker.io docker-compose python3
```

**"Permission denied" when setting ownership**

→ **Expected if not running as sudo**

**Solutions:**
```bash
# Option 1: Run with sudo (recommended for production)
sudo python3 visp_deploy.py install

# Option 2: Continue without sudo (warnings OK for dev)
python3 visp_deploy.py install
# Ignore permission warnings - Docker works fine
```

**"Failed to clone repository"**

```bash
# Check internet connection
ping github.com

# Check if git is installed
git --version

# Try manual clone
git clone https://github.com/humlab-speech/webclient.git
```

**`python3 visp_deploy.py status`**

**Shows: ⚠️ MISMATCH for WEBCLIENT_BUILD**

→ See [WebSocket Connection Fails](#4-websocket-connection-fails)

**Shows: ⚠️ HAS CHANGES for repositories**

→ **Local modifications not committed**

**Options:**
```bash
# Option 1: Commit changes
cd external/webclient
git add .
git commit -m "Local changes"

# Option 2: Stash changes
git stash

# Option 3: Force update (auto-stash)
python3 visp_deploy.py update --force
```

**Shows: 🚀 AHEAD or ⬇️ BEHIND**

→ **Local branch differs from remote**

```bash
# Pull remote changes
cd external/<repo-name>
git pull

# Or push local changes
git push
```

**`python3 visp_deploy.py update`**

**"Update failed" for repository**

```bash
# Check what went wrong
cd external/<repo-name>
git status
git fetch --all

# May have uncommitted changes
# Use --force to auto-stash:
python3 visp_deploy.py update --force
```

---

## Common Resolution Patterns

### Pattern A: Fresh Start

**When:** Everything is broken, start over

```bash
# Stop all services
docker compose down

# Remove containers and volumes (CAUTION: deletes data!)
docker compose down -v

# Clean Docker system
docker system prune -a

# Re-run install
sudo python3 visp_deploy.py install --mode=dev
docker compose up -d
```

### Pattern B: Configuration Change

**When:** Changed .env settings

```bash
# After editing .env:
docker compose down
docker compose build  # if WEBCLIENT_BUILD changed
docker compose up -d
```

### Pattern C: Code Update

**When:** Updated source code or git pulled

**Development mode:**
```bash
# Hot-reload works, just restart service
docker compose restart <service-name>
```

**Production mode:**
```bash
# Must rebuild image
docker compose build <service-name>
docker compose up -d <service-name>
```

### Pattern D: Domain Change

**When:** Deploying to new domain

```bash
# 1. Create environment file (if needed)
cd external/webclient/src/environments/
cp environment.visp-demo.ts environment.your-domain.ts
# Edit: Change API_ENDPOINT and BASE_DOMAIN

# 2. Add to package.json and angular.json
# See DEPLOYMENT_GUIDE.md "Adding a New Deployment Domain"

# 3. Update deployment .env
cd /path/to/visible-speech-deployment
nano .env
BASE_DOMAIN=your-domain.com
WEBCLIENT_BUILD=your-domain-build

# 4. Rebuild
docker compose build apache
docker compose up -d apache

# 5. Verify
python3 visp_deploy.py status
# Should show: ✅ CORRECT
```

---

## Still Having Issues?

### Comprehensive Logs

Collect all relevant logs:

```bash
# Create debug report
mkdir debug-report
docker compose logs > debug-report/docker-logs.txt
python3 visp_deploy.py status > debug-report/status.txt
cp .env debug-report/env-sanitized.txt  # Remove passwords before sharing!
docker compose ps > debug-report/containers.txt
docker compose config > debug-report/compose-resolved.txt

# Tar it up
tar -czf debug-report-$(date +%Y%m%d).tar.gz debug-report/
```

### Check Resources

```bash
# Disk space
df -h

# Memory usage
free -h

# Docker disk usage
docker system df

# Running containers
docker stats --no-stream
```

### Documentation

1. Read [Complete Deployment Guide](DEPLOYMENT_GUIDE.md)
2. Check [Webclient Build Configuration](WEBCLIENT_BUILD_CONFIG.md)
3. Review [Quick Reference](QUICK_REFERENCE.md)

### Get Help

1. Check GitHub Issues: https://github.com/humlab-speech/visible-speech-deployment/issues
2. Search existing issues for similar problems
3. Create new issue with:
   - Your deployment mode (dev/prod)
   - BASE_DOMAIN and WEBCLIENT_BUILD values
   - Output of `python3 visp_deploy.py status`
   - Relevant logs from above

---

**Pro Tip:** The `visp_deploy.py status` command is your friend! Run it often to catch configuration mismatches early.
