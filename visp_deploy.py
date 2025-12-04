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

    HAS_TABULATE = True
except ImportError:
    HAS_TABULATE = False

    # Fallback simple table formatter
    def tabulate(data, headers=None, tablefmt=None):
        """Simple fallback when tabulate is not available"""
        if not data:
            return ""
        if headers == "keys" and data:
            headers = list(data[0].keys())
        output = []
        if headers:
            output.append(" | ".join(str(h) for h in headers))
            output.append("-" * (len(output[0])))
        for row in data:
            if isinstance(row, dict):
                output.append(" | ".join(str(row.get(h, "")) for h in headers))
            else:
                output.append(" | ".join(str(v) for v in row))
        return "\n".join(output)


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


def chown_recursive(path, uid, gid):
    """Recursively change ownership of path using system chown command for speed"""
    try:
        # Using subprocess is 10-100x faster than os.walk for large directories like node_modules
        subprocess.run(
            ["chown", "-R", f"{uid}:{gid}", path], check=True, capture_output=True
        )
    except subprocess.CalledProcessError as e:
        print(f"Failed to chown {path}: {e}")
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


def get_repo_url(name, config):
    """Get repository URL from config or generate default GitHub URL"""
    url = config.get("url")
    if url:
        return url
    return f"https://github.com/humlab-speech/{name}.git"


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


def update_env_var(content, key, value):
    """Safely update or add an environment variable in .env content

    This function properly handles:
    - Exact key matching (won't confuse API_KEY with GOOGLE_API_KEY)
    - Values containing = signs
    - Adding new variables if they don't exist
    """
    lines = content.split("\n")
    updated = False

    for i, line in enumerate(lines):
        # Skip comments and empty lines
        if not line.strip() or line.strip().startswith("#"):
            continue

        # Check if this line defines the variable we're looking for
        if "=" in line:
            line_key = line.split("=", 1)[0].strip()
            if line_key == key:
                lines[i] = f"{key}={value}"
                updated = True
                break

    # If variable wasn't found, add it at the end
    if not updated:
        lines.append(f"{key}={value}")

    return "\n".join(lines)


def setup_env_file(auto_passwords=True, interactive=False):
    if not os.path.exists(".env"):
        if os.path.exists(".env-example"):
            shutil.copy(".env-example", ".env")
        else:
            print("Error: .env-example not found")
            return

    # Read current content
    with open(".env", "r", encoding="utf-8") as f:
        content = f.read()

    # Set defaults using safe update function
    env_updates = {"ABS_ROOT_PATH": os.getcwd(), "ADMIN_EMAIL": "admin@visp.local"}
    for key, value in env_updates.items():
        content = update_env_var(content, key, value)

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
        "SSP_ADMIN_PASSWORD": "local",
        "SSP_SALT": "local",
        "MONGO_EXPRESS_PASSWORD": "local",
    }

    for var, ptype in password_vars.items():
        # Special handling for MongoDB password if data already exists
        if var == "MONGO_ROOT_PASSWORD" and mongo_data_exists:
            # Parse current value safely
            current_value = ""
            for line in content.split("\n"):
                if line.strip().startswith(f"{var}="):
                    current_value = line.split("=", 1)[1] if "=" in line else ""
                    break

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
                    content = update_env_var(content, var, password)
                continue

        if auto_passwords or ptype == "local":
            random_value = generate_random_string()
            content = update_env_var(content, var, random_value)
        elif interactive:
            # Check if variable exists and has a value
            has_value = False
            for line in content.split("\n"):
                if line.strip().startswith(f"{var}="):
                    value_part = line.split("=", 1)[1] if "=" in line else ""
                    has_value = bool(value_part.strip())
                    break

            if not has_value:
                password = getpass.getpass(f"Enter {var}: ")
                content = update_env_var(content, var, password)

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

        # Generate all missing values using safe update function
        for var in missing:
            random_value = generate_random_string()
            content = update_env_var(content, var, random_value)

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


def setup_docker_compose_mode(mode="dev"):
    """Setup docker-compose.yml symlink for the specified mode"""
    compose_file = "docker-compose.yml"
    target_file = f"docker-compose.{mode}.yml"

    if os.path.islink(compose_file):
        current_target = os.readlink(compose_file)
        if current_target == target_file:
            print(f"✓ Docker Compose is already configured for {mode} mode")
        else:
            print(f"⚠️  Docker Compose is already linked to {current_target}")
            print(
                "   Keeping existing configuration. To change mode, manually update the symlink."
            )
    elif os.path.exists(compose_file):
        print(f"⚠️  {compose_file} already exists as a regular file")
        print(
            f"   Keeping existing file. To use mode-based configuration, manually create symlink to {target_file}"
        )
    else:
        try:
            os.symlink(target_file, compose_file)
            print(
                f"✓ Created docker-compose.yml symlink pointing to {target_file} ({mode} mode)"
            )
        except OSError as e:
            print(f"⚠️  Could not create symlink: {e}")
            print(f"   You can manually create: ln -s {target_file} {compose_file}")


def create_required_directories():
    """Create required directories and log files for VISP"""
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
    # Only create mongodb.log if it doesn't exist (avoid permission issues with running containers)
    if not os.path.exists("mounts/mongo/logs/mongodb.log"):
        with open("mounts/mongo/logs/mongodb.log", "w", encoding="utf-8"):
            pass
    os.makedirs("certs", exist_ok=True)
    os.makedirs("mounts/transcription-queued", exist_ok=True)


def generate_ssl_certificates():
    """Generate SSL certificates for VISP"""
    # Fetch SWAMID cert - this is optional, warn if it fails
    try:
        run_command(
            "curl -f http://mds.swamid.se/md/md-signer2.crt -o certs/md-signer2.crt",
            "Fetching SWAMID cert",
        )
    except subprocess.CalledProcessError as e:
        print("⚠️  Warning: Could not fetch SWAMID certificate from mds.swamid.se")
        print(f"   Error: {e}")
        print("   SWAMID authentication may not work properly.")
        print("   You can manually download it later if needed.")

    # Generate self-signed certs for local development
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


def verify_repository_content(repo_path, name):
    """Verify that a cloned repository has actual content"""
    print(f"Verifying clone of {name}...")
    try:
        status_result = subprocess.run(
            ["git", "status", "--short"],
            cwd=repo_path,
            capture_output=True,
            text=True,
            check=True,
        )
        log_result = subprocess.run(
            ["git", "log", "-1", "--oneline"],
            cwd=repo_path,
            capture_output=True,
            text=True,
            check=True,
        )
        file_count_result = subprocess.run(
            ["find", ".", "-type", "f", "-not", "-path", "./.git/*"],
            cwd=repo_path,
            capture_output=True,
            text=True,
            check=True,
        )
        file_count = len([f for f in file_count_result.stdout.strip().split("\n") if f])

        print("  Repository state:")
        print(f"    Path: {repo_path}")
        print(f"    Latest commit: {log_result.stdout.strip()}")
        print(f"    File count: {file_count}")
        if status_result.stdout.strip():
            print(f"    Git status: {status_result.stdout.strip()}")
        else:
            print("    Git status: clean working tree")

        # Sanity check: repository should have at least some files
        if file_count == 0:
            print(f"✗ Warning: {name} appears to be empty (0 files)")
            return False

        return True
    except subprocess.CalledProcessError as e:
        print(f"✗ Failed to verify {name}: {e}")
        return False


def clone_repositories(basedir):
    """Clone all required repositories from versions.json"""
    versions_config = load_versions_config()

    print("\n📦 Cloning repositories...")
    print(f"Working directory: {os.getcwd()}")
    failed_repos = []

    for name, config in versions_config.items():
        repo_path = os.path.join(basedir, "external", name)

        # Check if directory exists and whether it's a valid git repo
        needs_clone = False
        if os.path.exists(repo_path):
            # Directory exists - check if it's a valid git repository with content
            if not os.path.exists(os.path.join(repo_path, ".git")):
                print(
                    f"⚠️  {name} exists but is not a git repository - will remove and re-clone"
                )
                needs_clone = True
                try:
                    shutil.rmtree(repo_path)
                except OSError as e:
                    print(f"✗ Failed to remove invalid directory {repo_path}: {e}")
                    failed_repos.append(name)
                    continue
            else:
                # It's a git repo - verify it has files
                try:
                    file_count_result = subprocess.run(
                        ["find", ".", "-type", "f", "-not", "-path", "./.git/*"],
                        cwd=repo_path,
                        capture_output=True,
                        text=True,
                        check=True,
                    )
                    file_count = len(
                        [f for f in file_count_result.stdout.strip().split("\n") if f]
                    )

                    if file_count == 0:
                        print(
                            f"⚠️  {name} appears to be empty (0 files) - will remove and re-clone"
                        )
                        needs_clone = True
                        try:
                            shutil.rmtree(repo_path)
                        except OSError as e:
                            print(
                                f"✗ Failed to remove empty repository {repo_path}: {e}"
                            )
                            failed_repos.append(name)
                            continue
                    else:
                        # Repository exists - pull latest updates
                        print(
                            f"⊙ Repository {name} already exists at {repo_path} ({file_count} files), updating..."
                        )
                        try:
                            subprocess.run(
                                ["git", "fetch", "--all"],
                                cwd=repo_path,
                                check=True,
                                capture_output=True,
                            )

                            # Get current branch
                            branch_result = subprocess.run(
                                ["git", "branch", "--show-current"],
                                cwd=repo_path,
                                capture_output=True,
                                text=True,
                                check=True,
                            )
                            current_branch = branch_result.stdout.strip()
                            if not current_branch:
                                current_branch = "main"

                            # Pull latest changes (try main first, then master)
                            try:
                                subprocess.run(
                                    [
                                        "git",
                                        "pull",
                                        "--ff-only",
                                        "origin",
                                        current_branch,
                                    ],
                                    cwd=repo_path,
                                    capture_output=True,
                                    text=True,
                                    check=True,
                                )
                                print(f"  ✓ Updated {name} from remote")
                            except subprocess.CalledProcessError:
                                # Try master if main doesn't work
                                try:
                                    subprocess.run(
                                        [
                                            "git",
                                            "pull",
                                            "--ff-only",
                                            "origin",
                                            "master",
                                        ],
                                        cwd=repo_path,
                                        capture_output=True,
                                        text=True,
                                        check=True,
                                    )
                                    print(
                                        f"  ✓ Updated {name} from remote (master branch)"
                                    )
                                except subprocess.CalledProcessError as e:
                                    print(
                                        f"  ⚠️  Could not pull updates for {name}: {e}"
                                    )
                                    print(
                                        "     Repository may have uncommitted changes or diverged from remote"
                                    )
                        except subprocess.CalledProcessError as e:
                            print(f"  ⚠️  Failed to fetch updates for {name}: {e}")
                        continue
                except subprocess.CalledProcessError as e:
                    print(f"✗ Failed to verify {name}: {e}")
                    failed_repos.append(name)
                    continue
        else:
            needs_clone = True

        # Clone the repository
        if needs_clone:
            url = get_repo_url(name, config)
            try:
                print(f"Cloning {name} from {url}...")
                run_command(f"git clone {url} {repo_path}", f"Cloning {name}")

                # Verify the clone succeeded
                if not os.path.exists(repo_path):
                    print(
                        f"✗ Failed to clone {name} - directory not created at {repo_path}"
                    )
                    failed_repos.append(name)
                    continue

                # Verify repository content
                if not verify_repository_content(repo_path, name):
                    failed_repos.append(name)
                    continue

                # Checkout specific version if not "latest"
                version = config.get("version", "latest")
                if version != "latest":
                    os.chdir(repo_path)
                    run_command(
                        f"git checkout {version}",
                        f"Checking out {version} for {name}",
                    )
                    os.chdir(basedir)
                print(f"✓ Successfully cloned {name} to {repo_path}")
            except subprocess.CalledProcessError as e:
                print(f"✗ Failed to clone {name}: {e}")
                failed_repos.append(name)

    # Check if any critical repositories failed
    if failed_repos:
        print(f"\n⚠️  WARNING: Failed to clone {len(failed_repos)} repository(ies):")
        for repo in failed_repos:
            print(f"  - {repo}")
        print("\nInstallation incomplete. Please resolve the issues above.")
        print("You may need to:")
        print("  1. Check your internet connection")
        print("  2. Verify git is installed")
        print("  3. Check repository URLs in versions.json")
        print("  4. Manually clone missing repositories")
        sys.exit(1)

    print(f"✓ All {len(versions_config)} repositories ready")


def fix_repository_permissions():
    """Ensure repository directories have correct permissions for container access"""
    print("\n🔒 Setting repository permissions for container access...")

    versions_config = load_versions_config()

    for name in versions_config.keys():
        repo_path = os.path.join(os.getcwd(), "external", name)
        if os.path.exists(repo_path):
            try:
                # Make directories readable/executable by all (755)
                for root, dirs, files in os.walk(repo_path):
                    # Skip .git directory to avoid issues
                    if ".git" in root:
                        continue

                    # Set directory permissions - logs dirs need to be writable
                    for dir_name in dirs:
                        dir_path = os.path.join(root, dir_name)
                        try:
                            # logs directories need to be writable by containers
                            if dir_name == "logs":
                                os.chmod(dir_path, 0o777)
                            else:
                                os.chmod(dir_path, 0o755)
                        except OSError:
                            pass  # Skip if permission denied

                    # Set file permissions to 644, log files to 666
                    for file_name in files:
                        file_path = os.path.join(root, file_name)
                        try:
                            if file_name.endswith(".log"):
                                os.chmod(file_path, 0o666)
                            else:
                                os.chmod(file_path, 0o644)
                        except OSError:
                            pass  # Skip if permission denied

                print(f"  ✓ Fixed permissions for {name}")
            except Exception as e:
                print(f"  ⚠️  Could not fix all permissions for {name}: {e}")

    # Also fix mounts directory permissions
    if os.path.exists("mounts"):
        try:
            for root, dirs, files in os.walk("mounts"):
                for dir_name in dirs:
                    dir_path = os.path.join(root, dir_name)
                    try:
                        # logs directories need full write access
                        if dir_name == "logs" or "log" in dir_name:
                            os.chmod(dir_path, 0o777)
                        else:
                            os.chmod(dir_path, 0o755)
                    except OSError:
                        pass

                for file_name in files:
                    file_path = os.path.join(root, file_name)
                    try:
                        # Log files need to be writable by containers
                        if file_name.endswith(".log"):
                            os.chmod(file_path, 0o666)
                        else:
                            os.chmod(file_path, 0o644)
                    except OSError:
                        pass
            print("  ✓ Fixed permissions for mounts directory")
        except Exception as e:
            print(f"  ⚠️  Could not fix all permissions for mounts: {e}")

    print("✓ Permission setup complete")


def setup_service_env_files():
    """Setup environment files for individual services"""
    # Setup emu-webapp-server .env
    os.makedirs("mounts/emu-webapp-server/logs", exist_ok=True)

    # Ensure logs directory exists in the source repo for dev mode (required due to volume mount)
    if os.path.exists("external/emu-webapp-server"):
        os.makedirs("external/emu-webapp-server/logs", exist_ok=True)
        # Set permissions for the logs directory to ensure container can write to it
        try:
            os.chmod("external/emu-webapp-server/logs", 0o777)
        except OSError:
            pass

    run_command(
        "curl -L https://raw.githubusercontent.com/humlab-speech/emu-webapp-server/main/.env-example "
        "-o ./mounts/emu-webapp-server/.env",
        "Fetching emu-webapp-server .env",
    )

    # Setup wsrng-server .env (copy from .env-example and fill in MongoDB password)
    if os.path.exists("external/wsrng-server/.env-example"):
        if not os.path.exists("external/wsrng-server/.env"):
            shutil.copy(
                "external/wsrng-server/.env-example", "external/wsrng-server/.env"
            )
            print("Created wsrng-server/.env from .env-example")

            # Read the main .env to get MongoDB password
            mongo_password = ""
            if os.path.exists(".env"):
                with open(".env", "r", encoding="utf-8") as f:
                    for line in f:
                        if line.startswith("MONGO_ROOT_PASSWORD="):
                            mongo_password = line.split("=", 1)[1].strip()
                            break

            # Update wsrng-server/.env with MongoDB password
            if mongo_password:
                with open("external/wsrng-server/.env", "r", encoding="utf-8") as f:
                    content = f.read()
                content = content.replace(
                    "MONGO_PASSWORD=", f"MONGO_PASSWORD={mongo_password}"
                )
                with open("external/wsrng-server/.env", "w", encoding="utf-8") as f:
                    f.write(content)
                print("Configured wsrng-server/.env with MongoDB credentials")


def build_components(basedir):
    """Build all components using temporary Node.js containers based on versions.json config"""
    versions_config = load_versions_config()

    # Read WEBCLIENT_BUILD from .env for webclient builds
    webclient_build_cmd = "visp-build"  # default
    if os.path.exists(".env"):
        with open(".env", "r", encoding="utf-8") as f:
            for line in f:
                if line.strip().startswith("WEBCLIENT_BUILD="):
                    webclient_build_cmd = line.split("=", 1)[1].strip()
                    break

    print("\nBuilding components using temporary Node.js containers...")
    print(f"Webclient will be built with: npm run {webclient_build_cmd}")
    for name, config in versions_config.items():
        comp_path = os.path.join(basedir, name)

        # Skip if component doesn't exist
        if not os.path.exists(comp_path):
            continue

        commands = []

        # Build npm install command based on config
        if config.get("npm_install", False):
            # Special case for webclient which needs legacy-peer-deps
            if name == "webclient":
                commands.append("npm install --legacy-peer-deps")
            else:
                commands.append("npm install")

        # Build npm build command based on config
        if config.get("npm_build", False):
            # For webclient, use the WEBCLIENT_BUILD setting from .env
            if name == "webclient":
                commands.append(f"npm run {webclient_build_cmd}")
            else:
                commands.append("npm run build")

        # Execute commands if any
        for cmd in commands:
            # EMU-webApp uses webpack 4 which requires legacy OpenSSL algorithms
            # Use --openssl-legacy-provider flag for Node 17+ compatibility
            if name == "EMU-webApp" and "npm run build" in cmd:
                run_command(
                    f"docker run --rm -v {comp_path}:/app -w /app node:20 sh -c "
                    f"'export NODE_OPTIONS=--openssl-legacy-provider && {cmd}'",
                    f"Building {name}: {cmd} (with legacy OpenSSL)",
                )
            else:
                # Use temporary container for builds instead of host Node.js
                # This ensures clean host and consistent versioning
                run_command(
                    f"docker run --rm -v {comp_path}:/app -w /app node:20 {cmd}",
                    f"Building {name}: {cmd}",
                )

    print("\nNote: Dependencies are installed based on versions.json configuration.")
    print(
        "In development mode (docker-compose.dev.yml), source code is mounted for hot-reload."
    )
    print(
        "In production mode (docker-compose.prod.yml), run 'docker compose build' to bake code into images."
    )


def install_system(mode="dev"):
    """Install VISP system with all required components"""
    print("Starting VISP installation...")
    BASEDIR = os.getcwd()

    # Check for dependencies (non-root, just checks)
    check_dependencies()

    # Check permissions early
    check_root_permissions()

    # Setup .env
    setup_env_file(auto_passwords=True)

    # Setup docker-compose mode
    setup_docker_compose_mode(mode)

    # Create directories
    create_required_directories()

    # Generate SSL certificates
    generate_ssl_certificates()

    # Clone all repos
    clone_repositories(BASEDIR)

    # Fix permissions for container access
    fix_repository_permissions()

    # Setup service-specific .env files
    setup_service_env_files()

    # Build all components
    build_components(BASEDIR)

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
    repo_path = os.path.join(basedir, "external", name)

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
            # Use latest (main/master branch) - try main first, then master
            try:
                # Check if local branch has diverged from remote
                result = subprocess.run(
                    ["git", "rev-list", "--count", "HEAD..origin/main"],
                    capture_output=True,
                    text=True,
                    check=True,
                )
                behind = int(result.stdout.strip())

                result = subprocess.run(
                    ["git", "rev-list", "--count", "origin/main..HEAD"],
                    capture_output=True,
                    text=True,
                    check=True,
                )
                ahead = int(result.stdout.strip())

                if ahead > 0:
                    print(
                        f"⚠️  Warning: {name} has {ahead} local commit(s) not on remote"
                    )
                    if force:
                        print(
                            "   Force mode: Discarding local commits and resetting to origin/main"
                        )
                        subprocess.run(
                            ["git", "reset", "--hard", "origin/main"], check=True
                        )
                    else:
                        print(
                            "   Attempting to rebase local commits onto origin/main..."
                        )
                        try:
                            subprocess.run(["git", "rebase", "origin/main"], check=True)
                            print(f"✓ Successfully rebased {name}")
                        except subprocess.CalledProcessError:
                            print(
                                f"✗ Rebase failed for {name}. Use --force to discard local commits"
                            )
                            subprocess.run(["git", "rebase", "--abort"], check=False)
                            raise
                elif behind > 0:
                    print(f"Updating {name} ({behind} commit(s) behind)")
                    subprocess.run(
                        ["git", "merge", "--ff-only", "origin/main"], check=True
                    )
                else:
                    print(f"{name} is up to date")

            except subprocess.CalledProcessError:
                # Try master branch if main doesn't exist
                try:
                    result = subprocess.run(
                        ["git", "rev-list", "--count", "HEAD..origin/master"],
                        capture_output=True,
                        text=True,
                        check=True,
                    )
                    behind = int(result.stdout.strip())

                    result = subprocess.run(
                        ["git", "rev-list", "--count", "origin/master..HEAD"],
                        capture_output=True,
                        text=True,
                        check=True,
                    )
                    ahead = int(result.stdout.strip())

                    if ahead > 0:
                        print(
                            f"⚠️  Warning: {name} has {ahead} local commit(s) not on remote"
                        )
                        if force:
                            print(
                                "   Force mode: Discarding local commits and resetting to origin/master"
                            )
                            subprocess.run(
                                ["git", "reset", "--hard", "origin/master"], check=True
                            )
                        else:
                            print(
                                "   Attempting to rebase local commits onto origin/master..."
                            )
                            try:
                                subprocess.run(
                                    ["git", "rebase", "origin/master"], check=True
                                )
                                print(f"✓ Successfully rebased {name}")
                            except subprocess.CalledProcessError:
                                print(
                                    f"✗ Rebase failed for {name}. Use --force to discard local commits"
                                )
                                subprocess.run(
                                    ["git", "rebase", "--abort"], check=False
                                )
                                raise
                    elif behind > 0:
                        print(f"Updating {name} ({behind} commit(s) behind)")
                        subprocess.run(
                            ["git", "merge", "--ff-only", "origin/master"], check=True
                        )
                    else:
                        print(f"{name} is up to date")
                except subprocess.CalledProcessError:
                    print(f"Could not determine remote tracking branch for {name}")
                    raise

    except subprocess.CalledProcessError as e:
        print(f"Git update of {name} failed: {e}")
        sys.exit(1)

    if npm_install:
        if os.path.exists("node_modules"):
            # Remove existing node_modules using Docker to handle permissions
            run_command(
                f"docker run --rm -v {repo_path}:/app -w /app alpine sh -c 'rm -rf node_modules'",
                f"Removing old node_modules for {name}",
            )
        run_command(
            f"docker run --rm -v {repo_path}:/app -w /app node:20 npm install --legacy-peer-deps",
            f"Installing npm dependencies for {name}",
        )
    if npm_build:
        # Clean dist directory using Docker
        run_command(
            f"docker run --rm -v {repo_path}:/app -w /app alpine sh -c 'rm -rf dist'",
            f"Cleaning dist for {name}",
        )
        run_command(
            f"docker run --rm -v {repo_path}:/app -w /app node:20 npm run build",
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

    # Fix repository permissions after update
    fix_repository_permissions()

    # Check environment
    print("🔍 Checking environment...")
    env_result = check_environment()
    status_results.append(env_result)

    # Check and rebuild images
    print("🐳 Checking Docker images...")
    image_result = check_and_rebuild_images()
    status_results.append(image_result)

    # Set permissions after all operations complete
    print("🔒 Setting file permissions...")
    perm_result = set_permissions()
    status_results.append(perm_result)

    # Print summary with counters
    print_update_summary(status_results)


def check_webclient_build_config():
    """Check webclient build configuration from .env and compare with actual build"""
    result = {
        "Setting": "WEBCLIENT_BUILD",
        "Expected (.env)": "Not set",
        "Expected Domain": "Unknown",
        "Actual Build": "Unknown",
        "Match Status": "❓ UNKNOWN",
    }

    # Read .env file to get expected build config
    expected_build = "visp-build"  # default
    expected_domain = "Unknown"

    if os.path.exists(".env"):
        with open(".env", "r", encoding="utf-8") as f:
            for line in f:
                if line.strip().startswith("WEBCLIENT_BUILD="):
                    expected_build = line.split("=", 1)[1].strip()
                    result["Expected (.env)"] = expected_build
                elif line.strip().startswith("BASE_DOMAIN="):
                    expected_domain = line.split("=", 1)[1].strip()

    result["Expected Domain"] = expected_domain

    # Map build configs to their expected domains based on environment files
    build_to_domain = {
        "visp-build": "visp.humlab.umu.se",
        "visp-demo-build": "visp-demo.humlab.umu.se",
        "visp-pdf-server-build": "visp.pdf-server.humlab.umu.se",
        "visp-local-build": "visp.local",
        "datalab-build": None,  # Unknown
        "visp.dev-build": None,  # Unknown
        "production": "visp.humlab.umu.se",
    }

    # Check if we can determine actual built domain from dist files
    actual_domain = "Not built"

    # Determine deployment mode to prioritize the right location
    is_dev_mode = False
    if os.path.islink("docker-compose.yml"):
        target = os.readlink("docker-compose.yml")
        is_dev_mode = "dev" in target

    # Try multiple locations:
    # In dev mode: prioritize local dist (it's mounted)
    # In prod mode: prioritize container (dist is baked in)

    check_locations = []

    if is_dev_mode:
        # Dev mode: local dist is mounted, check it first
        check_locations.append(
            ("external/webclient/dist", "Local dist (mounted in dev)")
        )

    # Check if apache container is running and try to check inside it
    try:
        container_check = subprocess.run(
            ["docker", "compose", "ps", "-q", "apache"],
            capture_output=True,
            text=True,
            check=False,
        )
        if container_check.returncode == 0 and container_check.stdout.strip():
            check_locations.append(("docker", "Apache container"))
    except (subprocess.SubprocessError, FileNotFoundError):
        pass

    if not is_dev_mode:
        # Prod mode: also check local dist as fallback
        check_locations.append(("external/webclient/dist", "Local dist (fallback)"))

    for location, location_desc in check_locations:
        if location == "docker":
            # Check inside running container
            try:
                # Look for index.html which should contain the domain
                domain_pattern = (
                    "(visp\\.local|visp\\.humlab\\.umu\\.se|"
                    "visp-demo\\.humlab\\.umu\\.se|"
                    "visp\\.pdf-server\\.humlab\\.umu\\.se)"
                )
                docker_result = subprocess.run(
                    [
                        "docker",
                        "compose",
                        "exec",
                        "-T",
                        "apache",
                        "grep",
                        "-r",
                        "-o",
                        "-E",
                        domain_pattern,
                        "/var/www/html/",
                    ],
                    capture_output=True,
                    text=True,
                    check=False,
                    timeout=5,
                )
                if docker_result.returncode == 0 and docker_result.stdout:
                    # Find first domain match (visp.local takes priority)
                    for domain in [
                        "visp.local",
                        "visp.pdf-server.humlab.umu.se",
                        "visp-demo.humlab.umu.se",
                        "visp.humlab.umu.se",
                    ]:
                        if domain in docker_result.stdout:
                            actual_domain = domain
                            result["Actual Build"] = f"Container: '{domain}'"
                            break
            except (subprocess.SubprocessError, subprocess.TimeoutExpired):
                pass
        else:
            # Check local dist folder
            webclient_dist = location
            if os.path.exists(webclient_dist):
                # Try to find any HTML or JS file that might contain the domain
                try:
                    found_files = []
                    # Look for index.html first, then any .html or .js files
                    for root, dirs, files in os.walk(webclient_dist):
                        for file in files:
                            if file.endswith((".html", ".js")):
                                found_files.append(os.path.join(root, file))
                                if len(found_files) >= 5:  # Check up to 5 files
                                    break
                        if found_files:
                            break

                    domain_found = False
                    if found_files:
                        for file_path in found_files:
                            try:
                                with open(
                                    file_path, "r", encoding="utf-8", errors="ignore"
                                ) as f:
                                    content = f.read(
                                        10 * 1024 * 1024
                                    )  # Read first 10MB
                                    # Search for known domain patterns (visp.local first for local dev detection)
                                    for domain in [
                                        "visp.local",
                                        "visp.pdf-server.humlab.umu.se",
                                        "visp-demo.humlab.umu.se",
                                        "visp.humlab.umu.se",
                                    ]:
                                        if domain in content:
                                            actual_domain = domain
                                            result["Actual Build"] = (
                                                f"{location_desc}: '{domain}'"
                                            )
                                            domain_found = True
                                            break
                                    if domain_found:
                                        break
                            except (IOError, OSError):
                                continue
                except (IOError, OSError):
                    pass

        # If we found a domain in this location, stop checking other locations
        if actual_domain != "Not built":
            break

    # Determine if configuration matches
    expected_build_domain = build_to_domain.get(expected_build)

    if actual_domain == "Not built":
        result["Match Status"] = "⚠️  NOT BUILT"
        result["Actual Build"] = "No dist files found (run build or check container)"
    elif expected_build_domain and actual_domain != "Not built":
        if expected_build_domain in actual_domain or actual_domain in str(
            expected_build_domain
        ):
            if expected_domain in actual_domain or actual_domain in expected_domain:
                result["Match Status"] = "✅ CORRECT"
            else:
                result["Match Status"] = (
                    f"⚠️  MISMATCH (built for {actual_domain}, .env expects {expected_domain})"
                )
        else:
            result["Match Status"] = "⚠️  BUILD MISMATCH"
    elif not expected_build_domain:
        result["Match Status"] = "❓ UNKNOWN BUILD TYPE"
    else:
        result["Match Status"] = "❓ CANNOT VERIFY"

    return result


def check_deployment_mode():
    """Check which deployment mode is active (dev vs prod) and what's mounted"""
    result = {
        "Mode": "Unknown",
        "Docker Compose File": "Not found",
        "Webclient Source": "Unknown",
        "Mounted Services": "Unknown",
    }

    # Check which docker-compose file is being used
    compose_file = "docker-compose.yml"
    if os.path.islink(compose_file):
        target = os.readlink(compose_file)
        result["Docker Compose File"] = target

        if "dev" in target:
            result["Mode"] = "🔧 DEVELOPMENT"
        elif "prod" in target:
            result["Mode"] = "🏭 PRODUCTION"
    elif os.path.exists(compose_file):
        result["Docker Compose File"] = "docker-compose.yml (regular file)"

    # Check if webclient dist is mounted (dev mode) or baked in (prod mode)
    mounted_services = []

    try:
        # Read the active docker-compose file
        active_compose = None
        if os.path.islink(compose_file):
            active_compose = os.readlink(compose_file)
        elif os.path.exists(compose_file):
            active_compose = compose_file

        if active_compose and os.path.exists(active_compose):
            with open(active_compose, "r", encoding="utf-8") as f:
                content = f.read()

                # Check for webclient dist mount
                if (
                    "./external/webclient/dist:/var/www/html" in content
                    and not content.count('#- "./external/webclient/dist')
                ):
                    result["Webclient Source"] = (
                        "📁 Mounted from external/webclient/dist"
                    )
                    mounted_services.append("webclient")
                else:
                    result["Webclient Source"] = "📦 Baked into Docker image"

                # Check for other mounted services
                if "./external/session-manager:/session-manager" in content:
                    mounted_services.append("session-manager")
                if "./external/wsrng-server:/wsrng-server" in content:
                    mounted_services.append("wsrng-server")
                if "./external/emu-webapp-server:/home/node/app" in content:
                    mounted_services.append("emu-webapp-server")

                if mounted_services:
                    result["Mounted Services"] = f"🔧 {', '.join(mounted_services)}"
                else:
                    result["Mounted Services"] = "📦 All baked into images"
    except (IOError, OSError):
        pass

    return result


def check_repositories_status(fetch=True):
    """Check status of all repositories and report uncommitted changes"""
    print("🔍 Checking repository status...")

    if fetch:
        print("📡 Fetching latest remote information...")

    # Save original directory
    original_cwd = os.getcwd()

    # Check deployment mode
    deployment_mode = check_deployment_mode()

    # Check webclient build configuration
    webclient_build_config = check_webclient_build_config()

    versions_config = load_versions_config()

    status_results = []
    repos_with_changes = []
    repos_ahead = []
    repos_behind = []

    for repo_name, config in versions_config.items():
        repo_path = os.path.join(original_cwd, "external", repo_name)

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

    # Show deployment mode first
    if deployment_mode:
        print("\n🚀 DEPLOYMENT MODE")
        print("-" * 100)
        print(tabulate([deployment_mode], headers="keys", tablefmt="grid"))
        print()

    # Show webclient build configuration
    if webclient_build_config:
        print("📦 WEBCLIENT BUILD CONFIGURATION")
        print("-" * 100)
        print(tabulate([webclient_build_config], headers="keys", tablefmt="grid"))
        print()

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
    install_parser.add_argument(
        "--mode",
        choices=["dev", "prod"],
        default="dev",
        help="Installation mode: dev (development with source mounts) "
        "or prod (production with baked images). Default: dev",
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
        install_system(mode=getattr(args, "mode", "dev"))
    elif args.command == "update":
        update_system(force=getattr(args, "force", False))
    elif args.command == "status":
        check_repositories_status(fetch=not getattr(args, "no_fetch", False))
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
