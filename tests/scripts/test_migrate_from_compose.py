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
    "ABS_ROOT_PATH": "/srv/old-visp",
    "HTTP_PORT": "80",
    "GITLAB_HOME": "/srv/gitlab",
    "MONGO_ROOT_PASSWORD": "mongo-pw",
    "MATOMO_DB_USER": "matomo",
    "MATOMO_DB_PASSWORD": "matomo-pw",
    "MATOMO_DB_ROOT_PASSWORD": "matomo-root-pw",
    "TEST_USER_LOGIN_KEY": "",
}
EXAMPLE = {
    "BASE_DOMAIN": "visp.local",
    "ABS_ROOT_PATH": "/your/path",
    "HTTP_PORT": "8081",
    "ADMIN_EMAIL": "",
    "MONGO_ROOT_PASSWORD": "",
}


class TestSubids:
    def test_parse_skips_malformed_lines(self, mig):
        text = "alice:100000:65536\n\nbroken\nbob:165536:65536\n"
        assert mig.parse_subid_file(text) == [("alice", 100000, 65536), ("bob", 165536, 65536)]

    def test_next_start_follows_highest_range(self, mig):
        entries = [("alice", 165536, 65536), ("bob", 100000, 65536)]
        assert mig.next_subid_start(entries) == 231072

    def test_next_start_empty_uses_minimum(self, mig):
        assert mig.next_subid_start([]) == 100000


class TestPlanEnv:
    def test_overrides_old_values_and_defaults(self, mig):
        env, _, _, _ = mig.plan_env(OLD_ENV, EXAMPLE, {"ABS_ROOT_PATH": "/data/visp/new", "HTTP_PORT": "8081"})
        assert env == {
            "BASE_DOMAIN": "visp.example.org",  # carried from old
            "ABS_ROOT_PATH": "/data/visp/new",  # override beats old
            "HTTP_PORT": "8081",
            "ADMIN_EMAIL": "",  # example default
        }

    def test_secrets_carried_and_missing_generated(self, mig):
        _, secrets, _, generated = mig.plan_env(OLD_ENV, EXAMPLE, {})
        assert secrets["MONGO_ROOT_PASSWORD"] == "mongo-pw"
        assert secrets["MATOMO_DB_USER"] == "matomo"
        assert "TEST_USER_LOGIN_KEY" in generated  # empty in old .env
        assert len(secrets["TEST_USER_LOGIN_KEY"]) == 32
        assert set(secrets) == set(mig.SECRET_KEYS)

    def test_reports_dropped_keys(self, mig):
        _, _, dropped, _ = mig.plan_env(OLD_ENV, EXAMPLE, {})
        assert dropped == ["GITLAB_HOME"]

    def test_missing_database_password_is_fatal(self, mig):
        old = dict(OLD_ENV, MONGO_ROOT_PASSWORD="")
        with pytest.raises(mig.MigrationError, match="MONGO_ROOT_PASSWORD"):
            mig.plan_env(old, EXAMPLE, {})


def test_render_env_keeps_comments_and_drops_secret_lines(mig):
    example = "# Domain\nBASE_DOMAIN=visp.local\n\n#Mongo\nMONGO_ROOT_PASSWORD=\nHTTP_PORT=8081\n"
    out = mig.render_env(example, {"BASE_DOMAIN": "visp.example.org", "HTTP_PORT": "8081"})
    assert out == "# Domain\nBASE_DOMAIN=visp.example.org\n\n#Mongo\nHTTP_PORT=8081\n"


def test_patch_matomo_dbname_only_touches_database_section(mig):
    config = '[database]\nhost = "matomo-db"\ndbname = "old_db"\n\n[database_tests]\ndbname = "tests"\n'
    out = mig.patch_matomo_dbname(config, "matomo_db")
    assert 'dbname = "matomo_db"' in out
    assert 'dbname = "tests"' in out
    assert 'dbname = "old_db"' not in out


def test_proxy_conf_targets_upstream_with_websocket_headers(mig):
    conf = mig.render_proxy_conf("/etc/tls/cert.pem", "/etc/tls/key.pem", 8081)
    assert "proxy_pass http://127.0.0.1:8081;" in conf
    assert "proxy_set_header Upgrade $http_upgrade;" in conf
    assert "ssl_certificate_key /etc/tls/key.pem;" in conf
    assert "client_max_body_size 10G;" in conf


def test_mongo_config_yaml_escapes_password(mig):
    assert mig.mongo_config_yaml('p"a:ss#') == 'password: "p\\"a:ss#"\n'
