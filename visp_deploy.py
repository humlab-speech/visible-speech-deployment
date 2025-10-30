#!/usr/bin/env python3

import os
import sys
import shutil
import subprocess
import argparse
from datetime import datetime
import string
import random
import getpass
import json

try:
    from tabulate import tabulate
except ImportError:
    print("tabulate library not found. Install with: pip install tabulate")


# Configuration constants
DEFAULT_VERSIONS_CONFIG = {
    "webclient": {
        "version": "latest",
        "url": None,
        "npm_install": True,
        "npm_build": True,
    },
    "container-agent": {
        "version": "latest",
        "url": None,
        "npm_install": True,
        "npm_build": False,
    },
    "webapi": {
        "version": "latest",
        "url": None,
        "npm_install": False,
        "npm_build": False,
    },
    "wsrng-server": {
        "version": "latest",
        "url": None,
        "npm_install": True,
        "npm_build": False,
    },
    "session-manager": {
        "version": "latest",
        "url": None,
        "npm_install": True,
        "npm_build": False,
    },
    "emu-webapp-server": {
        "version": "latest",
        "url": None,
        "npm_install": True,
        "npm_build": False,
    },
    "EMU-webApp": {
        "version": "latest",
        "url": "https://github.com/humlab-speech/EMU-webApp.git",
        "npm_install": True,
        "npm_build": True,
    },
}

COMPONENTS_WITH_PERMISSIONS = [
    "webclient",
    "certs",
    "container-agent",
    "webapi",
    "wsrng-server",
    "session-manager",
    "emu-webapp-server",
]

TARGET_UID = os.getuid()  # Use current user's UID instead of hardcoded 1000
TARGET_GID = os.getgid()  # Use current user's GID instead of hardcoded 1000

COMPONENTS_WITH_PERMISSIONS = [
    "webclient",
    "certs",
    "container-agent",
    "webapi",
    "wsrng-server",
    "session-manager",
    "emu-webapp-server",
]


def chown_recursive(path, uid, gid):
    try:
        os.chown(path, uid, gid)
        for root, dirs, files in os.walk(path):
            for d in dirs:
                os.chown(os.path.join(root, d), uid, gid)
            for f in files:
                os.chown(os.path.join(root, f), uid, gid)
    except OSError as e:
        print(f"Failed to chown {path}: {e}")


def generate_random_string(length=16):
    return "".join(random.choices(string.ascii_letters + string.digits, k=length))


def load_versions_config():
    """Load version configuration from versions.json or use defaults"""
    config_file = "versions.json"
    if not os.path.exists(config_file):
        print(f"Warning: {config_file} not found, using default component versions")
        return DEFAULT_VERSIONS_CONFIG

    try:
        with open(config_file, "r", encoding="utf-8") as f:
            loaded_config = json.load(f).get("components", {})
            return loaded_config if loaded_config else DEFAULT_VERSIONS_CONFIG
    except (json.JSONDecodeError, IOError) as e:
        print(f"Warning: Failed to load {config_file}: {e}. Using defaults.")
        return DEFAULT_VERSIONS_CONFIG


def check_uncommitted_changes(repo_path):
    """Check if repository has uncommitted changes"""
    try:
        result = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=repo_path,
            capture_output=True,
            text=True,
            check=True,
        )
        return bool(result.stdout.strip())
    except subprocess.CalledProcessError:
        return False


def handle_uncommitted_changes(repo_path, name, force=False):
    """Handle uncommitted changes in repository"""
    if not check_uncommitted_changes(repo_path):
        return True  # No changes, proceed

    if force:
        print(f"⚠️  Force mode: Stashing uncommitted changes in {name}")
        try:
            subprocess.run(
                ["git", "stash", "push", "-m", f"Auto-stash before update: {name}"],
                cwd=repo_path,
                check=True,
            )
            return True
        except subprocess.CalledProcessError as e:
            print(f"Failed to stash changes in {name}: {e}")
            return False
    else:
        print(f"⚠️  WARNING: {name} has uncommitted changes!")
        print("   Options:")
        print("   1. Commit or stash your changes first")
        print("   2. Run update with --force to stash changes automatically")
        print("   3. Skip this repository")
        return False


def setup_env_file(auto_passwords=True, interactive=False):
    if not os.path.exists(".env"):
        if os.path.exists(".env-example"):
            shutil.copy(".env-example", ".env")
        else:
            print("Error: .env-example not found")
            return

    # Set defaults
    env_updates = {"ABS_ROOT_PATH": os.getcwd(), "ADMIN_EMAIL": "admin@visp.local"}

    with open(".env", "r", encoding="utf-8") as f:
        content = f.read()

    for key, value in env_updates.items():
        if f"{key}=" in content:
            content = content.replace(f"{key}=", f"{key}={value}")
        else:
            content += f"\n{key}={value}"

    # Check if MongoDB data already exists
    mongo_data_exists = os.path.exists("./mounts/mongo/data") and os.listdir(
        "./mounts/mongo/data"
    )

    # Password variables to handle
    password_vars = {
        "POSTGRES_PASSWORD": "local",
        "TEST_USER_LOGIN_KEY": "local",
        "VISP_API_ACCESS_TOKEN": "local",
        "RSTUDIO_PASSWORD": "local",
        "MONGO_ROOT_PASSWORD": "local",
        "ELASTIC_AGENT_FLEET_ENROLLMENT_TOKEN": "local",
        "MATOMO_DB_PASSWORD": "local",
        "MATOMO_DB_ROOT_PASSWORD": "local",
        "MATOMO_DB_USER": "local",
    }

    for var, ptype in password_vars.items():
        # Special handling for MongoDB password if data already exists
        if var == "MONGO_ROOT_PASSWORD" and mongo_data_exists:
            current_value = ""
            if f"{var}=" in content:
                current_value = content.split(f"{var}=")[1].split("\n")[0].strip()

            if current_value:
                # Password exists and MongoDB data exists - don't change it
                print("⚠️  MongoDB database already exists with data.")
                print(
                    "   Keeping existing MONGO_ROOT_PASSWORD to avoid authentication issues."
                )
                continue
            else:
                # No password set but data exists - warn user
                print(
                    "⚠️  WARNING: MongoDB data exists but no MONGO_ROOT_PASSWORD in .env!"
                )
                if (
                    interactive
                    or input("   Set MongoDB password now? (y/n): ").lower() == "y"
                ):
                    password = getpass.getpass(f"   Enter {var}: ")
                    content = (
                        content.replace(f"{var}=", f"{var}={password}")
                        if f"{var}=" in content
                        else content + f"\n{var}={password}"
                    )
                continue

        if auto_passwords or ptype == "local":
            random_value = generate_random_string()
            if f"{var}=" in content:
                content = content.replace(f"{var}=", f"{var}={random_value}")
            else:
                content += f"\n{var}={random_value}"
        elif interactive:
            if (
                f"{var}=" not in content
                or content.split(f"{var}=")[1].split("\n")[0].strip() == ""
            ):
                password = getpass.getpass(f"Enter {var}: ")
                if f"{var}=" in content:
                    content = content.replace(f"{var}=", f"{var}={password}")
                else:
                    content += f"\n{var}={password}"

    with open(".env", "w", encoding="utf-8") as f:
        f.write(content)


def check_env_file():
    if not os.path.exists(".env"):
        print(
            "Warning: .env file not found. Please create it from .env-example "
            "and fill in the required values as per the README."
        )
        return

    # Read file once
    with open(".env", "r", encoding="utf-8") as f:
        content = f.read()

    lines = content.splitlines()
    env_vars = {}
    for line in lines:
        if "=" in line and not line.strip().startswith("#"):
            key, value = line.split("=", 1)
            env_vars[key.strip()] = value.strip()

    required_vars = [
        "POSTGRES_PASSWORD",
        "VISP_API_ACCESS_TOKEN",
        "MONGO_ROOT_PASSWORD",
        "RSTUDIO_PASSWORD",
        "MATOMO_DB_ROOT_PASSWORD",
        "MATOMO_DB_USER",
        "MATOMO_DB_PASSWORD",
    ]

    missing = []
    for var in required_vars:
        if var not in env_vars or not env_vars[var]:
            missing.append(var)

    if missing:
        print(
            f"Warning: The following required environment variables are not set in .env: {', '.join(missing)}"
        )
        print("Auto-generating random values for demo deployment...")

        # Generate all missing values and update content in memory
        for var in missing:
            random_value = generate_random_string()
            if f"{var}=" in content:
                content = content.replace(f"{var}=", f"{var}={random_value}")
            else:
                content += f"\n{var}={random_value}"

        # Write file once
        with open(".env", "w", encoding="utf-8") as f:
            f.write(content)
        print("Environment variables auto-filled.")
    else:
        print("Environment file check passed.")


def run_command(cmd, description="", check=True):
    print(f"Running: {description or cmd}")
    try:
        result = subprocess.run(
            cmd, shell=True, check=check, capture_output=True, text=True
        )
        if result.stdout:
            print(result.stdout)
        return result
    except subprocess.CalledProcessError as e:
        print(f"Command failed: {e}")
        if e.stdout:
            print(e.stdout)
        if e.stderr:
            print(e.stderr)
        raise


def check_dependencies():
    """Check if required system dependencies are available"""
    dependencies = {
        "docker": "Docker engine",
        "git": "Git version control",
        "curl": "curl command-line tool",
        "openssl": "OpenSSL for certificate generation",
    }

    missing = []
    for cmd, name in dependencies.items():
        result = subprocess.run(["which", cmd], capture_output=True, text=True)
        if result.returncode != 0:
            missing.append(f"  - {name} ({cmd})")

    if missing:
        print("\n⚠️  WARNING: Missing required dependencies:")
        for dep in missing:
            print(dep)
        print("\nPlease install these dependencies before continuing.")
        print(
            "On Debian/Ubuntu: sudo apt install -y curl git openssl docker.io docker-compose"
        )
        print(
            "\nNote: You can run this script without root access if dependencies are already installed."
        )
        response = input("\nContinue anyway? (y/N): ")
        if response.lower() != "y":
            print("Installation cancelled.")
            sys.exit(1)
    else:
        print("✓ All required dependencies found")


def check_root_permissions():
    """Check if running as root and provide guidance"""
    import os

    if os.geteuid() == 0:
        print("✓ Running as root - full permissions available")
        return True
    else:
        print("⚠️  Running as regular user - some operations may be skipped")
        print("   This is fine for development/demo deployments")
        print("   For production, consider running with: sudo python3 visp_deploy.py")
        return False


def install_system():
    print("Starting VISP installation...")
    BASEDIR = os.getcwd()

    # Check for dependencies (non-root, just checks)
    check_dependencies()

    # Check permissions early
    check_root_permissions()

    # Setup .env
    setup_env_file(auto_passwords=True)

    # Create directories
    os.makedirs("mounts/session-manager", exist_ok=True)
    with open("mounts/session-manager/session-manager.log", "w", encoding="utf-8"):
        pass
    try:
        os.chown("mounts/session-manager/session-manager.log", TARGET_UID, TARGET_GID)
        os.chmod("mounts/session-manager/session-manager.log", 0o644)
    except OSError as e:
        print(f"⚠️  Could not set ownership on session-manager log: {e}")
        print("   → For production: run with 'sudo python3 visp_deploy.py install'")
        print("   → For development: this warning is OK, continuing...")
        print()

    os.makedirs("mounts/webapi", exist_ok=True)
    os.makedirs("mounts/apache/apache/uploads", exist_ok=True)
    os.makedirs("mounts/mongo/logs", exist_ok=True)
    with open("mounts/mongo/logs/mongodb.log", "w", encoding="utf-8"):
        pass
    os.makedirs("certs", exist_ok=True)
    os.makedirs("mounts/transcription-queued", exist_ok=True)

    # Fetch cert
    run_command(
        "curl http://mds.swamid.se/md/md-signer2.crt -o certs/md-signer2.crt",
        "Fetching SWAMID cert",
    )

    # Generate certs
    os.makedirs("certs/visp.local", exist_ok=True)
    run_command(
        "openssl req -x509 -newkey rsa:4096 -keyout certs/visp.local/cert.key "
        "-out certs/visp.local/cert.crt -nodes -days 3650 "
        '-subj "/C=SE/ST=visp/L=visp/O=visp/OU=visp/CN=visp.local"',
        "Generating TLS cert",
    )

    os.makedirs("certs/ssp-idp-cert", exist_ok=True)
    run_command(
        "openssl req -x509 -newkey rsa:4096 -keyout certs/ssp-idp-cert/key.pem "
        "-out certs/ssp-idp-cert/cert.pem -nodes -days 3650 "
        '-subj "/C=SE/ST=visp/L=visp/O=visp/OU=visp/CN=visp.local"',
        "Generating IdP cert",
    )

    # Clone all repos
    # Note: Even "containerized" components need source code for development mode
    versions_config = load_versions_config()

    for name, config in versions_config.items():
        if not os.path.exists(name):
            url = config.get("url", f"https://github.com/humlab-speech/{name}")
            run_command(f"git clone {url} {name}", f"Cloning {name}")

            # Checkout specific version if not "latest"
            version = config.get("version", "latest")
            if version != "latest":
                os.chdir(name)
                run_command(
                    f"git checkout {version}", f"Checking out {version} for {name}"
                )
                os.chdir(BASEDIR)

    # Setup emu-webapp-server .env
    os.makedirs("mounts/emu-webapp-server/logs", exist_ok=True)
    run_command(
        "curl -L https://raw.githubusercontent.com/humlab-speech/emu-webapp-server/main/.env-example "
        "-o ./mounts/emu-webapp-server/.env",
        "Fetching emu-webapp-server .env",
    )

    # Build all components using temporary Node.js containers
    # This works for both development (with source mounts) and production (baked into images)
    components = [
        ("session-manager", ["npm install"]),
        ("wsrng-server", ["npm install"]),
        ("container-agent", ["npm install", "npm run build"]),
        ("webclient", ["npm install --legacy-peer-deps", "npm run build"]),
    ]

    print("\nBuilding components using temporary Node.js containers...")
    for comp, cmds in components:
        comp_path = os.path.join(BASEDIR, comp)
        user_flag = ""
        for cmd in cmds:
            # Use temporary node:20 container for builds instead of host Node.js
            # This ensures clean host and consistent versioning (works with both Docker and Podman)
            run_command(
                f"docker run{user_flag} --rm -v {comp_path}:/app -w /app node:20 {cmd}",
                f"Building {comp}: {cmd}",
            )

    print("\nNote: session-manager and wsrng-server dependencies are installed.")
    print(
        "In development mode (docker-compose.dev.yml), source code is mounted for hot-reload."
    )
    print(
        "In production mode (docker-compose.prod.yml), run 'docker compose build' to bake code into images."
    )

    # Permissions are already correct for rootless execution

    print("\nInstallation complete.")
    print("Next steps:")
    print("  1. Review .env file and configure as needed")
    print("  2. Run 'docker compose build' to build containerized services")
    print("  3. Run 'docker compose up -d' to start all services")


def update_repo(
    basedir,
    name,
    npm_install=False,
    npm_build=False,
    repo_url=None,
    version="latest",
    force=False,
):
    print(f"Update {name}...")
    repo_path = os.path.join(basedir, name)

    # Clone repository if it doesn't exist
    if not os.path.exists(repo_path):
        if not repo_url:
            # Try to infer the repo URL from the name
            repo_url = f"https://github.com/humlab-speech/{name}.git"
        print(f"Repository {name} not found, cloning from {repo_url}...")
        os.chdir(basedir)
        try:
            subprocess.run(["git", "clone", repo_url, name], check=True)
            os.chdir(repo_path)
        except subprocess.CalledProcessError as e:
            print(f"Git clone of {name} failed: {e}")
            sys.exit(1)
    else:
        os.chdir(repo_path)

        # Check for uncommitted changes before proceeding
        if not handle_uncommitted_changes(repo_path, name, force):
            print(f"Skipping {name} due to uncommitted changes")
            return

    try:
        subprocess.run(["git", "fetch", "--all"], check=True)

        # Handle version checkout
        if version and version != "latest":
            print(f"Checking out version {version} for {name}")
            subprocess.run(["git", "checkout", version], check=True)
        else:
            # Use latest (main/master branch)
            try:
                subprocess.run(["git", "reset", "--hard", "origin/main"], check=True)
            except subprocess.CalledProcessError:
                subprocess.run(["git", "reset", "--hard", "origin/master"], check=True)
    except subprocess.CalledProcessError as e:
        print(f"Git update of {name} failed: {e}")
        sys.exit(1)
    user_flag = ""
    if npm_install:
        if os.path.exists("node_modules"):
            # Remove existing node_modules using Docker to handle permissions
            run_command(
                f"docker run --rm -v {repo_path}:/app -w /app alpine sh -c 'rm -rf node_modules'",
                f"Removing old node_modules for {name}",
            )
        run_command(
            f"docker run{user_flag} --rm -v {repo_path}:/app -w /app node:20 npm install --legacy-peer-deps",
            f"Installing npm dependencies for {name}",
        )
    if npm_build:
        # Clean dist directory using Docker
        run_command(
            f"docker run --rm -v {repo_path}:/app -w /app alpine sh -c 'rm -rf dist'",
            f"Cleaning dist for {name}",
        )
        run_command(
            f"docker run{user_flag} --rm -v {repo_path}:/app -w /app node:20 npm run build",
            f"Building {name}",
        )
    os.chdir(basedir)


def check_image_age(image_name, source_path):
    if not os.path.exists(source_path):
        return False  # If source doesn't exist, don't rebuild
    try:
        result = subprocess.run(
            ["docker", "inspect", "-f", "{{.Created}}", image_name],
            capture_output=True,
            text=True,
            check=True,
        )
        created_str = result.stdout.strip().strip('"')  # sometimes quoted
        created_time = datetime.fromisoformat(created_str.replace("Z", "+00:00"))
        source_mtime = os.path.getmtime(source_path)
        return source_mtime > created_time.timestamp()
    except subprocess.CalledProcessError:
        # Image doesn't exist
        return True
    except (ValueError, OSError) as e:
        print(f"Error checking age of {image_name}: {e}")
        return True


def rebuild_images():
    script_dir = "docker/session-manager"
    # Prefer a Docker-based build script when possible so images are available to Docker
    docker_script = os.path.join(script_dir, "build-session-images-no-cache.sh")
    legacy_script = os.path.join(script_dir, "build-session-images.sh")

    # select which script to run (prefer Docker script)
    if shutil.which("docker") and os.path.exists(docker_script):
        script_path = docker_script
        print("Rebuilding images using Docker (no-cache script)...")
    elif os.path.exists(legacy_script):
        # Fallback: older legacy script present
        script_path = legacy_script
        print("Rebuilding images using legacy build script (fallback)...")
    else:
        print(f"No build script found in {script_dir}, skipping rebuild.")
        return
    original_cwd = os.getcwd()
    try:
        original_cwd = os.getcwd()
        os.chdir(script_dir)
        # Run the selected script with bash to ensure it executes correctly
        result = subprocess.run(
            ["/bin/bash", os.path.basename(script_path)], check=False
        )
        if result.returncode == 0:
            print("Images rebuilt successfully.")
        else:
            print("Image rebuild failed, but continuing with update.")
    except (OSError, subprocess.SubprocessError) as e:
        print(f"Error running rebuild script: {e}, continuing with update.")
    finally:
        os.chdir(original_cwd)


def update_repositories(basedir, force=False):
    """Update all repositories and return status results"""
    status_results = []
    versions_config = load_versions_config()

    for repo_name, config in versions_config.items():
        try:
            update_repo(
                basedir,
                repo_name,
                config.get("npm_install", False),
                config.get("npm_build", False),
                config.get("url"),
                config.get("version", "latest"),
                force,
            )
            status_results.append(
                {
                    "Component": f"Update {repo_name}",
                    "Status": "✓ PASS",
                    "Details": f"Updated to {config.get('version', 'latest')}",
                }
            )
        except SystemExit:
            status_results.append(
                {
                    "Component": f"Update {repo_name}",
                    "Status": "✗ FAIL",
                    "Details": "Update failed",
                }
            )

    return status_results


def check_environment():
    """Check environment file and return status result"""
    try:
        check_env_file()
        return {
            "Component": "Environment Check",
            "Status": "✓ PASS",
            "Details": ".env file verified",
        }
    except SystemExit:
        return {
            "Component": "Environment Check",
            "Status": "✗ FAIL",
            "Details": ".env file issues",
        }


def set_permissions():
    """Set file permissions for all components and return status result"""
    try:
        for component in COMPONENTS_WITH_PERMISSIONS:
            chown_recursive(component, TARGET_UID, TARGET_GID)
        return {
            "Component": "Permissions",
            "Status": "✓ PASS",
            "Details": "Ownership set for all components",
        }
    except OSError as e:
        return {
            "Component": "Permissions",
            "Status": "⚠️  SKIP",
            "Details": f"Permission denied (run as root for proper ownership): {e}",
        }


def check_and_rebuild_images():
    """Check container image ages and rebuild if needed, return status result"""
    images_to_check = [
        ("visp-operations-session", "docker/session-manager/operations-session"),
        ("visp-rstudio-session", "docker/session-manager/rstudio-session"),
        ("visp-jupyter-session", "docker/session-manager/jupyter-session"),
        ("visp-emu-webapp", "docker/emu-webapp"),
        ("visp-session-manager-dev", "docker/session-manager/build-context"),
        ("visp-whisper", "docker/whisper"),
        ("visp-apache", "docker/apache"),
        ("visp-octra", "docker/octra"),
        ("visp-emu-webapp-server", "docker/emu-webapp-server"),
        ("visp-wsrng-server", "docker/wsrng-server"),
    ]

    old_images = []
    for image, source in images_to_check:
        if check_image_age(image, source):
            old_images.append(image)

    if old_images:
        try:
            rebuild_images()
            return {
                "Component": "Docker Images",
                "Status": "✓ REBUILT",
                "Details": f"Rebuilt: {', '.join(old_images)}",
            }
        except SystemExit:
            return {
                "Component": "Docker Images",
                "Status": "✗ FAIL",
                "Details": f"Rebuild failed for: {', '.join(old_images)}",
            }
    else:
        return {
            "Component": "Docker Images",
            "Status": "✓ UP TO DATE",
            "Details": "All images current",
        }


def print_update_summary(status_results):
    """Print the update summary table with counters"""
    print("\n" + "=" * 80)
    print("VISIBLE SPEECH DEPLOYMENT UPDATE SUMMARY")
    print("=" * 80)
    print(tabulate(status_results, headers="keys", tablefmt="grid"))

    # Count results
    total = len(status_results)
    passed = sum(
        1
        for r in status_results
        if "PASS" in r["Status"]
        or "REBUILT" in r["Status"]
        or "UP TO DATE" in r["Status"]
    )
    failed = total - passed

    print("=" * 80)
    print(f"Summary: {passed}/{total} components successful")
    if failed > 0:
        print(f"Failed: {failed} component(s)")
    print("=" * 80)


def update_system(force=False):
    BASEDIR = os.getcwd()
    status_results = []

    # Update repositories first (as requested)
    print("🔄 Updating repositories...")
    repo_results = update_repositories(BASEDIR, force)
    status_results.extend(repo_results)

    # Check environment
    print("🔍 Checking environment...")
    env_result = check_environment()
    status_results.append(env_result)

    # Set initial permissions
    print("🔒 Setting initial permissions...")
    perm_result = set_permissions()
    status_results.append(perm_result)

    # Check and rebuild images
    print("🐳 Checking Docker images...")
    image_result = check_and_rebuild_images()
    status_results.append(image_result)

    # Set final permissions
    print("🔒 Setting final permissions...")
    final_perm_result = set_permissions()
    status_results.append(final_perm_result)

    # Print summary with counters
    print_update_summary(status_results)


def check_repositories_status(fetch=True):
    """Check status of all repositories and report uncommitted changes"""
    print("🔍 Checking repository status...")

    if fetch:
        print("📡 Fetching latest remote information...")

    # Save original directory
    original_cwd = os.getcwd()

    versions_config = load_versions_config()

    status_results = []
    repos_with_changes = []
    repos_ahead = []
    repos_behind = []

    for repo_name, config in versions_config.items():
        repo_path = os.path.join(original_cwd, repo_name)

        if not os.path.exists(repo_path):
            status_results.append(
                {
                    "Repository": repo_name,
                    "Status": "❌ MISSING",
                    "Local Changes": "Repository not cloned",
                    "Sync Status": "N/A",
                }
            )
            continue

        if not os.path.exists(os.path.join(repo_path, ".git")):
            status_results.append(
                {
                    "Repository": repo_name,
                    "Status": "❌ NOT GIT",
                    "Local Changes": "Not a git repository",
                    "Sync Status": "N/A",
                }
            )
            continue

        try:
            os.chdir(repo_path)

            # Fetch latest remote information if requested
            if fetch:
                try:
                    subprocess.run(
                        ["git", "fetch", "--quiet", "origin"],
                        capture_output=True,
                        check=True,
                    )
                except subprocess.CalledProcessError:
                    # Fetch failed, continue with cached data
                    pass

            # Check for uncommitted changes
            has_changes = check_uncommitted_changes(repo_path)

            # Check sync status with remote
            sync_status = "✅ SYNCED"
            sync_details = []

            try:
                # Get current branch
                branch_result = subprocess.run(
                    ["git", "branch", "--show-current"],
                    capture_output=True,
                    text=True,
                    check=True,
                )
                current_branch = branch_result.stdout.strip()

                if not current_branch:
                    current_branch = "main"  # fallback

                # Check if remote exists
                remote_result = subprocess.run(
                    ["git", "remote", "get-url", "origin"],
                    capture_output=True,
                    text=True,
                )

                if remote_result.returncode == 0:
                    # Check commits ahead/behind
                    status_result = subprocess.run(
                        [
                            "git",
                            "rev-list",
                            "--count",
                            "--left-right",
                            f"origin/{current_branch}...HEAD",
                        ],
                        capture_output=True,
                        text=True,
                    )

                    if status_result.returncode == 0:
                        ahead_behind = status_result.stdout.strip().split()
                        if len(ahead_behind) == 2:
                            behind_count = int(ahead_behind[0])  # commits behind remote
                            ahead_count = int(
                                ahead_behind[1]
                            )  # commits ahead of remote

                            if ahead_count > 0:
                                repos_ahead.append(repo_name)
                                sync_details.append(f"🚀 {ahead_count} ahead")
                                sync_status = "🚀 AHEAD"

                            if behind_count > 0:
                                repos_behind.append(repo_name)
                                sync_details.append(f"⬇️ {behind_count} behind")
                                sync_status = (
                                    "⬇️ BEHIND"
                                    if sync_status == "✅ SYNCED"
                                    else "🔄 DIVERGED"
                                )
                        else:
                            sync_details.append("Remote branch not found")
                            sync_status = "❓ NO REMOTE BRANCH"
                    else:
                        sync_details.append("Cannot check remote status")
                        sync_status = "❓ UNKNOWN"
                else:
                    sync_details.append("No remote configured")
                    sync_status = "🏠 LOCAL ONLY"

            except subprocess.CalledProcessError:
                sync_details.append("Error checking remote")
                sync_status = "❌ ERROR"

            os.chdir(os.getcwd())  # Go back to original directory

            # Determine overall status
            if has_changes:
                repos_with_changes.append(repo_name)
                overall_status = "⚠️  HAS CHANGES"
                changes_desc = "Uncommitted changes present"
            else:
                overall_status = "✅ CLEAN"
                changes_desc = "No uncommitted changes"

            # Combine sync details
            sync_desc = ", ".join(sync_details) if sync_details else "Up to date"

            status_results.append(
                {
                    "Repository": repo_name,
                    "Status": overall_status,
                    "Local Changes": changes_desc,
                    "Sync Status": f"{sync_status} - {sync_desc}",
                }
            )

        except Exception as e:
            os.chdir(original_cwd)  # Make sure we return to original directory
            status_results.append(
                {
                    "Repository": repo_name,
                    "Status": "❌ ERROR",
                    "Local Changes": f"Error checking status: {str(e)}",
                    "Sync Status": "N/A",
                }
            )

    # Print results
    print("\n" + "=" * 100)
    print("REPOSITORY STATUS CHECK")
    print("=" * 100)
    print(tabulate(status_results, headers="keys", tablefmt="grid"))
    print("=" * 100)

    # Summary
    summary_lines = []
    if repos_with_changes:
        summary_lines.append(
            f"⚠️  Repositories with uncommitted changes: {', '.join(repos_with_changes)}"
        )
        summary_lines.append(
            f"   Total: {len(repos_with_changes)} repo(s) have local changes"
        )

    if repos_ahead:
        summary_lines.append(
            f"🚀 Repositories ahead of remote: {', '.join(repos_ahead)}"
        )
        summary_lines.append(f"   Total: {len(repos_ahead)} repo(s) need to push")

    if repos_behind:
        summary_lines.append(
            f"⬇️  Repositories behind remote: {', '.join(repos_behind)}"
        )
        summary_lines.append(f"   Total: {len(repos_behind)} repo(s) need to pull")

    if not repos_with_changes and not repos_ahead and not repos_behind:
        summary_lines.append("✅ All repositories are clean and synced!")
    else:
        summary_lines.append("   Use 'git status' in each repo for details")
        summary_lines.append(
            "   Run update with --force to stash local changes before updating"
        )

    for line in summary_lines:
        print(line)
    print("=" * 100)


def main():
    parser = argparse.ArgumentParser(description="VISP Deployment Manager")
    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    # Install command
    install_parser = subparsers.add_parser("install", help="Install VISP system")
    install_parser.add_argument(
        "--auto-passwords",
        action="store_true",
        default=True,
        help="Auto-generate passwords (default)",
    )
    install_parser.add_argument(
        "--interactive-passwords",
        action="store_true",
        help="Prompt for passwords interactively",
    )

    # Update command
    update_parser = subparsers.add_parser(
        "update", help="Update VISP system components"
    )
    update_parser.add_argument(
        "--rebuild-images",
        action="store_true",
        help="Rebuild Docker images if outdated",
    )
    update_parser.add_argument(
        "--force",
        action="store_true",
        help="Force update even with uncommitted changes",
    )

    # Status command
    status_parser = subparsers.add_parser(
        "status", help="Check status of all repositories for uncommitted changes"
    )
    status_parser.add_argument(
        "--no-fetch",
        action="store_true",
        help="Skip fetching from remotes (use cached remote state)",
    )

    args = parser.parse_args()

    if args.command == "install":
        install_system()
    elif args.command == "update":
        update_system(force=getattr(args, "force", False))
    elif args.command == "status":
        check_repositories_status(fetch=not getattr(args, "no_fetch", False))
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
