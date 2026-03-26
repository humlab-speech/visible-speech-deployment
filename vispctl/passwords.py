"""Password generation and environment file management for VISP."""

import getpass
import os
import random
import shutil
import string
from typing import Optional


def generate_random_string(length: int = 32) -> str:
    """Generate a random string for passwords."""
    chars = string.ascii_letters + string.digits
    return "".join(random.choice(chars) for _ in range(length))


class EnvFile:
    """
    Manages .env file operations.
    Encapsulates reading, writing, and updating environment variables.
    """

    def __init__(self, path: str = ".env"):
        """
        Initialize environment file manager.

        Args:
            path: Path to .env file
        """
        self.path = path
        self.vars: dict[str, str] = {}
        self.comments: dict[str, str] = {}  # Store comments for each variable
        self._load()

    def _load(self) -> None:
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

    def get(self, key: str, default: Optional[str] = None) -> Optional[str]:
        """Get an environment variable value."""
        return self.vars.get(key, default)

    def set(self, key: str, value: str, comment: Optional[str] = None) -> None:
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

    def save(self) -> None:
        """Write variables back to .env file."""
        with open(self.path, "w") as f:
            for key, value in sorted(self.vars.items()):
                if key in self.comments:
                    f.write(f"# {self.comments[key]}\n")
                f.write(f"{key}={value}\n")

    def exists(self) -> bool:
        """Check if .env file exists."""
        return os.path.exists(self.path)


def setup_env_file(auto_passwords: bool = True, interactive: bool = False) -> None:
    """
    Setup .env and .env.secrets files with required configuration.
    Uses EnvFile class for safe file manipulation.
    Separates non-sensitive config (.env) from sensitive credentials (.env.secrets).

    Args:
        auto_passwords: If True, auto-generate passwords. If False, use interactive mode.
        interactive: If True, prompt for all passwords interactively.
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
    placeholder = "/your/path/to/visible-speech-deployment"
    for key, value in defaults.items():
        current = env.get(key)
        if not current or current == placeholder:
            env.set(key, value)

    # 2. Check MongoDB Special Case
    mongo_data_exists = os.path.exists("./mounts/mongo/data") and os.listdir("./mounts/mongo/data")
    current_mongo_pass = secrets.get("MONGO_ROOT_PASSWORD")  # Check secrets file

    if mongo_data_exists and current_mongo_pass:
        print("⚠️  MongoDB database already exists with data.")
        print("   Keeping existing MONGO_ROOT_PASSWORD to avoid authentication issues.")
    elif mongo_data_exists and not current_mongo_pass:
        print("⚠️  WARNING: MongoDB data exists but no MONGO_ROOT_PASSWORD in .env.secrets!")
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
        if var in ("MONGO_ROOT_PASSWORD", "MONGO_INITDB_ROOT_PASSWORD") and mongo_data_exists:
            continue

        if interactive:
            # Interactive mode: prompt for each password
            password = getpass.getpass(f"   Enter {var}: ")
            secrets.set(var, password, comment)
        elif auto_passwords:
            # Auto mode: generate random passwords
            password = generate_random_string(32)
            secrets.set(var, password, comment)
            print(f"✅ Generated {var}")
        else:
            # Manual mode: skip
            print(f"⚠️  {var} not set (use --auto-passwords or --interactive-passwords)")

    # 4. Remove any password variables from .env (they should only be in .env.secrets)
    # This handles leftover keys from .env-example that were copied with empty values
    for var in password_vars.keys():
        if var in env.vars:
            print(f"ℹ️  Removing {var} from .env (passwords belong in .env.secrets)")
            del env.vars[var]

    # Save both files
    env.save()
    secrets.save()

    print("✅ Environment files updated:")
    print("   - .env (non-sensitive config)")
    print("   - .env.secrets (passwords and tokens)")
