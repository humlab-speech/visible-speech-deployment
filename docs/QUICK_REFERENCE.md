# VISP Deployment Quick Reference

## New Deployment Checklist

### Before You Start
- [ ] Server has public IP (production) or local network access (dev)
- [ ] DNS records configured (production only)
- [ ] Docker and docker-compose installed
- [ ] Git and OpenSSL installed

### Step 1: Clone and Configure
```bash
git clone https://github.com/humlab-speech/visible-speech-deployment.git
cd visible-speech-deployment
cp .env-example .env
nano .env
```

### Step 2: Critical .env Settings
```bash
# MUST MATCH YOUR DEPLOYMENT
BASE_DOMAIN=your-domain.com

# MUST MATCH BASE_DOMAIN - See table below
WEBCLIENT_BUILD=your-domain-build

# Auto-generated passwords (or set manually)
MONGO_ROOT_PASSWORD=<random>
TEST_USER_LOGIN_KEY=<random>
VISP_API_ACCESS_TOKEN=<random>
```

### Step 3: Check Webclient Configuration Exists
```bash
# Does this file exist?
ls external/webclient/src/environments/environment.your-domain.ts

# If NO, follow "Adding New Domain" section in DEPLOYMENT_GUIDE.md
```

### Step 4: Install
```bash
# Development
sudo python3 visp_deploy.py install --mode=dev

# Production
sudo python3 visp_deploy.py install --mode=prod
```

### Step 5: Build and Start
```bash
docker compose build
docker compose up -d
```

### Step 6: Verify
```bash
python3 visp_deploy.py status
# Should show: ✅ CORRECT for webclient build
```

---

## Domain Configuration Table

| BASE_DOMAIN | WEBCLIENT_BUILD | Environment File |
|-------------|-----------------|------------------|
| `visp.local` | `visp-build` | `environment.visp.ts` |
| `visp.humlab.umu.se` | `visp-build` | `environment.visp.ts` |
| `visp-demo.humlab.umu.se` | `visp-demo-build` | `environment.visp-demo.ts` |
| `visp.pdf-server.humlab.umu.se` | `visp-pdf-server-build` | `environment.visp-pdf-server.ts` |
| **Your domain** | **your-domain-build** | **Create new file!** |

---

## Common Commands

### Deployment Script
```bash
# Check status (shows config mismatches)
python3 visp_deploy.py status

# Install dev mode
sudo python3 visp_deploy.py install --mode=dev

# Install prod mode
sudo python3 visp_deploy.py install --mode=prod

# Update repositories
python3 visp_deploy.py update

# Force update (stash changes)
python3 visp_deploy.py update --force
```

### Docker Commands
```bash
# Build all images
docker compose build

# Build specific service
docker compose build apache

# Start services
docker compose up -d

# Stop services
docker compose down

# Restart service
docker compose restart apache

# View logs
docker compose logs -f apache

# Check status
docker compose ps
```

### MongoDB Commands
```bash
# Connect to MongoDB
docker exec -it visp-mongo-1 mongosh -u root -p <PASSWORD>

# Allow test user login
use visp
db.users.updateOne(
  {username: "testuser_at_example_dot_com"},
  {$set: {loginAllowed: true}},
  {upsert: true}
)
```

---

## Troubleshooting Quick Fixes

### ⚠️ MISMATCH: Build Config Wrong
**Symptom:** `python3 visp_deploy.py status` shows MISMATCH
```bash
# Fix .env
nano .env
# Set WEBCLIENT_BUILD to match BASE_DOMAIN

# Rebuild
docker compose build apache
docker compose up -d apache
```

### ⚠️ WebSocket Connection Failed
**Symptom:** Browser console: `WebSocket connection to 'wss://wrong-domain/' failed`
```bash
# Same as above - rebuild with correct WEBCLIENT_BUILD
docker compose build apache
docker compose up -d apache
```

### ⚠️ API Returns 401 Unauthorized
**Symptom:** User logged in but API fails
```bash
# Update MongoDB
docker exec -it visp-mongo-1 mongosh -u root -p <PASSWORD>
use visp
db.users.updateOne(
  {username: "your_user"},
  {$set: {loginAllowed: true}}
)
```

### ⚠️ Empty /var/www/html in Apache
**Symptom:** 404 errors or blank page
```bash
# Rebuild with no cache
docker compose build --no-cache apache
docker compose up -d apache
```

### ⚠️ Permission Errors
```bash
# Run install with sudo
sudo python3 visp_deploy.py install

# Or fix permissions manually
sudo chown -R $USER:$USER external/ mounts/
```

---

## Adding a New Domain (Quick Steps)

1. **Create environment file:**
   ```bash
   cd external/webclient/src/environments/
   cp environment.visp-demo.ts environment.mydomain.ts
   # Edit: Change API_ENDPOINT and BASE_DOMAIN
   ```

2. **Add npm script** in `package.json`:
   ```json
   "mydomain-build": "ng build --configuration=mydomain --output-path dist"
   ```

3. **Add Angular config** in `angular.json`:
   ```json
   "mydomain": {
     "fileReplacements": [{
       "replace": "src/environments/environment.ts",
       "with": "src/environments/environment.mydomain.ts"
     }]
   }
   ```

4. **Update deployment .env:**
   ```bash
   BASE_DOMAIN=mydomain.com
   WEBCLIENT_BUILD=mydomain-build
   ```

5. **Build and deploy:**
   ```bash
   docker compose build apache
   docker compose up -d apache
   ```

---

## File Locations Reference

```
visible-speech-deployment/
├── .env                    # Main configuration (EDIT THIS!)
├── docker-compose.yml      # Symlink to dev/prod
├── visp_deploy.py          # Deployment script
├── external/
│   └── webclient/
│       ├── src/environments/    # Domain configs (ADD HERE)
│       ├── package.json         # npm scripts (ADD HERE)
│       └── angular.json         # Build configs (ADD HERE)
├── mounts/                 # Persistent data
│   ├── apache/             # Apache configs
│   ├── mongo/              # MongoDB data
│   └── repositories/       # User data
└── docker/
    └── apache/
        └── Dockerfile      # Reads WEBCLIENT_BUILD arg
```

---

## URLs Reference

### Development (visp.local)
- Main: `https://visp.local`
- Test login: `https://visp.local/?login=<TEST_USER_LOGIN_KEY>`
- Mongo Express: `http://localhost:28084`
- EMU-webApp: `https://emu-webapp.visp.local`
- OCTRA: `https://octra.visp.local`

### Production (your-domain.com)
- Main: `https://your-domain.com`
- Test login: `https://your-domain.com/?login=<TEST_USER_LOGIN_KEY>`
- Mongo Express: `http://your-server:28084` (localhost only)
- Subdomains: `https://emu-webapp.your-domain.com`, etc.

---

## Status Output Interpretation

```bash
$ python3 visp_deploy.py status
```

### Good Status ✅
```
📦 WEBCLIENT BUILD CONFIGURATION
| WEBCLIENT_BUILD | visp-pdf-server-build | visp.pdf-server.humlab.umu.se | Contains '...' | ✅ CORRECT |
```

### Bad Status ⚠️
```
| WEBCLIENT_BUILD | visp-build | visp.pdf-server.humlab.umu.se | Contains 'visp.humlab.umu.se' | ⚠️ MISMATCH |
```
**Fix:** Update `.env` and rebuild apache

### Not Built ⚠️
```
| WEBCLIENT_BUILD | visp-build | visp.local | Not built | ⚠️ NOT BUILT |
```
**Fix:** Run `docker compose build apache`

---

## Important Notes

1. **WEBCLIENT_BUILD is BUILD-TIME configuration**
   - Set BEFORE building Docker image
   - Cannot be changed at runtime
   - Must rebuild apache image after changing

2. **BASE_DOMAIN and WEBCLIENT_BUILD must match**
   - See domain configuration table above
   - Mismatch causes WebSocket failures
   - Mismatch causes API connection issues

3. **Development vs Production**
   - Dev: Source code mounted, hot-reload works
   - Prod: Code baked in image, must rebuild after changes

4. **MongoDB Password**
   - If MongoDB already has data, DON'T change password
   - Keep existing MONGO_ROOT_PASSWORD in .env
   - Update password in all .env files if you must change it

5. **Test User Login**
   - Only for development/demo
   - Disable or remove TEST_USER_LOGIN_KEY in production
   - Use real Shibboleth/SWAMID authentication in production

---

## Support Resources

- **Full Documentation:** `docs/DEPLOYMENT_GUIDE.md`
- **Webclient Config:** `docs/WEBCLIENT_BUILD_CONFIG.md`
- **GitHub Issues:** https://github.com/humlab-speech/visible-speech-deployment/issues

---

**Last Updated:** December 2025
