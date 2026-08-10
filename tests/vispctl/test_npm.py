"""Tests for vispctl.npm: containerized npm for dev source-mounted services."""

from pathlib import Path

from vispctl.npm import NPM_SERVICES, ensure_node_modules, run_npm


class FakeResult:
    def __init__(self, returncode: int = 0) -> None:
        self.returncode = returncode


class FakeRunner:
    """Records commands; image existence and npm exit code are configurable."""

    def __init__(self, image_exists: bool = True, npm_rc: int = 0) -> None:
        self.image_exists = image_exists
        self.npm_rc = npm_rc
        self.commands: list[list[str]] = []

    def run_quiet(self, cmd):
        self.commands.append(cmd)
        return (0 if self.image_exists else 1), "", ""

    def run(self, cmd, capture=False, check=True, **kwargs):  # noqa: ARG002
        self.commands.append(cmd)
        return FakeResult(self.npm_rc)


def _project(tmp_path: Path, *, node_modules: bool = False, lockfile: bool = False) -> Path:
    source = tmp_path / "external" / "session-manager"
    source.mkdir(parents=True)
    if node_modules:
        (source / "node_modules").mkdir()
    if lockfile:
        (source / "package-lock.json").write_text("{}")
    return tmp_path


# ── run_npm ────────────────────────────────────────────────────────────────────


def test_run_npm_mounts_source_rw_and_runs_in_image(tmp_path: Path) -> None:
    project = _project(tmp_path)
    runner = FakeRunner()

    assert run_npm(runner, project, "session-manager", ["install", "foo"]) == 0

    podman_run = runner.commands[-1]
    assert podman_run[:3] == ["podman", "run", "--rm"]
    assert podman_run[-3:] == ["npm", "install", "foo"]
    # The source tree must be writable — npm writes node_modules and package.json.
    assert f"{project / 'external/session-manager'}:/session-manager:rw,z" in podman_run
    assert "-w" in podman_run and "/session-manager" in podman_run


def test_run_npm_rejects_unknown_service(tmp_path: Path) -> None:
    runner = FakeRunner()
    assert run_npm(runner, tmp_path, "apache", ["install"]) == 1
    assert runner.commands == []


def test_run_npm_fails_when_image_missing(tmp_path: Path) -> None:
    project = _project(tmp_path)
    runner = FakeRunner(image_exists=False)

    assert run_npm(runner, project, "session-manager", ["install"]) == 1
    # Only the existence probe ran — no container was started.
    assert all("run" not in cmd for cmd in runner.commands)


def test_run_npm_fails_when_source_missing(tmp_path: Path) -> None:
    runner = FakeRunner()
    assert run_npm(runner, tmp_path, "session-manager", ["install"]) == 1
    assert runner.commands == []


def test_run_npm_propagates_npm_exit_code(tmp_path: Path) -> None:
    project = _project(tmp_path)
    runner = FakeRunner(npm_rc=7)
    assert run_npm(runner, project, "session-manager", ["install", "nope"]) == 7


def test_run_npm_defaults_to_install(tmp_path: Path) -> None:
    project = _project(tmp_path)
    runner = FakeRunner()

    assert run_npm(runner, project, "session-manager", []) == 0
    assert runner.commands[-1][-1] == "install"


# ── ensure_node_modules ────────────────────────────────────────────────────────


def test_ensure_node_modules_noop_when_present(tmp_path: Path) -> None:
    project = _project(tmp_path, node_modules=True)
    runner = FakeRunner()

    assert ensure_node_modules(runner, project, "session-manager") is True
    assert runner.commands == []


def test_ensure_node_modules_uses_ci_when_lockfile_present(tmp_path: Path) -> None:
    project = _project(tmp_path, lockfile=True)
    runner = FakeRunner()

    assert ensure_node_modules(runner, project, "session-manager") is True
    assert runner.commands[-1][-1] == "ci"


def test_ensure_node_modules_falls_back_to_install_without_lockfile(tmp_path: Path) -> None:
    project = _project(tmp_path)
    runner = FakeRunner()

    assert ensure_node_modules(runner, project, "session-manager") is True
    assert runner.commands[-1][-1] == "install"


def test_ensure_node_modules_is_best_effort_when_image_not_built(tmp_path: Path) -> None:
    """A fresh clone installs before building — this must warn, not fail the install."""
    project = _project(tmp_path)
    runner = FakeRunner(image_exists=False)

    assert ensure_node_modules(runner, project, "session-manager") is False


def test_ensure_node_modules_skips_missing_source(tmp_path: Path) -> None:
    runner = FakeRunner()
    assert ensure_node_modules(runner, tmp_path, "session-manager") is False
    assert runner.commands == []


# ── registry ───────────────────────────────────────────────────────────────────


def test_npm_services_target_the_session_manager_source_tree() -> None:
    rel_source, image, workdir = NPM_SERVICES["session-manager"]
    assert rel_source == "external/session-manager"
    assert image == "localhost/visp-session-manager:latest"
    # Must match the dev quadlet mount destination, or npm writes to the wrong tree.
    assert workdir == "/session-manager"
