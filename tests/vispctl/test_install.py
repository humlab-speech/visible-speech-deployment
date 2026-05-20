"""Tests for vispctl/install.py."""

from vispctl.install import (
    cleanup_disabled_optional_services,
    fix_writable_permissions,
    generate_tracker_config,
    install_quadlets,
    scaffold_directories,
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
