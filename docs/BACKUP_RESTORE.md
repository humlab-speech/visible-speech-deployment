# VISP Backup & Restore Guide

Quick guide for backing up and restoring VISP on a new machine.

## What to Back Up

Only 2 things need backup:

```
visible-speech-deployment/
├── mounts/
│   └── repositories/        ← Back up this (user data)
└── visp_mongodb_backup.tar.gz  ← Database backup
```

## Backup Process

```bash
# 1. Backup database
cd ~/Projects/visible-speech-deployment
./visp-podman.py backup -o ~/backups/visp_mongodb_$(date +%Y%m%d).tar.gz

# 2. Backup repositories
tar -czf ~/backups/visp_repositories_$(date +%Y%m%d).tar.gz \
    mounts/repositories/

# 3. Copy backups to safe location
# ~/backups/visp_mongodb_YYYYMMDD.tar.gz
# ~/backups/visp_repositories_YYYYMMDD.tar.gz
```

## Restore on Clean Machine

### Step 1: System Prerequisites (as admin user)

```bash
# SSH to new machine as your admin user (with sudo)

# Install system packages
sudo apt update
sudo apt install -y git podman podman-docker podman-netavark aardvark-dns

# Create dedicated visp user (NO sudo access)
sudo adduser visp
# Set password when prompted

# Enable user services to run without login
sudo loginctl enable-linger visp
```

### Step 2: Switch to VISP User

```bash
# Switch to visp user for all remaining steps
sudo su - visp

# Verify netavark (run as visp user)
podman info | grep networkBackend  # Should show: netavark
```

### Step 3: Clone & Configure (as visp user)

```bash
# Should still be logged in as visp user
# If not: sudo su - visp

# Clone repository
mkdir -p ~/Projects
cd ~/Projects
git clone https://github.com/humlab-speech/visible-speech-deployment.git
cd visible-speech-deployment

# Create .env file
cp .env-example .env
nano .env  # Set BASE_DOMAIN, ADMIN_EMAIL, etc.
```

### Step 4: Build & Install (as visp user)

```bash
# All commands run as visp user (rootless Podman)

# Build required images
./visp-podman.py build session-manager
./visp-podman.py build webclient

# Build session images (if using RStudio/Jupyter features)
./visp-podman.py build operations-session
./visp-podman.py build rstudio-session
./visp-podman.py build jupyter-session

# Install quadlets (netavark auto-configured)
./visp-podman.py install --mode prod

# Start services
./visp-podman.py reload
./visp-podman.py start all
```

### Step 5: Restore Data (as visp user)

```bash
# Wait for MongoDB to start
sleep 10

# Stop MongoDB temporarily
./visp-podman.py stop mongo

# Restore database
./visp-podman.py restore ~/backups/visp_mongodb_YYYYMMDD.tar.gz --force

# Restore repositories
cd ~/Projects/visible-speech-deployment
tar -xzf ~/backups/visp_repositories_YYYYMMDD.tar.gz

# Verify structure
ls -la mounts/repositories/
# Should show project directories

# Fix permissions (everything owned by visp user)
chown -R $(id -u):$(id -g) mounts/repositories/

# Restart all services
./visp-podman.py start all
```

### Step 6: Verify

```bash
# Check services
./visp-podman.py status

# Test web interface
curl http://localhost:8081/

# Check logs
./visp-podman.py logs -n 50
```

## Quick Reference

### Backup Everything
```bash
./visp-podman.py backup -o ~/visp_db_$(date +%Y%m%d).tar.gz && \
tar -czf ~/visp_repos_$(date +%Y%m%d).tar.gz mounts/repositories/
```

### Expected Directory Structure

After restore, your system should look like:

```
~/Projects/visible-speech-deployment/
├── .env                          # Configuration (create manually)
├── .env.secrets                  # Auto-generated on install
├── visp-podman.py               # Management script
├── mounts/
│   ├── repositories/            # ← RESTORED USER DATA
│   │   ├── project1_repo/
│   │   └── project2_repo/
│   ├── apache/
│   │   └── apache/
│   │       └── uploads/         # Audio uploads
│   ├── mongo/
│   │   └── data/                # MongoDB data files
│   └── session-manager/
│       └── logs/
└── quadlets/
    ├── dev/
    └── prod/
```

## Troubleshooting

### Database restore fails
```bash
# Check MongoDB is stopped
./visp-podman.py stop mongo
# Try restore again
./visp-podman.py restore backup.tar.gz --force
```

### Repositories not accessible
```bash
# Check permissions (should be owned by visp user)
ls -la mounts/repositories/
chown -R $(id -u):$(id -g) mounts/repositories/
```

### Services won't start
```bash
# Check network backend
podman info | grep networkBackend  # Must be: netavark

# Check logs
./visp-podman.py logs -n 100
```

### DNS resolution slow (20+ seconds)
```bash
# You're on CNI backend, need to migrate
./visp-podman.py install --mode prod --force
# Will prompt for netavark migration
```

## Automated Backup Script

Create `~/backup-visp.sh`:

```bash
#!/bin/bash
BACKUP_DIR=~/visp-backups
DATE=$(date +%Y%m%d_%H%M%S)
mkdir -p "$BACKUP_DIR"

cd ~/Projects/visible-speech-deployment

# Backup database
./visp-podman.py backup -o "$BACKUP_DIR/db_$DATE.tar.gz"

# Backup repositories
tar -czf "$BACKUP_DIR/repos_$DATE.tar.gz" mounts/repositories/

# Keep only last 7 days
find "$BACKUP_DIR" -name "*.tar.gz" -mtime +7 -delete

echo "Backup complete: $BACKUP_DIR"
ls -lh "$BACKUP_DIR"
```

Make executable: `chmod +x ~/backup-visp.sh`

Add to crontab: `crontab -e`
```
# Daily backup at 2 AM
0 2 * * * /home/visp/backup-visp.sh >> /home/visp/backup.log 2>&1
```

## Notes

- **Images preserved**: Container images don't need backup (rebuilt from Dockerfiles)
- **Config not backed up**: `.env` file contains site-specific config, recreate on new machine
- **Secrets auto-generated**: `.env.secrets` created automatically during install
- **Netavark required**: System will auto-configure on first install
- **User data location**: `mounts/repositories/` contains all user research data
- **Database location**: MongoDB data in `mounts/mongo/data/` (backed up via dump)
- **Security**: visp user runs without sudo - system packages installed by admin user only
- **Rootless Podman**: All containers run as visp user, not root
