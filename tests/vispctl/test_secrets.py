from vispctl.secrets import SecretManager


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
    secrets.write_text(
        "MONGO_ROOT_PASSWORD=secretpass\nVISP_API_ACCESS_TOKEN=token123\n"
    )

    sm = SecretManager(FakeRunner(), project_dir=p)
    all_env = sm.load_all()
    assert all_env["MONGO_ROOT_PASSWORD"] == "secretpass"
    assert all_env["BASE_DOMAIN"] == "example.com"
    assert all_env["VISP_API_ACCESS_TOKEN"] == "token123"


def test_get_derived():
    sm = SecretManager(FakeRunner())
    env = {"MONGO_ROOT_PASSWORD": "pw", "BASE_DOMAIN": "example.com"}
    derived = sm.get_derived(env)
    assert derived["visp_mongo_root_password"] == "pw"
    assert "visp_mongo_uri" in derived
    assert derived["visp_media_file_base_url"] == "https://emu-webapp.example.com"


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
