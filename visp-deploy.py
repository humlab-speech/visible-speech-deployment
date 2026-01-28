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
from contextlib import contextmanager

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


# =============================================================================
# CONTAINER RUNTIME DETECTION
# =============================================================================

# Global container runtime setting - can be overridden via --runtime flag
_CONTAINER_RUNTIME = None


def detect_container_runtime():
    """
    Detect available container runtime (podman or docker).

    Returns 'podman' if podman is available, otherwise 'docker'.
    This prefers podman for rootless container support.
    """
    global _CONTAINER_RUNTIME

    if _CONTAINER_RUNTIME is not None:
        return _CONTAINER_RUNTIME

    # Check for podman first (preferred for rootless)
    result = subprocess.run(["which", "podman"], capture_output=True, text=True)
    if result.returncode == 0:
        _CONTAINER_RUNTIME = "podman"
        return "podman"

    # Fall back to docker
    result = subprocess.run(["which", "docker"], capture_output=True, text=True)
    if result.returncode == 0:
        _CONTAINER_RUNTIME = "docker"
        return "docker"

    # Neither found
    return None


def set_container_runtime(runtime):
    """Explicitly set the container runtime to use."""
    global _CONTAINER_RUNTIME
    if runtime not in ("docker", "podman"):
        raise ValueError(f"Invalid runtime: {runtime}. Must be 'docker' or 'podman'")
    _CONTAINER_RUNTIME = runtime


def get_container_runtime():
    """Get the currently configured container runtime."""
    return detect_container_runtime()


# =============================================================================
# HELPER CLASSES AND CONTEXT MANAGERS
# =============================================================================


@contextmanager
def working_directory(path):
    """
    Context manager for safely changing directories.
    Prevents directory state corruption if exceptions occur.

    Usage:
        with working_directory('/some/path'):
            # do work in /some/path
            pass
        # automatically returns to original directory
    """
    prev_cwd = os.getcwd()
    os.chdir(path)
    try:
        yield
    finally:
        os.chdir(prev_cwd)


class GitRepository:
    """
    Encapsulates all Git operations for a repository.
    Centralizes subprocess handling and error management.
    """

    def __init__(self, path, url=None):
        """
        Initialize a Git repository wrapper.

        Args:
            path: Absolute or relative path to the repository
            url: Optional remote URL for the repository
        """
        self.path = os.path.abspath(path) if path else None
        self.url = url

    def exists(self):
        """Check if the repository directory exists."""
        return self.path and os.path.exists(self.path)

    def is_git_repo(self):
        """Check if the path is a valid git repository."""
        if not self.exists():
            return False
        return os.path.exists(os.path.join(self.path, ".git"))

    def run_git(self, args, check=True, capture_output=True):
        """
        Run a git command in the repository.

        Args:
            args: List of git command arguments (without 'git')
            check: Whether to raise exception on non-zero exit
            capture_output: Whether to capture stdout/stderr

        Returns:
            CompletedProcess result if capture_output=True, else None
        """
        cmd = ["git"] + args
        if capture_output:
            result = subprocess.run(
                cmd, cwd=self.path, capture_output=True, text=True, check=check
            )
            return result
        else:
            subprocess.run(cmd, cwd=self.path, check=check)
            return None

    def clone(self, destination=None):
        """Clone the repository to destination (or self.path if not specified)."""
        if not self.url:
            raise ValueError("Cannot clone: no URL specified")
        target = destination or self.path
        subprocess.run(["git", "clone", self.url, target], check=True)
        if destination:
            self.path = os.path.abspath(destination)

    def fetch(self, quiet=True):
        """Fetch all remotes."""
        args = ["fetch", "--all"]
        if quiet:
            args.append("--quiet")
        self.run_git(args)

    def checkout(self, ref, force=False):
        """Checkout a specific ref (branch, tag, commit)."""
        args = ["checkout", ref]
        if force:
            args.insert(1, "-f")
        self.run_git(args)

    def pull(self, rebase=False):
        """Pull from current tracking branch."""
        args = ["pull"]
        if rebase:
            args.append("--rebase")
        self.run_git(args)

    def get_current_commit(self):
        """Get the current commit SHA (full)."""
        try:
            result = self.run_git(["rev-parse", "HEAD"])
            return result.stdout.strip()
        except subprocess.CalledProcessError:
            return None

    def get_current_branch(self):
        """Get the current branch name."""
        try:
            result = self.run_git(["rev-parse", "--abbrev-ref", "HEAD"])
            return result.stdout.strip()
        except subprocess.CalledProcessError:
            return None

    def get_commit_info(self, ref="HEAD"):
        """
        Get detailed information about a commit.

        Returns:
            dict with keys: sha, sha_short, date, subject, author
            or None if the ref doesn't exist
        """
        try:
            # Get full SHA
            sha_result = self.run_git(["rev-parse", ref])
            sha = sha_result.stdout.strip()

            # Get short SHA
            sha_short_result = self.run_git(["rev-parse", "--short", ref])
            sha_short = sha_short_result.stdout.strip()

            # Get commit details
            log_result = self.run_git(["log", "-1", "--format=%ci|%s|%an", ref])
            log_parts = log_result.stdout.strip().split("|", 2)

            return {
                "sha": sha,
                "sha_short": sha_short,
                "date": log_parts[0] if len(log_parts) > 0 else "",
                "subject": log_parts[1] if len(log_parts) > 1 else "",
                "author": log_parts[2] if len(log_parts) > 2 else "",
            }
        except subprocess.CalledProcessError:
            return None

    def count_commits_between(self, from_ref, to_ref):
        """
        Count commits between two refs.

        Returns:
            int: Number of commits from from_ref to to_ref
            Returns 0 if refs are the same or on error
        """
        try:
            result = self.run_git(["rev-list", "--count", f"{from_ref}..{to_ref}"])
            return int(result.stdout.strip())
        except (subprocess.CalledProcessError, ValueError):
            return 0

    def is_dirty(self):
        """Check if there are uncommitted changes."""
        try:
            result = self.run_git(["status", "--porcelain"], check=False)
            return bool(result.stdout.strip())
        except subprocess.CalledProcessError:
            return False

    def get_remote_url(self, remote="origin"):
        """Get the URL for a remote."""
        try:
            result = self.run_git(["remote", "get-url", remote])
            return result.stdout.strip()
        except subprocess.CalledProcessError:
            return None

    def has_remote_branch(self, branch, remote="origin"):
        """Check if a remote branch exists."""
        try:
            self.run_git(["rev-parse", f"{remote}/{branch}"])
            return True
        except subprocess.CalledProcessError:
            return False


class ComponentConfig:
    """
    Manages the versions.json configuration file.
    Encapsulates loading, saving, and manipulation of component versions.
    """

    def __init__(self, filepath="versions.json", defaults=None):
        """
        Initialize configuration manager.

        Args:
            filepath: Path to versions.json file
            defaults: Default configuration dict (uses DEFAULT_VERSIONS_CONFIG if None)
        """
        self.filepath = filepath
        self.defaults = defaults or DEFAULT_VERSIONS_CONFIG
        self.config = self._load()

    def _load(self):
        """Load configuration from file, falling back to defaults."""
        if os.path.exists(self.filepath):
            try:
                with open(self.filepath, "r") as f:
                    data = json.load(f)

                # Extract components from the wrapper structure
                config = data.get("components", data)

                # Merge with defaults to ensure all required fields exist
                for component, default_data in self.defaults.items():
                    if component not in config:
                        config[component] = default_data.copy()
                    else:
                        # Ensure all default fields exist
                        for key, value in default_data.items():
                            if key not in config[component]:
                                config[component][key] = value
                return config
            except (json.JSONDecodeError, IOError) as e:
                print(f"⚠️  Error loading {self.filepath}: {e}")
                print("   Using default configuration")
                return self.defaults.copy()
        return self.defaults.copy()

    def save(self):
        """
        Save configuration to file with backup.
        Creates a timestamped backup before overwriting.
        """
        # Create backup if file exists
        if os.path.exists(self.filepath):
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            backup_path = f"{self.filepath}.backup_{timestamp}"
            shutil.copy2(self.filepath, backup_path)

        # Write new config with wrapper structure
        output = {
            "_comment": (
                "Version can be: 'latest' (tracks main/master), "
                "a git commit SHA, or a git tag. "
                "Use 'locked_version' to record current stable version for rollback."
            ),
            "components": self.config,
        }
        with open(self.filepath, "w") as f:
            json.dump(output, f, indent=2)

    def get_components(self):
        """Get all components as (name, data) tuples."""
        return self.config.items()

    def get_component(self, name):
        """Get a specific component's configuration."""
        return self.config.get(name)

    def get_version(self, name):
        """Get the active version for a component."""
        component = self.config.get(name, {})
        return component.get("version", "latest")

    def get_locked_version(self, name):
        """Get the locked version for a component."""
        component = self.config.get(name, {})
        return component.get("locked_version")

    def set_version(self, name, version):
        """Set the active version for a component."""
        if name in self.config:
            self.config[name]["version"] = version

    def set_locked_version(self, name, version):
        """Set the locked version for a component."""
        if name in self.config:
            self.config[name]["locked_version"] = version

    def lock(self, name, commit_sha):
        """
        Lock a component to a specific commit.
        Sets both version and locked_version to the commit SHA.
        """
        if name in self.config:
            self.config[name]["version"] = commit_sha
            self.config[name]["locked_version"] = commit_sha
            return True
        return False

    def unlock(self, name):
        """
        Unlock a component to track latest.
        Sets version to 'latest' but preserves locked_version for rollback.
        """
        if name in self.config:
            self.config[name]["version"] = "latest"
            # Preserve locked_version for rollback
            return True
        return False

    def rollback(self, name):
        """
        Rollback a component to its locked version.
        Sets version to match locked_version.
        """
        if name in self.config:
            locked = self.config[name].get("locked_version")
            if locked:
                self.config[name]["version"] = locked
                return True
        return False

    def is_locked(self, name):
        """Check if a component is locked (version != 'latest')."""
        return self.get_version(name) != "latest"


class EnvFile:
    """
    Manages .env file operations.
    Encapsulates reading, writing, and updating environment variables.
    """

    def __init__(self, path=".env"):
        """
        Initialize environment file manager.

        Args:
            path: Path to .env file
        """
        self.path = path
        self.vars = {}
        self.comments = {}  # Store comments for each variable
        self._load()

    def _load(self):
        """Load variables from .env file if it exists."""
        if not os.path.exists(self.path):
            return

        with open(self.path, "r") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if "=" in line:
                    key, value = line.split("=", 1)
                    self.vars[key.strip()] = value.strip()

    def get(self, key, default=None):
        """Get an environment variable value."""
        return self.vars.get(key, default)

    def set(self, key, value, comment=None):
        """
        Set an environment variable.

        Args:
            key: Variable name
            value: Variable value
            comment: Optional comment to store with the variable
        """
        self.vars[key] = value
        if comment:
            self.comments[key] = comment

    def save(self):
        """Write variables back to .env file."""
        with open(self.path, "w") as f:
            for key, value in sorted(self.vars.items()):
                if key in self.comments:
                    f.write(f"# {self.comments[key]}\n")
                f.write(f"{key}={value}\n")

    def exists(self):
        """Check if .env file exists."""
        return os.path.exists(self.path)


# =============================================================================
# CONFIGURATION CONSTANTS
# =============================================================================


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


# =============================================================================
# HELPER FUNCTIONS
# =============================================================================


def get_repo_url(name, config):
    """Get repository URL from config or generate default GitHub URL"""
    url = config.get("url")
    if url:
        return url
    return f"https://github.com/humlab-speech/{name}.git"


def generate_random_string(length=32):
    """Generate a random string for passwords"""
    chars = string.ascii_letters + string.digits
    return "".join(random.choice(chars) for _ in range(length))


def setup_env_file(auto_passwords=True, interactive=False):
    """
    Setup .env and .env.secrets files with required configuration.
    Uses EnvFile class for safe file manipulation.
    Separates non-sensitive config (.env) from sensitive credentials (.env.secrets).
    """
    # Initialize EnvFile for non-sensitive config
    env = EnvFile(".env")

    # If .env doesn't exist, copy example and reload
    if not env.exists() and os.path.exists(".env-example"):
        shutil.copy(".env-example", ".env")
        env = EnvFile(".env")  # Reload from the copy
    elif not env.exists():
        print("Error: .env-example not found")
        return

    # Initialize EnvFile for sensitive credentials
    secrets = EnvFile(".env.secrets")

    # If .env.secrets doesn't exist, create from template or empty
    if not secrets.exists():
        if os.path.exists(".env.secrets.template"):
            shutil.copy(".env.secrets.template", ".env.secrets")
            secrets = EnvFile(".env.secrets")  # Reload from the copy
        print("📝 Created .env.secrets file for sensitive credentials")

    # 1. Set Basic Defaults (non-sensitive, goes to .env)
    defaults = {"ABS_ROOT_PATH": os.getcwd(), "ADMIN_EMAIL": "admin@visp.local"}
    for key, value in defaults.items():
        if not env.get(key):
            env.set(key, value)

    # 2. Check MongoDB Special Case
    mongo_data_exists = os.path.exists("./mounts/mongo/data") and os.listdir(
        "./mounts/mongo/data"
    )
    current_mongo_pass = secrets.get("MONGO_ROOT_PASSWORD")  # Check secrets file

    if mongo_data_exists and current_mongo_pass:
        print("⚠️  MongoDB database already exists with data.")
        print("   Keeping existing MONGO_ROOT_PASSWORD to avoid authentication issues.")
    elif mongo_data_exists and not current_mongo_pass:
        print(
            "⚠️  WARNING: MongoDB data exists but no MONGO_ROOT_PASSWORD in .env.secrets!"
        )
        if interactive or input("   Set MongoDB password now? (y/n): ").lower() == "y":
            password = getpass.getpass("   Enter MONGO_ROOT_PASSWORD: ")
            secrets.set("MONGO_ROOT_PASSWORD", password, "MongoDB root password")

    # 3. Handle Passwords (all go to .env.secrets)
    password_vars = {
        "POSTGRES_PASSWORD": ("local", "PostgreSQL password"),
        "TEST_USER_LOGIN_KEY": ("local", "Test user login bypass key"),
        "VISP_API_ACCESS_TOKEN": ("local", "API access token"),
        "RSTUDIO_PASSWORD": ("local", "RStudio password"),
        "MONGO_ROOT_PASSWORD": ("local", "MongoDB root password"),
        "ELASTIC_AGENT_FLEET_ENROLLMENT_TOKEN": (
            "local",
            "Elastic agent fleet enrollment token",
        ),
        "MATOMO_DB_PASSWORD": ("local", "Matomo database password"),
        "MATOMO_DB_ROOT_PASSWORD": ("local", "Matomo database root password"),
        "MATOMO_DB_USER": ("local", "Matomo database user"),
        "SSP_ADMIN_PASSWORD": ("local", "SimpleSAMLphp admin password"),
        "SSP_SALT": ("local", "SimpleSAMLphp salt"),
        "MONGO_EXPRESS_PASSWORD": ("local", "Mongo Express password"),
        "MONGO_INITDB_ROOT_PASSWORD": (
            "local",
            "MongoDB init root password (should match MONGO_ROOT_PASSWORD)",
        ),
    }

    for var, (ptype, comment) in password_vars.items():
        # Skip if already set in secrets file
        if secrets.get(var):
            continue

        # Skip Mongo if we handled it above (data exists check)
        if var == "MONGO_ROOT_PASSWORD" and mongo_data_exists:
            continue

        if auto_passwords or ptype == "local":
            password = generate_random_string()
            secrets.set(var, password, comment)

            # Special case: MONGO_INITDB_ROOT_PASSWORD should match MONGO_ROOT_PASSWORD
            if var == "MONGO_ROOT_PASSWORD":
                secrets.set(
                    "MONGO_INITDB_ROOT_PASSWORD",
                    password,
                    "MongoDB init root password (matches MONGO_ROOT_PASSWORD)",
                )
        elif interactive:
            password = getpass.getpass(f"Enter {var}: ")
            secrets.set(var, password, comment)

    # 4. Ensure non-sensitive vars are NOT in .env (they should be in .env only for reference)
    # Remove any password variables from .env if they exist (legacy cleanup)
    for var in password_vars.keys():
        if env.get(var):
            print(f"ℹ️  Removing {var} from .env (moved to .env.secrets)")
            del env.vars[var]

    # Save both files
    env.save()
    secrets.save()
    print("✅ Configuration saved:")
    print("   • .env (non-sensitive config)")
    print("   • .env.secrets (passwords and tokens)")
    print()
    print("ℹ️  Security Note:")
    print("   • .env contains configuration (safe to share in documentation)")
    print("   • .env.secrets contains passwords (NEVER commit to git)")
    print("   • Passwords are managed via Podman Secrets for security")


def check_env_file():
    """
    Check if required environment variables are set in .env and .env.secrets.
    Uses EnvFile class for safe file manipulation.
    """
    env = EnvFile(".env")
    secrets = EnvFile(".env.secrets")

    if not env.exists():
        print(
            "Warning: .env file not found. Please create it from .env-example "
            "and fill in the required values as per the README."
        )
        return

    if not secrets.exists():
        print(
            "Warning: .env.secrets file not found. Please create it from .env.secrets.template "
            "and fill in all password values."
        )
        return

    # Check non-sensitive config in .env
    required_config = ["BASE_DOMAIN", "ADMIN_EMAIL", "ABS_ROOT_PATH"]
    missing_config = [var for var in required_config if not env.get(var)]

    # Check sensitive credentials in .env.secrets
    required_secrets = [
        "POSTGRES_PASSWORD",
        "VISP_API_ACCESS_TOKEN",
        "MONGO_ROOT_PASSWORD",
        "RSTUDIO_PASSWORD",
        "TEST_USER_LOGIN_KEY",
    ]
    missing_secrets = [var for var in required_secrets if not secrets.get(var)]

    missing = missing_config + missing_secrets

    if missing:
        print(
            f"Warning: The following required environment variables are not set in .env: {', '.join(missing)}"
        )
        print("Auto-generating random values for demo deployment...")

        # Generate all missing values using EnvFile class
        for var in missing:
            env.set(var, generate_random_string())

        # Save using the class
        env.save()
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
    # Core dependencies (always required)
    core_dependencies = {
        "git": "Git version control",
        "curl": "curl command-line tool",
        "openssl": "OpenSSL for certificate generation",
    }

    missing = []
    for cmd, name in core_dependencies.items():
        result = subprocess.run(["which", cmd], capture_output=True, text=True)
        if result.returncode != 0:
            missing.append(f"  - {name} ({cmd})")

    # Check for container runtime (podman OR docker)
    runtime = detect_container_runtime()
    if runtime:
        print(f"✓ Container runtime: {runtime}")
    else:
        missing.append("  - Container runtime (podman or docker)")

    if missing:
        print("\n⚠️  WARNING: Missing required dependencies:")
        for dep in missing:
            print(dep)
        print("\nPlease install these dependencies before continuing.")
        print(
            "On Debian/Ubuntu: sudo apt install -y curl git openssl docker.io docker-compose"
        )
        print("Or for Podman: sudo apt install -y curl git openssl podman")
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
        print("   For production, consider running with: sudo python3 visp-deploy.py")
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
        print("   → For production: run with 'sudo python3 visp-deploy.py install'")
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
    swamid_cert_path = "certs/md-signer2.crt"
    if not os.path.exists(swamid_cert_path):
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
    else:
        print("✓ SWAMID certificate already exists, skipping download")

    # Generate self-signed certs for local development
    os.makedirs("certs/visp.local", exist_ok=True)
    visp_cert_path = "certs/visp.local/cert.crt"
    visp_key_path = "certs/visp.local/cert.key"

    if not os.path.exists(visp_cert_path) or not os.path.exists(visp_key_path):
        run_command(
            "openssl req -x509 -newkey rsa:4096 -keyout certs/visp.local/cert.key "
            "-out certs/visp.local/cert.crt -nodes -days 3650 "
            '-subj "/C=SE/ST=visp/L=visp/O=visp/OU=visp/CN=visp.local"',
            "Generating TLS cert",
        )
    else:
        print("✓ VISP TLS certificate already exists, skipping generation")

    os.makedirs("certs/ssp-idp-cert", exist_ok=True)
    idp_cert_path = "certs/ssp-idp-cert/cert.pem"
    idp_key_path = "certs/ssp-idp-cert/key.pem"

    if not os.path.exists(idp_cert_path) or not os.path.exists(idp_key_path):
        run_command(
            "openssl req -x509 -newkey rsa:4096 -keyout certs/ssp-idp-cert/key.pem "
            "-out certs/ssp-idp-cert/cert.pem -nodes -days 3650 "
            '-subj "/C=SE/ST=visp/L=visp/O=visp/OU=visp/CN=visp.local"',
            "Generating IdP cert",
        )
    else:
        print("✓ SSP IdP certificate already exists, skipping generation")


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


def clone_repositories(basedir, mode="dev"):
    """
    Clone all required repositories from versions.json.

    Args:
        basedir: Base directory for cloning
        mode: Installation mode - 'dev' uses latest, 'prod' uses locked versions
    """
    config = ComponentConfig()

    print("\n📦 Cloning repositories...")
    print(f"Working directory: {os.getcwd()}")
    print(f"Mode: {mode} ({'unlocked/latest' if mode == 'dev' else 'locked versions'})")

    failed_repos = []
    external_dir = os.path.join(basedir, "external")
    os.makedirs(external_dir, exist_ok=True)

    for name, comp_data in config.get_components():
        repo_path = os.path.join(external_dir, name)
        url = get_repo_url(name, comp_data)
        repo = GitRepository(repo_path, url)

        # Determine what version to check out
        version = comp_data.get("version", "latest")
        locked_version = comp_data.get("locked_version")

        # In prod mode, prefer locked version
        if mode == "prod" and version == "latest" and locked_version:
            target_version = locked_version
            print(
                f"⊙ {name}: Production mode - using locked version {locked_version[:8]}"
            )
        else:
            target_version = version

        # Check if repo exists and is valid
        if repo.exists():
            if not repo.is_git_repo():
                print(
                    f"⚠️  {name} exists but is not a git repository - will remove and re-clone"
                )
                try:
                    shutil.rmtree(repo_path)
                except OSError as e:
                    print(f"✗ Failed to remove invalid directory {repo_path}: {e}")
                    failed_repos.append(name)
                    continue
            else:
                # Existing repo - check if it has content
                try:
                    # Quick check: see if there are any files outside .git
                    has_content = any(
                        os.path.isfile(os.path.join(repo_path, f))
                        for f in os.listdir(repo_path)
                        if f != ".git"
                    )

                    if not has_content:
                        # Check subdirectories
                        has_content = any(
                            os.path.isdir(os.path.join(repo_path, d))
                            for d in os.listdir(repo_path)
                            if d != ".git"
                        )

                    if not has_content:
                        print(
                            f"⚠️  {name} appears to be empty - will remove and re-clone"
                        )
                        shutil.rmtree(repo_path)
                    else:
                        # Valid repo - fetch and update
                        print(f"⊙ Repository {name} already exists, updating...")
                        try:
                            repo.fetch(quiet=True)

                            # Try to pull with fast-forward only
                            try:
                                repo.pull()
                                print(f"  ✓ Updated {name} from remote")
                            except subprocess.CalledProcessError:
                                print(
                                    f"  ⚠️  Could not pull updates for {name} (may have local changes or diverged)"
                                )

                        except subprocess.CalledProcessError as e:
                            print(f"  ⚠️  Failed to fetch updates for {name}: {e}")

                        continue  # Skip to next repo

                except OSError as e:
                    print(f"✗ Failed to verify {name}: {e}")
                    failed_repos.append(name)
                    continue

        # Clone the repository
        print(f"Cloning {name} from {url}...")
        try:
            repo.clone()

            # Verify the clone succeeded
            if not repo.exists() or not repo.is_git_repo():
                print(f"✗ Failed to clone {name} - repository not created properly")
                failed_repos.append(name)
                continue

            # Checkout specific version if not "latest"
            if target_version != "latest":
                try:
                    repo.checkout(target_version)
                    print(f"  ✓ Checked out {target_version[:8]} for {name}")
                except subprocess.CalledProcessError as e:
                    print(
                        f"  ⚠️  Failed to checkout {target_version[:8]} for {name}: {e}"
                    )
                    print("     Repository will remain on default branch")
            else:
                print("  Development mode: using latest from default branch")

            print(f"✓ Successfully cloned {name}")

        except subprocess.CalledProcessError as e:
            print(f"✗ Failed to clone {name}: {e}")
            failed_repos.append(name)

    # Report results
    if failed_repos:
        print(f"\n⚠️  WARNING: Failed to clone {len(failed_repos)} repository(ies):")
        for repo_name in failed_repos:
            print(f"  - {repo_name}")
        print("\nInstallation incomplete. Please resolve the issues above.")
        print("You may need to:")
        print("  1. Check your internet connection")
        print("  2. Verify git is installed")
        print("  3. Check repository URLs in versions.json")
        print("  4. Manually clone missing repositories")
        sys.exit(1)

    total_repos = len(list(config.get_components()))
    print(f"✓ All {total_repos} repositories ready")


def fix_repository_permissions():
    """Ensure repository directories have correct permissions for container access"""
    print("\n🔒 Setting repository permissions for container access...")

    config = ComponentConfig()

    for name, _ in config.get_components():
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
    config = ComponentConfig()

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
    for name, comp_data in config.get_components():
        comp_path = os.path.join(basedir, name)

        # Skip if component doesn't exist
        if not os.path.exists(comp_path):
            continue

        commands = []

        # Build npm install command based on config
        if comp_data.get("npm_install", False):
            # Special case for webclient which needs legacy-peer-deps
            if name == "webclient":
                commands.append("npm install --legacy-peer-deps")
            else:
                commands.append("npm install")

        # Build npm build command based on config
        if comp_data.get("npm_build", False):
            # For webclient, use the WEBCLIENT_BUILD setting from .env
            if name == "webclient":
                commands.append(f"npm run {webclient_build_cmd}")
            else:
                commands.append("npm run build")

        # Execute commands if any
        runtime = get_container_runtime()
        for cmd in commands:
            # EMU-webApp uses webpack 4 which requires legacy OpenSSL algorithms
            # Use --openssl-legacy-provider flag for Node 17+ compatibility
            if name == "EMU-webApp" and "npm run build" in cmd:
                run_command(
                    f"{runtime} run --rm -v {comp_path}:/app -w /app node:20 sh -c "
                    f"'export NODE_OPTIONS=--openssl-legacy-provider && {cmd}'",
                    f"Building {name}: {cmd} (with legacy OpenSSL)",
                )
            else:
                # Use temporary container for builds instead of host Node.js
                # This ensures clean host and consistent versioning
                run_command(
                    f"{runtime} run --rm -v {comp_path}:/app -w /app node:20 {cmd}",
                    f"Building {name}: {cmd}",
                )

    print("\nNote: Dependencies are installed based on versions.json configuration.")
    print(
        "In development mode (docker-compose.dev.yml), source code is mounted for hot-reload."
    )
    print(
        "In production mode (docker-compose.prod.yml), run 'docker compose build' to bake code into images."
    )


def install_npm_dependencies(basedir):
    """
    Install npm dependencies in required external directories.

    This runs npm install in specific directories that need their dependencies
    installed for development or build purposes.
    """
    npm_directories = [
        "external/EMU-webApp",
        "external/container-agent",
        "external/emu-webapp-server",
        "external/session-manager",
        "external/webclient",
        "external/wsrng-server",
    ]

    print("\nInstalling npm dependencies in external directories...")

    for dir_path in npm_directories:
        full_path = os.path.join(basedir, dir_path)
        package_json = os.path.join(full_path, "package.json")

        # Skip if directory or package.json doesn't exist
        if not os.path.exists(full_path):
            print(f"⚠️  Skipping {dir_path} (directory not found)")
            continue

        if not os.path.exists(package_json):
            print(f"⚠️  Skipping {dir_path} (no package.json found)")
            continue

        print(f"\n📦 Installing dependencies in {dir_path}...")

        # Special case for webclient which needs legacy-peer-deps
        if "webclient" in dir_path:
            cmd = "npm install --legacy-peer-deps"
        else:
            cmd = "npm install"

        try:
            # Use temporary container for npm install to avoid host Node.js version issues
            runtime = get_container_runtime()
            run_command(
                f"{runtime} run --rm -v {full_path}:/app -w /app node:20 {cmd}",
                f"Installing {dir_path}",
            )
            print(f"✅ Successfully installed dependencies in {dir_path}")
        except subprocess.CalledProcessError as e:
            print(f"❌ Failed to install dependencies in {dir_path}: {e}")
            # Continue with other directories even if one fails


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

    # Clone all repos (respecting mode for lock behavior)
    clone_repositories(BASEDIR, mode)

    # Fix permissions for container access
    fix_repository_permissions()

    # Install npm dependencies in external directories
    install_npm_dependencies(BASEDIR)

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
    locked_version="N/A",
    force=False,
):
    """
    Update a repository with lock awareness.

    Args:
        basedir: Base directory containing external repos
        name: Repository name
        npm_install: Whether to run npm install (unused here, for compatibility)
        npm_build: Whether to run npm build (unused here, for compatibility)
        repo_url: Repository URL
        version: Current version setting from versions.json
        locked_version: Locked version for rollback reference
        force: Force update (not used - locks must be explicitly unlocked)
    """
    print(f"\n{'='*60}")
    print(f"Updating {name}...")
    print(f"{'='*60}")

    repo_path = os.path.join(basedir, "external", name)
    if not repo_url:
        repo_url = f"https://github.com/humlab-speech/{name}.git"

    repo = GitRepository(repo_path, repo_url)

    # Clone repository if it doesn't exist
    if not repo.exists():
        print(f"Repository {name} not found, cloning from {repo_url}...")
        try:
            repo.clone()
        except subprocess.CalledProcessError as e:
            print(f"✗ Git clone of {name} failed: {e}")
            return {"name": name, "status": "❌ CLONE FAILED", "details": str(e)}

    try:
        # Always fetch first to get latest remote info
        print("Fetching latest from remote...")
        repo.fetch(quiet=True)

        # Get current commit info using GitRepository
        current_info = repo.get_commit_info("HEAD")
        if not current_info:
            return {
                "name": name,
                "status": "❌ ERROR",
                "details": "Cannot get commit info",
            }

        # Format date for display (convert from string format)
        current_date_str = current_info["date"][:10] if current_info["date"] else "N/A"

        # Check if locked
        is_locked = version != "latest"

        if is_locked:
            print(f"🔒 {name} is LOCKED to version {version[:8]}")
            print(f"   Current: {current_info['sha_short']} from {current_date_str}")
            print(f"   Locked:  {version[:8]}")
            print()
            print("⚠️  Cannot update locked component")
            print(f"   To update {name}:")
            print(f"   1. Run: python3 visp-deploy.py unlock {name}")
            print("   2. Run: python3 visp-deploy.py update")
            print("   3. Test the changes")
            print(
                f"   4. Run: python3 visp-deploy.py lock {name}  (to lock new version)"
            )
            print()
            return {
                "name": name,
                "status": "🔒 LOCKED",
                "details": f"Locked at {version[:8]}",
            }

        # Component is unlocked - check for updates
        print(f"🔓 {name} is UNLOCKED (tracking latest)")

        # Check for uncommitted changes using GitRepository
        if repo.is_dirty():
            print(f"⚠️  WARNING: {name} has uncommitted changes!")
            print("   Options:")
            print("   1. Commit or stash your changes first")
            print("   2. Skip this repository")
            return {
                "name": name,
                "status": "⚠️  UNCOMMITTED",
                "details": "Has local changes",
            }

        # Determine main branch (try main, fall back to master)
        main_branch = "main"
        if not repo.has_remote_branch("main"):
            main_branch = "master"

        # Get remote HEAD info
        remote_info = repo.get_commit_info(f"origin/{main_branch}")
        if not remote_info:
            return {
                "name": name,
                "status": "❌ ERROR",
                "details": "Cannot get remote info",
            }

        remote_date_str = remote_info["date"][:10] if remote_info["date"] else "N/A"

        # Check if already up to date
        if current_info["sha"] == remote_info["sha"]:
            print(f"✅ Already up to date at {current_info['sha_short']}")
            print(f"   Date: {current_date_str}")
            print(f"   Commit: {current_info['subject'][:60]}")
            return {
                "name": name,
                "status": "✅ UP TO DATE",
                "details": current_info["sha_short"],
            }

        # Calculate commits ahead/behind using GitRepository
        commits_behind = repo.count_commits_between(
            current_info["sha"], remote_info["sha"]
        )
        commits_ahead = repo.count_commits_between(
            remote_info["sha"], current_info["sha"]
        )

        # Show update info
        print("\n📊 Update available:")
        print(f"   Current:  {current_info['sha_short']} from {current_date_str}")
        print(f"   Remote:   {remote_info['sha_short']} from {remote_date_str}")
        if commits_behind:
            print(f"   Behind:   {commits_behind} commit(s)")
        if commits_ahead:
            print(f"   Ahead:    {commits_ahead} commit(s) (will be rebased)")
        print()

        # Perform update using GitRepository
        if commits_ahead > 0:
            print("⚠️  Local commits detected - attempting rebase...")
            try:
                repo.run_git(["rebase", f"origin/{main_branch}"])
                print("✓ Successfully rebased local commits")
            except subprocess.CalledProcessError:
                repo.run_git(["rebase", "--abort"], check=False)
                print("✗ Rebase failed - manual intervention required")
                return {
                    "name": name,
                    "status": "❌ REBASE FAILED",
                    "details": "Manual merge needed",
                }
        else:
            print(f"Pulling latest from origin/{main_branch}...")
            repo.run_git(["merge", "--ff-only", f"origin/{main_branch}"])

        # Get new commit info
        new_info = repo.get_commit_info("HEAD")
        new_date_str = new_info["date"][:10] if new_info["date"] else "N/A"
        print(f"\n✅ Updated to {new_info['sha_short']} from {new_date_str}")
        print(f"   {new_info['subject'][:60]}")

        return {
            "name": name,
            "status": "✅ UPDATED",
            "details": f"{current_info['sha_short']} → {new_info['sha_short']} ({commits_behind} commits)",
        }

    except subprocess.CalledProcessError as e:
        print(f"✗ Git update of {name} failed: {e}")
        return {"name": name, "status": "❌ UPDATE FAILED", "details": str(e)}


def update_repositories(basedir, force=False):
    """Update all external repositories with lock awareness"""
    config = ComponentConfig()
    results = []

    for name, comp_data in config.get_components():
        repo_url = get_repo_url(name, comp_data)
        version = comp_data.get("version", "latest")
        locked_version = comp_data.get("locked_version", "N/A")

        result = update_repo(
            basedir,
            name,
            repo_url=repo_url,
            version=version,
            locked_version=locked_version,
            force=force,
        )
        if result:
            results.append(result)

    return results


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


class SessionImageBuilder:
    """Handles building session images (operations, rstudio, jupyter)"""

    def __init__(self, basedir):
        self.basedir = basedir
        self.docker_dir = os.path.join(basedir, "docker", "session-manager")
        self.container_agent_src = os.path.join(basedir, "external", "container-agent")
        self.container_agent_dest = os.path.join(self.docker_dir, "container-agent")
        self.images = [
            {
                "name": "visp-operations-session",
                "dockerfile": "operations-session/Dockerfile",
                "description": "Operations session image",
            },
            {
                "name": "visp-rstudio-session",
                "dockerfile": "rstudio-session/Dockerfile",
                "description": "RStudio session image",
            },
            {
                "name": "visp-jupyter-session",
                "dockerfile": "jupyter-session/Dockerfile",
                "description": "Jupyter session image",
            },
        ]

    def prepare_build_context(self):
        """Copy container-agent source into build context"""
        print("\n📦 Preparing build context...")
        print(f"   Copying container-agent from: {self.container_agent_src}")
        print(f"   To: {self.container_agent_dest}")

        if not os.path.exists(self.container_agent_src):
            raise FileNotFoundError(
                f"container-agent source not found at {self.container_agent_src}\n"
                "Run 'python3 visp-deploy.py install' or 'python3 visp-deploy.py update' first."
            )

        # Remove existing copy if present
        if os.path.exists(self.container_agent_dest):
            shutil.rmtree(self.container_agent_dest)

        # Copy container-agent source into build context
        shutil.copytree(self.container_agent_src, self.container_agent_dest)
        print("   ✅ Build context ready")

    def cleanup_build_context(self):
        """Remove temporary container-agent copy from build context"""
        print("\n🧹 Cleaning up build context...")
        if os.path.exists(self.container_agent_dest):
            shutil.rmtree(self.container_agent_dest)
            print("   ✅ Cleanup complete")

    def rebuild_all(self, no_cache=True, images_to_build=None):
        """
        Rebuild session images

        Args:
            no_cache: Whether to use --no-cache flag
            images_to_build: List of image names to build, or None for all
                           e.g. ["visp-operations-session", "visp-jupyter-session"]
        """
        print("\n" + "=" * 70)
        print("REBUILDING SESSION IMAGES")
        print("=" * 70)

        # Filter images if specific ones requested
        if images_to_build:
            images = [img for img in self.images if img["name"] in images_to_build]
            if not images:
                print(f"⚠️  No matching images found for: {images_to_build}")
                return []
        else:
            images = self.images

        try:
            # Prepare build context (copy container-agent)
            self.prepare_build_context()

            # Build in order (operations must be first since others depend on it)
            results = []
            for image in images:
                result = self.build_image(image, no_cache=no_cache)
                results.append(result)
                if not result["success"]:
                    print(f"⚠️  Build failed for {image['name']}, but continuing...")

            return results

        finally:
            # Always cleanup, even if build fails
            self.cleanup_build_context()

    def rebuild_operations(self, no_cache=True):
        """Rebuild only operations-session image"""
        return self.rebuild_all(
            no_cache=no_cache, images_to_build=["visp-operations-session"]
        )

    def rebuild_jupyter(self, no_cache=True):
        """Rebuild only jupyter-session image (requires operations-session to exist)"""
        return self.rebuild_all(
            no_cache=no_cache, images_to_build=["visp-jupyter-session"]
        )

    def rebuild_rstudio(self, no_cache=True):
        """Rebuild only rstudio-session image (requires operations-session to exist)"""
        return self.rebuild_all(
            no_cache=no_cache, images_to_build=["visp-rstudio-session"]
        )

    def build_image(self, image, no_cache=True):
        """Build a single session image"""
        print(f"\n{'='*70}")
        print(f"Building {image['description']}")
        print(f"Image: {image['name']}")
        print(f"{'='*70}")

        try:
            with working_directory(self.docker_dir):
                runtime = get_container_runtime()
                cmd = [runtime, "build"]
                if no_cache:
                    cmd.append("--no-cache")
                cmd.extend(["-t", image["name"], "-f", image["dockerfile"], "."])

                print(f"Running: {' '.join(cmd)}")
                result = subprocess.run(cmd, check=False, capture_output=False)

                if result.returncode == 0:
                    print(f"✅ Successfully built {image['name']}")
                    return {"success": True, "image": image["name"]}
                else:
                    print(f"❌ Failed to build {image['name']}")
                    return {
                        "success": False,
                        "image": image["name"],
                        "error": "Build failed",
                    }

        except (OSError, subprocess.SubprocessError) as e:
            print(f"❌ Error building {image['name']}: {e}")
            return {"success": False, "image": image["name"], "error": str(e)}


def rebuild_images(basedir=None):
    """Rebuild session images using SessionImageBuilder"""
    if basedir is None:
        basedir = os.getcwd()

    builder = SessionImageBuilder(basedir)
    results = builder.rebuild_all(no_cache=True)

    # Check if any builds failed
    failed = [r for r in results if not r["success"]]
    if failed:
        print(
            f"\n⚠️  {len(failed)} image(s) failed to build, but continuing with update."
        )
    else:
        print("\n✅ All images rebuilt successfully.")


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


def check_and_rebuild_images(basedir=None):
    """Check container image ages and rebuild if needed, return status result"""
    if basedir is None:
        basedir = os.getcwd()

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
            rebuild_images(basedir)
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

    # Count results - handle both key formats (name/status/details and Component/Status/Details)
    total = len(status_results)
    passed = sum(
        1
        for r in status_results
        if (
            "Status" in r
            and (
                "PASS" in r["Status"]
                or "REBUILT" in r["Status"]
                or "UP TO DATE" in r["Status"]
            )
        )
        or (
            "status" in r
            and (
                "PASS" in r["status"]
                or "REBUILT" in r["status"]
                or "UP TO DATE" in r["status"]
            )
        )
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

    # Install npm dependencies after repository updates
    install_npm_dependencies(BASEDIR)

    # Check environment
    print("🔍 Checking environment...")
    env_result = check_environment()
    status_results.append(env_result)

    # Check and rebuild images
    print("🐳 Checking Docker images...")
    image_result = check_and_rebuild_images(BASEDIR)
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


def check_session_images_status():
    """Check if session images exist and report their age"""
    images = ["visp-operations-session", "visp-rstudio-session", "visp-jupyter-session"]

    results = []

    for image_name in images:
        try:
            # Check if image exists
            result = subprocess.run(
                ["docker", "image", "inspect", image_name, "--format", "{{.Created}}"],
                capture_output=True,
                text=True,
                check=False,
            )

            if result.returncode == 0:
                # Parse creation date
                created_str = result.stdout.strip()
                # Docker returns ISO format like: 2024-12-11T10:30:45.123456789Z
                from datetime import datetime, timezone

                try:
                    created = datetime.fromisoformat(created_str.replace("Z", "+00:00"))
                    now = datetime.now(timezone.utc)
                    age = now - created

                    # Format age nicely
                    if age.days > 0:
                        age_str = f"{age.days} day(s) old"
                        status = "⚠️ OLD" if age.days > 30 else "✅ OK"
                    elif age.seconds > 3600:
                        hours = age.seconds // 3600
                        age_str = f"{hours} hour(s) old"
                        status = "✅ OK"
                    else:
                        minutes = age.seconds // 60
                        age_str = f"{minutes} minute(s) old"
                        status = "✅ NEW"

                    results.append(
                        {
                            "Image": image_name,
                            "Status": status,
                            "Age": age_str,
                            "Built": created.strftime("%Y-%m-%d %H:%M"),
                        }
                    )
                except (ValueError, AttributeError):
                    results.append(
                        {
                            "Image": image_name,
                            "Status": "✅ EXISTS",
                            "Age": "Unknown",
                            "Built": "Unknown",
                        }
                    )
            else:
                results.append(
                    {
                        "Image": image_name,
                        "Status": "❌ MISSING",
                        "Age": "N/A",
                        "Built": "Not built",
                    }
                )

        except (subprocess.SubprocessError, OSError) as e:
            results.append(
                {
                    "Image": image_name,
                    "Status": "❌ ERROR",
                    "Age": f"Error: {e}",
                    "Built": "Unknown",
                }
            )

    return results


def check_repositories_status(fetch=True):
    """Check status of all repositories and report uncommitted changes"""
    print("🔍 Checking repository status...")

    if fetch:
        print("📡 Fetching latest remote information...")

    # Check deployment mode
    deployment_mode = check_deployment_mode()

    # Check webclient build configuration
    webclient_build_config = check_webclient_build_config()

    # 1. Check Deployment Repository (the one we're in)
    print("\n📦 Checking main deployment repository...")
    deployment_repo = GitRepository(os.getcwd())

    try:
        if fetch:
            deployment_repo.fetch(quiet=True)

        current_branch = deployment_repo.get_current_branch() or "main"
        has_changes = deployment_repo.is_dirty()

        # Calculate ahead/behind using the class method
        behind_count = deployment_repo.count_commits_between(
            f"origin/{current_branch}", "HEAD"
        )
        ahead_count = deployment_repo.count_commits_between(
            "HEAD", f"origin/{current_branch}"
        )

        deployment_repo_status = {
            "Repository": "visible-speech-deployment (THIS REPO)",
            "Branch": current_branch,
            "Has Changes": "⚠️  YES" if has_changes else "✅ NO",
            "Behind Remote": f"⬇️ {behind_count}" if behind_count > 0 else "✅ 0",
            "Ahead Remote": f"🚀 {ahead_count}" if ahead_count > 0 else "✅ 0",
        }

        if behind_count > 0:
            print(
                f"⚠️  WARNING: Deployment repo is {behind_count} commit(s) "
                "behind remote!"
            )
            print(
                f"   Run 'git pull origin {current_branch}' to update "
                "the deployment scripts"
            )

    except Exception as e:
        deployment_repo_status = {
            "Repository": "visible-speech-deployment (THIS REPO)",
            "Branch": "ERROR",
            "Has Changes": "❌ ERROR",
            "Behind Remote": f"Error: {str(e)}",
            "Ahead Remote": "N/A",
        }

    # 2. Check External Component Repositories
    config = ComponentConfig()
    status_results = []
    repos_with_changes = []
    repos_ahead = []
    repos_behind = []

    for repo_name, comp_data in config.get_components():
        repo_path = os.path.join(os.getcwd(), "external", repo_name)
        repo = GitRepository(repo_path)

        # Get version info from config
        version = comp_data.get("version", "latest")
        locked_version = comp_data.get("locked_version", "N/A")
        is_locked = config.is_locked(repo_name)

        # Format lock status
        lock_status = "🔒 LOCKED" if is_locked else "🔓 UNLOCKED"
        lock_details = f"at {version[:8]}" if is_locked else "tracking latest"

        # Check if repo exists
        if not repo.exists():
            status_results.append(
                {
                    "Repository": repo_name,
                    "Lock Status": f"{lock_status} ({lock_details})",
                    "Current Commit": "N/A",
                    "Locked Version": (
                        locked_version[:8] if locked_version != "N/A" else "N/A"
                    ),
                    "Status": "❌ MISSING",
                    "Sync Status": "Repository not cloned",
                }
            )
            continue

        if not repo.is_git_repo():
            status_results.append(
                {
                    "Repository": repo_name,
                    "Lock Status": f"{lock_status} ({lock_details})",
                    "Current Commit": "N/A",
                    "Locked Version": (
                        locked_version[:8] if locked_version != "N/A" else "N/A"
                    ),
                    "Status": "❌ NOT GIT",
                    "Sync Status": "Not a git repository",
                }
            )
            continue

        try:
            # Use GitRepository methods - no os.chdir needed!
            if fetch:
                try:
                    repo.fetch(quiet=True)
                except subprocess.CalledProcessError:
                    pass  # Fetch failed, continue with cached data

            # Get current commit using the class
            current_commit = (repo.get_current_commit() or "N/A")[:8]

            # Check for uncommitted changes using the class
            has_changes = repo.is_dirty()

            # Check sync status with remote
            sync_status = "✅ SYNCED"
            sync_details = []

            try:
                current_branch = repo.get_current_branch() or "main"

                # Check if remote exists
                remote_url = repo.get_remote_url()

                if remote_url:
                    # Check if remote branch exists
                    if repo.has_remote_branch(current_branch):
                        # Calculate ahead/behind using class methods
                        behind_count = repo.count_commits_between(
                            f"origin/{current_branch}", "HEAD"
                        )
                        ahead_count = repo.count_commits_between(
                            "HEAD", f"origin/{current_branch}"
                        )

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
                    sync_details.append("No remote configured")
                    sync_status = "🏠 LOCAL ONLY"

            except Exception:
                sync_details.append("Error checking remote")
                sync_status = "❌ ERROR"

            # Determine overall status
            if has_changes:
                repos_with_changes.append(repo_name)
                overall_status = "⚠️  HAS CHANGES"
            else:
                overall_status = "✅ CLEAN"

            # Combine sync details
            sync_desc = ", ".join(sync_details) if sync_details else "Up to date"

            status_results.append(
                {
                    "Repository": repo_name,
                    "Lock Status": f"{lock_status} ({lock_details})",
                    "Current Commit": current_commit,
                    "Locked Version": (
                        locked_version[:8] if locked_version != "N/A" else "N/A"
                    ),
                    "Status": overall_status,
                    "Sync Status": f"{sync_status} - {sync_desc}",
                }
            )

        except Exception as e:
            status_results.append(
                {
                    "Repository": repo_name,
                    "Lock Status": f"{lock_status} ({lock_details})",
                    "Current Commit": "ERROR",
                    "Locked Version": (
                        locked_version[:8] if locked_version != "N/A" else "N/A"
                    ),
                    "Status": "❌ ERROR",
                    "Sync Status": f"Error: {str(e)}",
                }
            )

    # Print results
    print("\n" + "=" * 100)
    print("REPOSITORY STATUS CHECK")
    print("=" * 100)

    # Show deployment repo status first
    if deployment_repo_status:
        print("\n🔧 DEPLOYMENT REPOSITORY (visible-speech-deployment)")
        print("-" * 100)
        print(tabulate([deployment_repo_status], headers="keys", tablefmt="grid"))
        print()

    # Show deployment mode first
    if deployment_mode:
        print("🚀 DEPLOYMENT MODE")
        print("-" * 100)
        print(tabulate([deployment_mode], headers="keys", tablefmt="grid"))
        print()

    # Show webclient build configuration
    if webclient_build_config:
        print("📦 WEBCLIENT BUILD CONFIGURATION")
        print("-" * 100)
        print(tabulate([webclient_build_config], headers="keys", tablefmt="grid"))
        print()

    print("📚 EXTERNAL COMPONENT REPOSITORIES")
    print("-" * 100)
    print(tabulate(status_results, headers="keys", tablefmt="grid"))
    print("=" * 100)

    # Check session images
    print("\n🐳 SESSION DOCKER IMAGES")
    print("-" * 100)
    session_images = check_session_images_status()
    print(tabulate(session_images, headers="keys", tablefmt="grid"))
    print("=" * 100)

    # Summary
    summary_lines = []

    # Check if any session images are missing or old
    missing_images = [
        img["Image"] for img in session_images if "MISSING" in img["Status"]
    ]
    old_images = [img["Image"] for img in session_images if "OLD" in img["Status"]]

    if missing_images:
        summary_lines.append(f"❌ Missing session images: {', '.join(missing_images)}")
        summary_lines.append(
            "   Run 'python3 visp-deploy.py build' to build missing images"
        )

    if old_images:
        summary_lines.append(
            f"⚠️  Old session images (>30 days): {', '.join(old_images)}"
        )
        summary_lines.append(
            "   Consider rebuilding with 'python3 visp-deploy.py build'"
        )

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


def lock_components(components, lock_all=False):
    """Lock components to their current commit versions"""
    config = ComponentConfig()

    if lock_all:
        components = [name for name, _ in config.get_components()]
    elif not components:
        print("❌ Error: No components specified")
        print("Usage: visp-deploy.py lock <component> [<component> ...]")
        print("   or: visp-deploy.py lock --all")
        return False

    print(f"🔒 Locking {len(components)} component(s)...\n")

    locked_count = 0
    for component in components:
        comp_data = config.get_component(component)
        if not comp_data:
            print(f"⚠️  {component}: Not found in versions.json, skipping")
            continue

        repo_path = os.path.join(os.getcwd(), "external", component)
        repo = GitRepository(repo_path)

        if not repo.exists():
            print(f"⚠️  {component}: Repository not cloned at {repo_path}, skipping")
            continue

        try:
            # Get current commit using GitRepository
            commit_info = repo.get_commit_info("HEAD")
            if not commit_info:
                print(f"✗ {component}: Failed to get current commit")
                continue

            # Extract date for display
            commit_date = commit_info["date"][:10] if commit_info["date"] else "N/A"

            # Lock using ComponentConfig method
            config.lock(component, commit_info["sha"])

            print(f"✓ {component}: Locked to {commit_info['sha_short']}")
            print(f"  Date: {commit_date}")
            print(f"  Commit: {commit_info['subject'][:60]}")
            print()

            locked_count += 1

        except Exception as e:
            print(f"✗ {component}: Failed to lock - {e}")
            continue

    # Save updated config using ComponentConfig
    if locked_count > 0:
        try:
            config.save()
            print(f"\n✅ Successfully locked {locked_count} component(s)")
            print("   Changes saved to versions.json")
            print(
                "   Don't forget to commit versions.json to track these locked versions"
            )
            return True
        except Exception as e:
            print(f"\n❌ Failed to save versions.json: {e}")
            return False
    else:
        print("\n⚠️  No components were locked")
        return False


def unlock_components(components, unlock_all=False):
    """Unlock components to track latest"""
    config = ComponentConfig()

    if unlock_all:
        components = [name for name, _ in config.get_components()]
    elif not components:
        print("❌ Error: No components specified")
        print("Usage: visp-deploy.py unlock <component> [<component> ...]")
        print("   or: visp-deploy.py unlock --all")
        return False

    print(f"🔓 Unlocking {len(components)} component(s)...\n")

    unlocked_count = 0
    for component in components:
        if not config.get_component(component):
            print(f"⚠️  {component}: Not found in versions.json, skipping")
            continue

        if not config.is_locked(component):
            print(f"ℹ️  {component}: Already unlocked (tracking latest)")
            continue

        locked_version = config.get_locked_version(component)

        # Unlock using ComponentConfig method
        config.unlock(component)

        print(f"✓ {component}: Unlocked (now tracking latest)")
        if locked_version and locked_version != "N/A":
            print(f"  Locked version preserved for rollback: {locked_version[:8]}")
        print()

        unlocked_count += 1

    # Save updated config using ComponentConfig
    if unlocked_count > 0:
        try:
            config.save()
            print(f"\n✅ Successfully unlocked {unlocked_count} component(s)")
            print("   Changes saved to versions.json")
            print("   Run 'visp-deploy.py update' to pull latest changes")
            return True
        except Exception as e:
            print(f"\n❌ Failed to save versions.json: {e}")
            return False
    else:
        print("\n⚠️  No components were unlocked")
        return False


def rollback_components(components, rollback_all=False):
    """Rollback components to their locked versions"""
    config = ComponentConfig()

    if rollback_all:
        components = [name for name, _ in config.get_components()]
    elif not components:
        print("❌ Error: No components specified")
        print("Usage: visp-deploy.py rollback <component> [<component> ...]")
        print("   or: visp-deploy.py rollback --all")
        return False

    print(f"⏪ Rolling back {len(components)} component(s)...\n")

    rolled_back_count = 0
    for component in components:
        if not config.get_component(component):
            print(f"⚠️  {component}: Not found in versions.json, skipping")
            continue

        locked_version = config.get_locked_version(component)
        if not locked_version or locked_version == "N/A":
            print(f"⚠️  {component}: No locked version available, skipping")
            continue

        repo_path = os.path.join(os.getcwd(), "external", component)
        repo = GitRepository(repo_path)

        if not repo.exists():
            print(f"⚠️  {component}: Repository not cloned at {repo_path}, skipping")
            continue

        try:
            # Get current commit info using GitRepository
            current_info = repo.get_commit_info("HEAD")
            if not current_info:
                print(f"✗ {component}: Failed to get current commit")
                continue

            # Get locked version info
            locked_info = repo.get_commit_info(locked_version)
            if not locked_info:
                print(f"✗ {component}: Locked version {locked_version[:8]} not found")
                continue

            # Check if already at locked version
            if current_info["sha"] == locked_info["sha"]:
                print(f"ℹ️  {component}: Already at locked version")
                print(f"  {locked_info['sha_short']} from {locked_info['date'][:10]}")
                print()
                continue

            # Check for uncommitted changes using GitRepository
            if repo.is_dirty():
                print(f"⚠️  {component}: Skipping due to uncommitted changes")
                print("   Commit or stash changes before rollback")
                print()
                continue

            # Checkout locked version using GitRepository - NO os.chdir!
            repo.checkout(locked_version)

            # Update config using ComponentConfig method
            config.rollback(component)

            # Calculate commits difference
            commits_back = repo.count_commits_between(
                locked_info["sha"], current_info["sha"]
            )

            # Extract dates for display
            current_date = current_info["date"][:10] if current_info["date"] else "N/A"
            locked_date = locked_info["date"][:10] if locked_info["date"] else "N/A"

            print(f"✓ {component}: Rolled back to {locked_info['sha_short']}")
            print(f"  From: {current_info['sha_short']} ({current_date})")
            print(f"  To:   {locked_info['sha_short']} ({locked_date})")
            if commits_back:
                print(f"  Moved back {commits_back} commit(s)")
            print()

            rolled_back_count += 1

        except subprocess.CalledProcessError as e:
            print(f"✗ {component}: Failed to rollback - {e}")
            continue
        except Exception as e:
            print(f"✗ {component}: Error during rollback - {e}")
            continue

    # Save updated config using ComponentConfig
    if rolled_back_count > 0:
        try:
            config.save()
            print(f"\n✅ Successfully rolled back {rolled_back_count} component(s)")
            print("   Changes saved to versions.json")
            print("   You may need to rebuild Docker images with updated code")
            return True
        except Exception as e:
            print(f"\n❌ Failed to save versions.json: {e}")
            return False
    else:
        print("\n⚠️  No components were rolled back")
        return False


def main():
    parser = argparse.ArgumentParser(description="VISP Deployment Manager")

    # Global options
    parser.add_argument(
        "--runtime",
        choices=["docker", "podman"],
        default=None,
        help="Container runtime to use (default: auto-detect, prefers podman)",
    )

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

    # Lock command
    lock_parser = subparsers.add_parser(
        "lock", help="Lock components to their current versions"
    )
    lock_parser.add_argument(
        "components",
        nargs="*",
        help="Components to lock (specify names or use --all)",
    )
    lock_parser.add_argument(
        "--all",
        action="store_true",
        help="Lock all components",
    )

    # Unlock command
    unlock_parser = subparsers.add_parser(
        "unlock", help="Unlock components to track latest"
    )
    unlock_parser.add_argument(
        "components",
        nargs="*",
        help="Components to unlock (specify names or use --all)",
    )
    unlock_parser.add_argument(
        "--all",
        action="store_true",
        help="Unlock all components",
    )

    # Rollback command
    rollback_parser = subparsers.add_parser(
        "rollback", help="Rollback components to their locked versions"
    )
    rollback_parser.add_argument(
        "components",
        nargs="*",
        help="Components to rollback (specify names or use --all)",
    )
    rollback_parser.add_argument(
        "--all",
        action="store_true",
        help="Rollback all components",
    )

    # Build command for session images
    build_parser = subparsers.add_parser(
        "build", help="Build session images (operations, rstudio, jupyter)"
    )
    build_parser.add_argument(
        "images",
        nargs="*",
        choices=["operations", "rstudio", "jupyter", "all"],
        default=["all"],
        help="Which images to build (default: all)",
    )
    build_parser.add_argument(
        "--cache",
        action="store_true",
        help="Use Docker cache (default is --no-cache)",
    )

    args = parser.parse_args()

    # Set container runtime if specified
    if getattr(args, "runtime", None):
        set_container_runtime(args.runtime)
        print(f"Using container runtime: {args.runtime}")
    else:
        runtime = detect_container_runtime()
        if runtime:
            print(f"Auto-detected container runtime: {runtime}")

    if args.command == "install":
        install_system(mode=getattr(args, "mode", "dev"))
    elif args.command == "update":
        update_system(force=getattr(args, "force", False))
    elif args.command == "status":
        check_repositories_status(fetch=not getattr(args, "no_fetch", False))
    elif args.command == "lock":
        lock_components(args.components, lock_all=args.all)
    elif args.command == "unlock":
        unlock_components(args.components, unlock_all=args.all)
    elif args.command == "rollback":
        rollback_components(args.components, rollback_all=args.all)
    elif args.command == "build":
        basedir = os.getcwd()
        builder = SessionImageBuilder(basedir)
        use_cache = getattr(args, "cache", False)
        no_cache = not use_cache

        # Determine which images to build
        images_arg = getattr(args, "images", ["all"])
        if not images_arg or "all" in images_arg:
            print("Building all session images...")
            builder.rebuild_all(no_cache=no_cache)
        else:
            # Map short names to full image names
            image_map = {
                "operations": "visp-operations-session",
                "rstudio": "visp-rstudio-session",
                "jupyter": "visp-jupyter-session",
            }
            images_to_build = [image_map[img] for img in images_arg if img in image_map]
            print(f"Building images: {', '.join(images_arg)}...")
            builder.rebuild_all(no_cache=no_cache, images_to_build=images_to_build)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
