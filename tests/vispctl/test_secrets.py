from vispctl.secrets import (
    SecretManager,
    parse_quadlet_secret_map,
    secrets_to_remove_for_uninstall,
)


class FakeRunner:
    def __init__(self):
        self.calls = []

    def run(self, cmd, capture=False, check=True, **kwargs):
        self.calls.append((cmd, kwargs))

        class R:
            def __init__(self, returncode=0, stdout="", stderr=""):
                self.returncode = returncode
                self.stdout = stdout
                self.stderr = stderr

        # Simulate list output
        if cmd[:3] == ["podman", "secret", "ls"]:
            return R(returncode=0, stdout="visp_mongo_root_password\nother_secret")
        if cmd[:3] == ["podman", "secret", "inspect"]:
            # pretend secret doesn't exist
            return R(returncode=1)
        if cmd[:3] == ["podman", "secret", "create"]:
            return R(returncode=0)
        if cmd[:3] == ["podman", "secret", "rm"]:
            return R(returncode=0)
        return R()


def test_load_and_merge_envs(tmp_path):
    p = tmp_path
    env = p / ".env"
    env.write_text("BASE_DOMAIN=example.com\nMONGO_ROOT_PASSWORD=pass1\n")
    secrets = p / ".env.secrets"
    secrets.write_text("MONGO_ROOT_PASSWORD=secretpass\nVISP_API_ACCESS_TOKEN=token123\n")

    sm = SecretManager(FakeRunner(), project_dir=p)
    all_env = sm.load_all()
    assert all_env["MONGO_ROOT_PASSWORD"] == "secretpass"
    assert all_env["BASE_DOMAIN"] == "example.com"
    assert all_env["VISP_API_ACCESS_TOKEN"] == "token123"


def test_get_derived():
    sm = SecretManager(FakeRunner())
    env = {
        "MONGO_ROOT_PASSWORD": "pw",
        "MONGO_EXPRESS_PASSWORD": "mongo-express-pw",
        "BASE_DOMAIN": "example.com",
        "SSP_ADMIN_PASSWORD": "adminpw",
        "SSP_SALT": "devsalt",
    }
    derived = sm.get_derived(env)
    assert derived["visp_mongo_root_password"] == "pw"
    assert derived["visp_mongo_express_password"] == "mongo-express-pw"
    assert "visp_mongo_uri" in derived
    assert derived["visp_media_file_base_url"] == "https://artic.example.com"
    assert derived["visp_ssp_admin_password"] == "adminpw"
    assert derived["visp_ssp_salt"] == "devsalt"


def test_create_remove_list_secrets():
    fr = FakeRunner()
    sm = SecretManager(fr)
    secrets = {"visp_test": "val"}

    sm.create_secrets(secrets)
    # verify create was called
    assert any(c[0][:3] == ["podman", "secret", "create"] for c in fr.calls)

    lst = sm.list_secrets()
    assert "visp_mongo_root_password" in lst

    sm.remove_secrets(["visp_test"])
    assert any(c[0][:3] == ["podman", "secret", "rm"] for c in fr.calls)


# ── Scoped uninstall secret removal ───────────────────────────────────────────


def test_parse_quadlet_secret_map(tmp_path):
    """parse_quadlet_secret_map maps each service to its Secret= references."""
    (tmp_path / "mongo.container").write_text(
        "ContainerImage=localhost/visp-mongo:latest\n"
        "Secret=visp_mongo_root_password,type=env,target=MONGO_INITDB_ROOT_PASSWORD\n"
    )
    (tmp_path / "apache.container").write_text(
        "ContainerImage=localhost/visp-apache:latest\n"
        "Secret=visp_api_access_token,type=env,target=HS_API_ACCESS_TOKEN\n"
        "Secret=visp_mongo_root_password,type=env,target=MONGO_ROOT_PASSWORD\n"
        "# Secret=commented_out should be ignored\n"
    )
    (tmp_path / "artic.container").write_text("ContainerImage=localhost/visp-artic:latest\n")
    (tmp_path / "visp-net.network").write_text("NetworkName=systemd-visp-net\n")

    mapping = parse_quadlet_secret_map(tmp_path)

    assert mapping["mongo"] == {"visp_mongo_root_password"}
    assert mapping["apache"] == {"visp_api_access_token", "visp_mongo_root_password"}
    # No Secret= lines → omitted; .network files are ignored
    assert "artic" not in mapping
    assert "visp-net" not in mapping


def test_parse_quadlet_secret_map_missing_dir(tmp_path):
    """A missing quadlets dir yields an empty map (no crash)."""
    assert parse_quadlet_secret_map(tmp_path / "does-not-exist") == {}


def test_uninstall_all_removes_everything():
    """remove_all=True returns every existing secret."""
    existing = ["visp_a", "visp_b", "visp_c"]
    assert secrets_to_remove_for_uninstall({"mongo"}, {}, existing, remove_all=True) == [
        "visp_a",
        "visp_b",
        "visp_c",
    ]


def test_uninstall_single_removes_only_exclusive_secrets():
    """A service's exclusive secrets are removed; shared ones are kept."""
    secret_map = {
        "mongo": {"visp_mongo_root_password"},
        "local-idp": {"visp_ssp_admin_password", "visp_ssp_salt"},
        "apache": {"visp_api_access_token", "visp_mongo_root_password"},
    }
    existing = [
        "visp_mongo_root_password",
        "visp_ssp_admin_password",
        "visp_ssp_salt",
        "visp_api_access_token",
    ]

    # Uninstalling local-idp removes its two exclusive SSP secrets only.
    removed = secrets_to_remove_for_uninstall({"local-idp"}, secret_map, existing)
    assert removed == ["visp_ssp_admin_password", "visp_ssp_salt"]

    # Uninstalling mongo removes nothing: its only secret is shared with apache.
    assert secrets_to_remove_for_uninstall({"mongo"}, secret_map, existing) == []


def test_uninstall_keeps_shared_secret_for_other_services():
    """Uninstalling one of several users of a shared secret keeps the secret."""
    secret_map = {
        "mongo": {"visp_mongo_root_password"},
        "session-manager": {"visp_mongo_root_password", "visp_api_access_token"},
        "wsrng-server": {"visp_mongo_root_password"},
    }
    existing = ["visp_mongo_root_password", "visp_api_access_token"]

    # mongo's secret is still needed by session-manager and wsrng-server.
    assert secrets_to_remove_for_uninstall({"mongo"}, secret_map, existing) == []
    # session-manager's exclusive secret (api token) is removed; shared one kept.
    assert secrets_to_remove_for_uninstall({"session-manager"}, secret_map, existing) == ["visp_api_access_token"]


# ── round-2 pins: failure paths and value-never-in-argv ──────────────────────

import types

from vispctl.secrets import SecretManager


class ScriptedRunner:
    def __init__(self, script):
        self.script = script
        self.calls = []

    def run(self, cmd, **kwargs):
        self.calls.append((list(cmd), kwargs))
        rc = self.script(len(self.calls) - 1) if callable(self.script) else self.script
        return types.SimpleNamespace(returncode=rc, stdout="", stderr="nope")


def _mgr(runner, tmp_path):
    return SecretManager(runner, project_dir=tmp_path)


def test_existing_secret_is_removed_before_create(tmp_path):
    fr = ScriptedRunner(0)
    _mgr(fr, tmp_path).create_secrets({"visp_x": "v"})
    tools = [c[2] for c, _k in fr.calls]
    assert tools[:3] == ["inspect", "rm", "create"]


def test_create_failure_is_reported(capsys, tmp_path):
    fr = ScriptedRunner(lambda i: 0 if i == 0 else 1)  # inspect ok, create fails
    _mgr(fr, tmp_path).create_secrets({"visp_x": "v"})
    out = capsys.readouterr().out
    assert "failed" in out and "✓" not in out


def test_secret_value_never_enters_argv(tmp_path):
    # The value must travel via input= (stdin); a ps-visible argv leak is the
    # manual mutant that kept the pre-round-2 suite green.
    fr = ScriptedRunner(0)
    _mgr(fr, tmp_path).create_secrets({"visp_x": "sup3r-s3cret"})
    create = [(c, k) for c, k in fr.calls if c[2:3] == ["create"]]
    assert create, "expected a create call"
    for cmd, kwargs in fr.calls:
        assert not any("sup3r-s3cret" in el for el in cmd)
    assert create[0][1].get("input") == "sup3r-s3cret"


def test_remove_failure_is_reported(capsys, tmp_path):
    fr = ScriptedRunner(1)
    _mgr(fr, tmp_path).remove_secrets(["visp_x"])
    out = capsys.readouterr().out
    assert "remove failed" in out


def test_list_secrets_returns_empty_on_failure(tmp_path):
    fr = ScriptedRunner(1)
    assert _mgr(fr, tmp_path).list_secrets() == []
