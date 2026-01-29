"""SecretManager for loading env files and managing Podman secrets."""

from __future__ import annotations
from pathlib import Path
from typing import Dict, List

from .runner import Runner


class SecretManager:
    def __init__(self, runner: Runner, project_dir: Path | None = None):
        self.runner = runner
        self.project_dir = (
            Path(project_dir) if project_dir else Path(__file__).parent.parent
        )

    def load_env(self, env_file_path: Path) -> Dict[str, str]:
        env_vars: Dict[str, str] = {}
        if not env_file_path.exists():
            return env_vars
        with open(env_file_path, "r") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if "=" in line:
                    key, value = line.split("=", 1)
                    env_vars[key.strip()] = value.strip()
        return env_vars

    def load_all(self) -> Dict[str, str]:
        env_vars: Dict[str, str] = {}
        env_file = Path(self.project_dir) / ".env"
        if env_file.exists():
            env_vars.update(self.load_env(env_file))

        secrets_file = Path(self.project_dir) / ".env.secrets"
        if secrets_file.exists():
            env_vars.update(self.load_env(secrets_file))
        return env_vars

    def get_derived(self, env_vars: Dict[str, str]) -> Dict[str, str]:
        secrets: Dict[str, str] = {}
        if "MONGO_ROOT_PASSWORD" in env_vars:
            secrets["visp_mongo_root_password"] = env_vars["MONGO_ROOT_PASSWORD"]
            secrets["visp_mongo_uri"] = (
                f"mongodb://root:{env_vars['MONGO_ROOT_PASSWORD']}@mongo:27017"
            )
        if "VISP_API_ACCESS_TOKEN" in env_vars:
            secrets["visp_api_access_token"] = env_vars["VISP_API_ACCESS_TOKEN"]
        if "TEST_USER_LOGIN_KEY" in env_vars:
            secrets["visp_test_user_login_key"] = env_vars["TEST_USER_LOGIN_KEY"]
        if "BASE_DOMAIN" in env_vars:
            secrets["visp_media_file_base_url"] = (
                f"https://emu-webapp.{env_vars['BASE_DOMAIN']}"
            )
        return secrets

    def create_secrets(self, secrets: Dict[str, str]) -> None:
        for name, value in secrets.items():
            result = self.runner.run(
                ["podman", "secret", "inspect", name], capture=True, check=False
            )
            if result.returncode == 0:
                self.runner.run(
                    ["podman", "secret", "rm", name], capture=True, check=False
                )

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
            res = self.runner.run(
                ["podman", "secret", "rm", name], capture=True, check=False
            )
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
