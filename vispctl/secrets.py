"""SecretManager for loading env files and managing Podman secrets."""

from __future__ import annotations

from pathlib import Path
from typing import Dict, List

from .config import get_config
from .env import load_all_env as _load_all_env
from .env import load_env_file
from .runner import Runner


class SecretManager:
    def __init__(self, runner: Runner, project_dir: Path | None = None):
        self.runner = runner
        self.project_dir = Path(project_dir) if project_dir else get_config().project_dir

    def load_env(self, env_file_path: Path) -> Dict[str, str]:
        return load_env_file(env_file_path)

    def load_all(self) -> Dict[str, str]:
        return _load_all_env(self.project_dir)

    def get_derived(self, env_vars: Dict[str, str]) -> Dict[str, str]:
        secrets: Dict[str, str] = {}
        if "MONGO_ROOT_PASSWORD" in env_vars:
            secrets["visp_mongo_root_password"] = env_vars["MONGO_ROOT_PASSWORD"]
            secrets["visp_mongo_uri"] = f"mongodb://root:{env_vars['MONGO_ROOT_PASSWORD']}@mongo:27017"
        if "MONGO_EXPRESS_PASSWORD" in env_vars:
            secrets["visp_mongo_express_password"] = env_vars["MONGO_EXPRESS_PASSWORD"]
        if "VISP_API_ACCESS_TOKEN" in env_vars:
            secrets["visp_api_access_token"] = env_vars["VISP_API_ACCESS_TOKEN"]
        if "TEST_USER_LOGIN_KEY" in env_vars:
            secrets["visp_test_user_login_key"] = env_vars["TEST_USER_LOGIN_KEY"]
        if "BASE_DOMAIN" in env_vars:
            secrets["visp_media_file_base_url"] = f"https://artic.{env_vars['BASE_DOMAIN']}"
        if "MATOMO_DB_ROOT_PASSWORD" in env_vars:
            secrets["visp_matomo_db_root_password"] = env_vars["MATOMO_DB_ROOT_PASSWORD"]
        if "MATOMO_DB_USER" in env_vars:
            secrets["visp_matomo_db_user"] = env_vars["MATOMO_DB_USER"]
        if "MATOMO_DB_PASSWORD" in env_vars:
            secrets["visp_matomo_db_password"] = env_vars["MATOMO_DB_PASSWORD"]
        if "SSP_ADMIN_PASSWORD" in env_vars:
            secrets["visp_ssp_admin_password"] = env_vars["SSP_ADMIN_PASSWORD"]
        if "SSP_SALT" in env_vars:
            secrets["visp_ssp_salt"] = env_vars["SSP_SALT"]
        return secrets

    def create_secrets(self, secrets: Dict[str, str]) -> None:
        for name, value in secrets.items():
            result = self.runner.run(["podman", "secret", "inspect", name], capture=True, check=False)
            if result.returncode == 0:
                self.runner.run(["podman", "secret", "rm", name], capture=True, check=False)

            proc = self.runner.run(
                ["podman", "secret", "create", name, "-"],
                capture=True,
                check=False,
                input=value,
            )
            if proc.returncode == 0:
                print(f"  ✓ Secret '{name}': created")
            else:
                print(f"  ✗ Secret '{name}': failed - {proc.stderr}")

    def remove_secrets(self, names: List[str]) -> None:
        for name in names:
            res = self.runner.run(["podman", "secret", "rm", name], capture=True, check=False)
            if res.returncode == 0:
                print(f"  ✓ Secret '{name}': removed")

    def list_secrets(self) -> List[str]:
        res = self.runner.run(
            ["podman", "secret", "ls", "--format", "{{.Name}}"],
            capture=True,
            check=False,
        )
        if res.returncode == 0:
            return [n for n in res.stdout.strip().split("\n") if n.startswith("visp_")]
        return []
