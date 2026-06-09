"""Tests for vispctl/install.py."""

from pathlib import Path
from types import SimpleNamespace

from vispctl.install import (
    EMU_WEBAPP_IMAGE,
    WSRNG_IMAGE,
    _ensure_webclient_dist,
    _resolve_image_uid,
    cleanup_disabled_optional_services,
    fix_mongo_mount_ownership,
    fix_writable_permissions,
    generate_tracker_config,
    install_quadlets,
    normalize_repository_ownership,
    scaffold_directories,
    verify_repository_write_access,
)
from vispctl.service import Service

# ---------------------------------------------------------------------------
# scaffold_directories
# ---------------------------------------------------------------------------


def test_scaffold_creates_missing_dir(tmp_path):
    project_dir = tmp_path / "project"
    project_dir.mkdir()
    quadlets_dir = tmp_path / "quadlets"
    quadlets_dir.mkdir()

    quadlet = quadlets_dir / "test.container"
    quadlet.write_text(f"Volume={project_dir}/mounts/data:/data:Z\n")

    created = scaffold_directories(project_dir, quadlets_dir, lambda c: c)

    assert (project_dir / "mounts/data").is_dir()
    assert created >= 1


def test_scaffold_skips_existing_dir(tmp_path):
    project_dir = tmp_path / "project"
    project_dir.mkdir()
    existing = project_dir / "mounts/data"
    existing.mkdir(parents=True)
    quadlets_dir = tmp_path / "quadlets"
    quadlets_dir.mkdir()

    quadlet = quadlets_dir / "test.container"
    quadlet.write_text(f"Volume={project_dir}/mounts/data:/data:Z\n")

    created = scaffold_directories(project_dir, quadlets_dir, lambda c: c)

    assert created == 0


def test_scaffold_skips_external(tmp_path):
    project_dir = tmp_path / "project"
    project_dir.mkdir()
    quadlets_dir = tmp_path / "quadlets"
    quadlets_dir.mkdir()

    quadlet = quadlets_dir / "test.container"
    quadlet.write_text(f"Volume={project_dir}/external/webclient/dist:/var/www:Z\n")

    created = scaffold_directories(project_dir, quadlets_dir, lambda c: c)

    assert not (project_dir / "external").exists()
    # only special dirs (whisper/api, podman-proxy) may be created
    assert created == 0


def test_scaffold_creates_file_placeholder(tmp_path):
    project_dir = tmp_path / "project"
    project_dir.mkdir()
    quadlets_dir = tmp_path / "quadlets"
    quadlets_dir.mkdir()

    quadlet = quadlets_dir / "test.container"
    quadlet.write_text(f"Volume={project_dir}/mounts/apache/vc.js:/var/www/vc.js:Z\n")

    scaffold_directories(project_dir, quadlets_dir, lambda c: c)

    placeholder = project_dir / "mounts/apache/vc.js"
    assert placeholder.exists()


def test_scaffold_always_creates_special_dirs(tmp_path):
    project_dir = tmp_path / "project"
    project_dir.mkdir()
    quadlets_dir = tmp_path / "quadlets"
    quadlets_dir.mkdir()
    # empty quadlets dir — no Volume= lines
    (quadlets_dir / "empty.container").write_text("[Container]\nImage=foo\n")

    scaffold_directories(project_dir, quadlets_dir, lambda c: c)

    assert (project_dir / "mounts/whisper/api").is_dir()
    assert (project_dir / "mounts/podman-proxy").is_dir()


# ---------------------------------------------------------------------------
# fix_writable_permissions
# ---------------------------------------------------------------------------


def test_fix_writable_permissions_sets_777(tmp_path):
    project_dir = tmp_path / "project"
    uploads = project_dir / "mounts/apache/apache/uploads"
    uploads.mkdir(parents=True)
    uploads.chmod(0o755)

    fixed = fix_writable_permissions(project_dir)

    assert fixed == 1
    assert (uploads.stat().st_mode & 0o777) == 0o777


def test_fix_writable_permissions_skips_already_777(tmp_path):
    project_dir = tmp_path / "project"
    uploads = project_dir / "mounts/apache/apache/uploads"
    uploads.mkdir(parents=True)
    uploads.chmod(0o777)

    fixed = fix_writable_permissions(project_dir)

    assert fixed == 0


def test_fix_writable_permissions_skips_missing(tmp_path):
    project_dir = tmp_path / "project"
    project_dir.mkdir()
    # no mounts/ subdirs at all
    fixed = fix_writable_permissions(project_dir)
    assert fixed == 0


# ---------------------------------------------------------------------------
# fix_mongo_mount_ownership
# ---------------------------------------------------------------------------


def test_fix_mongo_mount_ownership_runs_unshare_chown(monkeypatch, tmp_path):
    project_dir = tmp_path / "project"
    data_dir = project_dir / "mounts/mongo/data"
    logs_dir = project_dir / "mounts/mongo/logs"
    data_dir.mkdir(parents=True)
    logs_dir.mkdir(parents=True)

    calls: list[list[str]] = []

    def fake_run(cmd, capture_output, text):  # noqa: ANN001
        calls.append(cmd)
        return SimpleNamespace(returncode=0, stderr="")

    monkeypatch.setattr("vispctl.install.subprocess.run", fake_run)

    fixed = fix_mongo_mount_ownership(project_dir)

    assert fixed == 2
    assert calls == [
        ["podman", "unshare", "chown", "-R", "999:999", str(data_dir)],
        ["podman", "unshare", "chmod", "-R", "u+rwX,go-rwx", str(data_dir)],
        ["podman", "unshare", "chown", "-R", "999:999", str(logs_dir)],
        ["podman", "unshare", "chmod", "-R", "u+rwX,go-rwx", str(logs_dir)],
    ]


def test_fix_mongo_mount_ownership_skips_missing(tmp_path):
    project_dir = tmp_path / "project"
    project_dir.mkdir()

    fixed = fix_mongo_mount_ownership(project_dir)

    assert fixed == 0


# ---------------------------------------------------------------------------
# verify_repository_write_access / normalize_repository_ownership / _resolve_image_uid
# ---------------------------------------------------------------------------


def _patch_run(monkeypatch, handler):
    """Replace vispctl.install.subprocess.run with *handler(cmd) -> namespace*."""

    def fake_run(cmd, capture_output=False, text=False):  # noqa: ANN001, ARG001
        return handler(cmd)

    monkeypatch.setattr("vispctl.install.subprocess.run", fake_run)


def test_resolve_image_uid_numeric(monkeypatch):
    _patch_run(monkeypatch, lambda cmd: SimpleNamespace(returncode=0, stdout="1000\n", stderr=""))
    assert _resolve_image_uid("img") == 1000


def test_resolve_image_uid_strips_gid(monkeypatch):
    _patch_run(monkeypatch, lambda cmd: SimpleNamespace(returncode=0, stdout="1000:1000\n", stderr=""))
    assert _resolve_image_uid("img") == 1000


def test_resolve_image_uid_empty_means_root(monkeypatch):
    _patch_run(monkeypatch, lambda cmd: SimpleNamespace(returncode=0, stdout="\n", stderr=""))
    assert _resolve_image_uid("img") == 0


def test_resolve_image_uid_username_lookup(monkeypatch):
    def handler(cmd):
        if cmd[1] == "image":  # podman image inspect ...
            return SimpleNamespace(returncode=0, stdout="node\n", stderr="")
        # podman run ... id -u node
        return SimpleNamespace(returncode=0, stdout="1000\n", stderr="")

    _patch_run(monkeypatch, handler)
    assert _resolve_image_uid("img") == 1000


def test_resolve_image_uid_missing_image(monkeypatch):
    _patch_run(monkeypatch, lambda cmd: SimpleNamespace(returncode=125, stdout="", stderr="no such image"))
    assert _resolve_image_uid("img") is None


def test_verify_skips_when_repos_missing(tmp_path):
    project_dir = tmp_path / "project"
    project_dir.mkdir()
    # mounts/repositories does not exist → skipped, treated as OK.
    assert verify_repository_write_access(project_dir) is True


def test_verify_skips_when_images_missing(monkeypatch, tmp_path):
    project_dir = tmp_path / "project"
    (project_dir / "mounts/repositories").mkdir(parents=True)
    _patch_run(monkeypatch, lambda cmd: SimpleNamespace(returncode=125, stdout="", stderr=""))
    assert verify_repository_write_access(project_dir) is True


def test_verify_passes_when_keepid_write_succeeds(monkeypatch, tmp_path):
    project_dir = tmp_path / "project"
    (project_dir / "mounts/repositories").mkdir(parents=True)

    def handler(cmd):
        if cmd[1] == "image":  # inspect → image exists (USER node = 1000)
            return SimpleNamespace(returncode=0, stdout="1000\n", stderr="")
        # the throwaway keep-id write probe succeeds
        assert cmd[0] == "podman" and cmd[1] == "run"
        assert "--userns" in cmd and "keep-id:uid=1000,gid=1000" in cmd
        assert "--user" in cmd and "1000:1000" in cmd
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    _patch_run(monkeypatch, handler)
    assert verify_repository_write_access(project_dir) is True
    # probe directory must be cleaned up
    assert not list((project_dir / "mounts/repositories").glob(".vispctl-permcheck-*"))


def test_verify_fails_when_keepid_write_denied(monkeypatch, tmp_path):
    project_dir = tmp_path / "project"
    (project_dir / "mounts/repositories").mkdir(parents=True)

    def handler(cmd):
        if cmd[1] == "image":
            return SimpleNamespace(returncode=0, stdout="1000\n", stderr="")
        return SimpleNamespace(returncode=1, stdout="", stderr="Permission denied")

    _patch_run(monkeypatch, handler)
    assert verify_repository_write_access(project_dir) is False
    assert not list((project_dir / "mounts/repositories").glob(".vispctl-permcheck-*"))


def test_normalize_repository_ownership_runs_unshare_chown(monkeypatch, tmp_path):
    project_dir = tmp_path / "project"
    repos = project_dir / "mounts/repositories"
    repos.mkdir(parents=True)

    calls: list[list[str]] = []

    def handler(cmd):
        calls.append(cmd)
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    _patch_run(monkeypatch, handler)

    assert normalize_repository_ownership(project_dir) is True
    assert calls == [["podman", "unshare", "chown", "-R", "0:0", str(repos)]]


def test_normalize_repository_ownership_skips_missing(tmp_path):
    project_dir = tmp_path / "project"
    project_dir.mkdir()
    # No subprocess call needed; missing path is a no-op success.
    assert normalize_repository_ownership(project_dir) is True


def test_normalize_repository_ownership_reports_failure(monkeypatch, tmp_path):
    project_dir = tmp_path / "project"
    (project_dir / "mounts/repositories").mkdir(parents=True)
    _patch_run(monkeypatch, lambda cmd: SimpleNamespace(returncode=1, stdout="", stderr="boom"))
    assert normalize_repository_ownership(project_dir) is False


def test_repository_writer_image_constants():
    assert WSRNG_IMAGE == "localhost/visp-wsrng-server:latest"
    assert EMU_WEBAPP_IMAGE == "localhost/visp-emu-webapp-server:latest"


# ---------------------------------------------------------------------------
# generate_tracker_config
# ---------------------------------------------------------------------------


def test_generate_tracker_config_renders_template(tmp_path):
    apache_dir = tmp_path / "mounts/apache/apache"
    apache_dir.mkdir(parents=True)
    template = apache_dir / "vc.js.template"
    template.write_text("var domain = '{{BASE_DOMAIN}}';")

    generate_tracker_config(tmp_path, {"BASE_DOMAIN": "visp.local"})

    output = apache_dir / "vc.js"
    assert output.exists()
    assert "visp.local" in output.read_text()
    assert "{{BASE_DOMAIN}}" not in output.read_text()


def test_generate_tracker_config_writes_placeholder_if_no_domain(tmp_path):
    apache_dir = tmp_path / "mounts/apache/apache"
    apache_dir.mkdir(parents=True)

    generate_tracker_config(tmp_path, {})

    output = apache_dir / "vc.js"
    assert output.exists()
    assert "not configured" in output.read_text()


def test_generate_tracker_config_leaves_existing_if_no_domain(tmp_path):
    apache_dir = tmp_path / "mounts/apache/apache"
    apache_dir.mkdir(parents=True)
    existing = apache_dir / "vc.js"
    existing.write_text("// existing")

    generate_tracker_config(tmp_path, {})

    assert existing.read_text() == "// existing"


# ---------------------------------------------------------------------------
# _ensure_webclient_dist
# ---------------------------------------------------------------------------


def test_ensure_webclient_dist_rebuilds_when_vendor_missing(tmp_path):
    project_dir = tmp_path / "project"
    webclient_dir = project_dir / "external/webclient"
    dist_dir = webclient_dir / "dist"
    dist_dir.mkdir(parents=True)
    (dist_dir / "index.php").write_text("<?php")

    commands: list[list[str]] = []

    class FakeRunner:
        def run(self, cmd, check=True):  # noqa: ANN001, ARG002
            commands.append(list(cmd))
            for value in cmd:
                if isinstance(value, str) and value.endswith(":/output:Z"):
                    output_dir = value.split(":/output:Z")[0]
                    vendor_dir = Path(output_dir) / "vendor"
                    vendor_dir.mkdir(parents=True, exist_ok=True)
                    (Path(output_dir) / "index.php").write_text("<?php")
                    (vendor_dir / "autoload.php").write_text("<?php")
                    break

            return SimpleNamespace(returncode=0)

    _ensure_webclient_dist(project_dir, FakeRunner())

    joined_commands = [" ".join(command) for command in commands]
    assert len(commands) == 2
    assert any("composer install" in command for command in joined_commands)
    assert any("npx ng build --configuration=visp.dev" in command for command in joined_commands)
    assert not any("visp-local-build" in command for command in joined_commands)
    assert (dist_dir / "vendor/autoload.php").exists()


def test_ensure_webclient_dist_skips_when_runtime_files_exist(tmp_path):
    project_dir = tmp_path / "project"
    dist_dir = project_dir / "external/webclient/dist"
    (dist_dir / "vendor").mkdir(parents=True)
    (dist_dir / "index.php").write_text("<?php")
    (dist_dir / "vendor/autoload.php").write_text("<?php")

    class FakeRunner:
        def run(self, cmd, check=True):  # noqa: ANN001, ARG002
            raise AssertionError("webclient build should not run")

    _ensure_webclient_dist(project_dir, FakeRunner())


# ---------------------------------------------------------------------------
# install_quadlets
# ---------------------------------------------------------------------------

SERVICES = [
    Service("mongo", "container", "mongo.container"),
    Service("apache", "container", "apache.container"),
]


def test_install_quadlets_installs_files(tmp_path):
    src = tmp_path / "quadlets"
    dst = tmp_path / "systemd"
    src.mkdir()
    dst.mkdir()
    (src / "mongo.container").write_text("[Container]\nImage=mongo\n")
    (src / "apache.container").write_text("[Container]\nImage=apache\n")

    installed, skipped, errors = install_quadlets(src, dst, SERVICES, lambda c: c, force=False)

    assert "mongo.container" in installed
    assert "apache.container" in installed
    assert skipped == []
    assert errors == []
    assert (dst / "mongo.container").read_text() == "[Container]\nImage=mongo\n"


def test_install_quadlets_skips_already_installed_without_force(tmp_path):
    src = tmp_path / "quadlets"
    dst = tmp_path / "systemd"
    src.mkdir()
    dst.mkdir()
    (src / "mongo.container").write_text("[Container]\nImage=mongo\n")
    (dst / "mongo.container").write_text("old content")

    installed, skipped, errors = install_quadlets(src, dst, SERVICES, lambda c: c, force=False)

    assert "mongo.container" in skipped
    assert (dst / "mongo.container").read_text() == "old content"


def test_install_quadlets_force_overwrites(tmp_path):
    src = tmp_path / "quadlets"
    dst = tmp_path / "systemd"
    src.mkdir()
    dst.mkdir()
    (src / "mongo.container").write_text("new content")
    (dst / "mongo.container").write_text("old content")

    installed, skipped, errors = install_quadlets(src, dst, SERVICES, lambda c: c, force=True)

    assert "mongo.container" in installed
    assert (dst / "mongo.container").read_text() == "new content"


def test_install_quadlets_applies_render_fn(tmp_path):
    src = tmp_path / "quadlets"
    dst = tmp_path / "systemd"
    src.mkdir()
    dst.mkdir()
    (src / "mongo.container").write_text("path=@@PROJECT_DIR@@")

    install_quadlets(
        src,
        dst,
        [Service("mongo", "container", "mongo.container")],
        lambda c: c.replace("@@PROJECT_DIR@@", "/real/path"),
        force=False,
    )

    assert (dst / "mongo.container").read_text() == "path=/real/path"


def test_install_quadlets_only_processes_services_present_in_src(tmp_path):
    src = tmp_path / "quadlets"
    dst = tmp_path / "systemd"
    src.mkdir()
    dst.mkdir()
    # Only mongo.container exists in src; apache.container does not
    (src / "mongo.container").write_text("[Container]\n")

    installed, skipped, errors = install_quadlets(src, dst, SERVICES, lambda c: c, force=False)

    assert "mongo.container" in installed
    # apache is silently skipped (not present in src)
    assert "apache.container" not in installed
    assert "apache.container" not in errors


# ---------------------------------------------------------------------------
# cleanup_disabled_optional_services
# ---------------------------------------------------------------------------


def test_cleanup_removes_disabled_optional(tmp_path):
    systemd_dir = tmp_path / "systemd"
    systemd_dir.mkdir()
    (systemd_dir / "whisperx.container").write_text("")

    services = [Service("whisperx", "container", "whisperx.container")]
    disabled = {"whisperx": "WHISPERX_ENABLED"}

    cleanup_disabled_optional_services(services, disabled, systemd_dir)

    assert not (systemd_dir / "whisperx.container").exists()


def test_cleanup_leaves_enabled_services(tmp_path):
    systemd_dir = tmp_path / "systemd"
    systemd_dir.mkdir()
    (systemd_dir / "mongo.container").write_text("")

    services = [Service("mongo", "container", "mongo.container")]
    disabled = {}  # mongo is not disabled

    cleanup_disabled_optional_services(services, disabled, systemd_dir)

    assert (systemd_dir / "mongo.container").exists()


def test_cleanup_no_op_if_not_installed(tmp_path):
    systemd_dir = tmp_path / "systemd"
    systemd_dir.mkdir()
    # whisperx is disabled but not installed — should not raise

    services = [Service("whisperx", "container", "whisperx.container")]
    disabled = {"whisperx": "WHISPERX_ENABLED"}

    cleanup_disabled_optional_services(services, disabled, systemd_dir)  # no error
