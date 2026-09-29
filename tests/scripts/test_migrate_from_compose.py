"""Tests for the pure helpers in scripts/migrate-from-compose.py."""

import importlib.util
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "migrate-from-compose.py"


@pytest.fixture(scope="module")
def mig():
    spec = importlib.util.spec_from_file_location("migrate_from_compose", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


OLD_ENV = {
    "BASE_DOMAIN": "visp.example.org",
    "ADMIN_EMAIL": "admin@example.org",
    "ABS_ROOT_PATH": "/srv/old-visp",
    "HTTP_PORT": "80",
    "GITLAB_HOME": "/srv/gitlab",
    "MONGO_ROOT_PASSWORD": "old-mongo-pw",
}
EXAMPLE = {
    "BASE_DOMAIN": "visp.local",
    "ADMIN_EMAIL": "",
    "ABS_ROOT_PATH": "/your/path",
    "HTTP_PORT": "8081",
    "MONGO_ROOT_PASSWORD": "",
}


class TestSubids:
    def test_parse_skips_malformed_lines(self, mig):
        text = "alice:100000:65536\n\nbroken\nbob:165536:65536\n"
        assert mig.parse_subid_file(text) == [("alice", 100000, 65536), ("bob", 165536, 65536)]

    def test_next_start_follows_highest_range(self, mig):
        assert mig.next_subid_start([("alice", 165536, 65536), ("bob", 100000, 65536)]) == 231072

    def test_next_start_empty_uses_minimum(self, mig):
        assert mig.next_subid_start([]) == 100000


class TestPlanEnv:
    def test_carries_only_whitelisted_keys(self, mig):
        env, carried = mig.plan_env(OLD_ENV, EXAMPLE, {"ABS_ROOT_PATH": "/data/visp/new"})
        assert env == {
            "BASE_DOMAIN": "visp.example.org",  # carried
            "ADMIN_EMAIL": "admin@example.org",  # carried
            "ABS_ROOT_PATH": "/data/visp/new",  # override
            "HTTP_PORT": "8081",  # example default, not the old 80
        }
        assert carried == ["BASE_DOMAIN", "ADMIN_EMAIL"]

    def test_never_takes_secrets_from_the_old_env(self, mig):
        env, _ = mig.plan_env(OLD_ENV, EXAMPLE, {})
        assert "MONGO_ROOT_PASSWORD" not in env
        assert "old-mongo-pw" not in env.values()


class TestPlanSecrets:
    def test_generates_all_when_none_exist(self, mig):
        values, generated = mig.plan_secrets({})
        assert set(values) == set(mig.SECRET_KEYS) == set(generated)
        assert values["MATOMO_DB_USER"] == "matomo"
        assert len(values["MONGO_ROOT_PASSWORD"]) == 32

    def test_keeps_existing_secrets(self, mig):
        values, generated = mig.plan_secrets({"MONGO_ROOT_PASSWORD": "keep-me-please"})
        assert values["MONGO_ROOT_PASSWORD"] == "keep-me-please"
        assert "MONGO_ROOT_PASSWORD" not in generated


def test_render_env_keeps_comments_and_drops_secret_lines(mig):
    example = "# Domain\nBASE_DOMAIN=visp.local\n\n#Mongo\nMONGO_ROOT_PASSWORD=\nHTTP_PORT=8081\n"
    out = mig.render_env(example, {"BASE_DOMAIN": "visp.example.org", "HTTP_PORT": "8081"})
    assert out == "# Domain\nBASE_DOMAIN=visp.example.org\n\n#Mongo\nHTTP_PORT=8081\n"


class TestPatchIni:
    CONFIG = (
        "; <?php exit; ?> DO NOT REMOVE THIS LINE\n"
        "[database]\n"
        'host = "matomo-db"\n'
        'username = "old_user"\n'
        'password = "old_pw"\n'
        'dbname = "old_db"\n'
        'tables_prefix = "matomo_"\n'
        "\n"
        "[General]\n"
        'salt = "keep-this-salt"\n'
    )

    def test_replaces_keys_in_section_only(self, mig):
        out = mig.patch_ini_section(
            self.CONFIG, "[database]", {"username": "matomo", "password": "new_pw", "dbname": "matomo_db"}
        )
        assert 'username = "matomo"' in out
        assert 'password = "new_pw"' in out
        assert 'dbname = "matomo_db"' in out
        assert "old_pw" not in out and "old_user" not in out
        assert 'tables_prefix = "matomo_"' in out
        assert 'salt = "keep-this-salt"' in out

    def test_adds_missing_keys_before_next_section(self, mig):
        out = mig.patch_ini_section('[database]\nhost = "x"\n[General]\na = 1\n', "[database]", {"port": "3306"})
        assert out == '[database]\nhost = "x"\nport = "3306"\n[General]\na = 1\n'

    def test_adds_missing_keys_when_section_is_last(self, mig):
        out = mig.patch_ini_section('[General]\na = 1\n[database]\nhost = "x"', "[database]", {"port": "3306"})
        assert out.endswith('[database]\nhost = "x"\nport = "3306"\n')


class TestProxyConf:
    OLD = "server {\n    listen 443 ssl;\n    location / { proxy_pass http://127.0.0.1:80; }\n}\n"

    def test_cutover_config_targets_new_apache(self, mig):
        conf = mig.render_proxy_conf("/etc/tls/cert.pem", "/etc/tls/key.pem", 8081)
        assert "listen 443 ssl;" in conf
        assert "proxy_pass http://127.0.0.1:8081;" in conf
        assert "proxy_set_header Upgrade $http_upgrade;" in conf
        assert "map $http_upgrade $connection_upgrade" in conf

    def test_preview_keeps_the_old_server_verbatim(self, mig):
        conf = mig.add_preview_block(self.OLD, "/c", "/k", 8081)
        assert conf.startswith(self.OLD)
        assert "listen 127.0.0.1:8443 ssl;" in conf
        assert conf.count("proxy_pass http://127.0.0.1:8081;") == 1

    def test_preview_is_idempotent_and_removable(self, mig):
        once = mig.add_preview_block(self.OLD, "/c", "/k", 8081)
        assert mig.add_preview_block(once, "/c", "/k", 8081) == once
        assert mig.strip_preview_block(once) == self.OLD


def test_compare_counts_reports_mismatches(mig):
    old = {"visp": {"users": 19, "projects": 17}, "wsrng": {"sessions": 56}}
    new = {"visp": {"users": 19, "projects": 16}}
    assert mig.compare_counts(old, new) == ["visp.projects: old 17, new 16", "wsrng.sessions: old 56, new None"]
    assert mig.compare_counts(old, old) == []


def test_app_databases_excludes_system_ones(mig):
    assert mig.app_databases(["local", "wsrng", "admin", "visp", "config"]) == ["visp", "wsrng"]


def test_mongo_config_yaml_escapes_password(mig):
    assert mig.mongo_config_yaml('p"a:ss#') == 'password: "p\\"a:ss#"\n'


def test_log_masks_secrets(mig, tmp_path):
    log = mig.Log()
    log.add_secret("s3cr3t-value")
    log.add_secret("short")  # too short to mask safely
    assert log.mask("pw=s3cr3t-value user=short") == "pw=******** user=short"


def test_podman_tmp_files_point_pulls_and_builds_at_the_dir(mig):
    files = mig.podman_tmp_files("/data/visp/tmp")
    assert files[".config/containers/containers.conf"] == '[engine]\nimage_copy_tmp_dir = "/data/visp/tmp"\n'
    assert files[".config/environment.d/10-tmpdir.conf"] == "TMPDIR=/data/visp/tmp\n"


def test_profile_tmpdir_is_appended_once(mig):
    once = mig.profile_with_tmpdir("umask 022", "/data/visp/tmp")
    assert once.startswith("umask 022\n")
    assert once.endswith("export TMPDIR=/data/visp/tmp\n")
    assert mig.profile_with_tmpdir(once, "/data/visp/tmp") == once
