from pathlib import Path

from vispctl.build import BuildManager


class FakeRunner:
    def __init__(self):
        self.last_cmd = None

    def run(self, cmd, check=True):
        # Record command
        self.last_cmd = cmd
        # If this is a containerized node build, try to detect output mount and create a fake output file
        if isinstance(cmd, list) and "podman" in cmd and "run" in cmd:
            # Find host path before ':/output'
            for i, c in enumerate(cmd):
                if isinstance(c, str) and c.endswith(":/output:Z"):
                    host_path = c.split(":/output:Z")[0]
                    # Create a fake verify file
                    Path(host_path).mkdir(parents=True, exist_ok=True)
                    (Path(host_path) / "main.js").write_text("// built")
                    break

        class R:
            def __init__(self):
                self.returncode = 0

        return R()


def test_prepare_build_context_missing_source(tmp_path):
    runner = FakeRunner()
    bm = BuildManager(runner, build_configs={}, node_configs={})
    cfg = {"context": "does/not/exist", "prepare_context": "container-agent"}
    ok = bm.prepare_build_context("operations-session", cfg)
    assert ok is False


def test_build_node_project_invokes_podman_run(tmp_path):
    # Create fake source and output dirs
    source = tmp_path / "src"
    output = tmp_path / "out"
    source.mkdir()
    (source / "package.json").write_text("{}")

    config = {
        "source": str(source.resolve()),
        "output": str(output.resolve()),
        "description": "test",
        "build_cmd": "npm run build",
        "verify_file": "main.js",
        "container_image": "node:fake",
    }

    runner = FakeRunner()
    bm = BuildManager(runner, build_configs={}, node_configs={"container-agent": {"source": "no"}})

    success = bm.build_node_project("container-agent", config, no_cache=True)
    assert success is True
    assert runner.last_cmd is not None
    assert runner.last_cmd[0] == "podman"
    assert "node:fake" in runner.last_cmd


def test_build_node_project_runs_pre_build(tmp_path):
    """When a config has pre_build_cmd, it should run a separate container first."""
    source = tmp_path / "src"
    output = tmp_path / "out"
    source.mkdir()
    (source / "package.json").write_text("{}")

    # Track all commands
    commands = []

    class MultiRunner:
        def __init__(self):
            self.last_cmd = None

        def run(self, cmd, check=True):
            self.last_cmd = cmd
            commands.append(list(cmd))
            # Create fake output for node build
            if isinstance(cmd, list) and "podman" in cmd and "run" in cmd:
                for c in cmd:
                    if isinstance(c, str) and c.endswith(":/output:Z"):
                        host_path = c.split(":/output:Z")[0]
                        Path(host_path).mkdir(parents=True, exist_ok=True)
                        (Path(host_path) / "index.php").write_text("<?php")
                        break

            class R:
                returncode = 0

            return R()

    runner = MultiRunner()
    bm = BuildManager(runner, build_configs={}, node_configs={})

    config = {
        "source": str(source.resolve()),
        "output": str(output.resolve()),
        "description": "test webclient",
        "build_cmd": "npx ng build",
        "verify_file": "index.php",
        "container_image": "node:22",
        "pre_build_cmd": "composer install --no-dev",
        "pre_build_image": "docker.io/library/composer:2",
    }

    success = bm.build_node_project("webclient", config)
    assert success is True
    # Should have run two podman commands: pre-build then main build
    assert len(commands) == 2
    # First command is the pre-build (composer)
    assert "docker.io/library/composer:2" in commands[0]
    assert "composer install --no-dev" in " ".join(commands[0])
    # Second command is the main Node build
    assert "node:22" in commands[1]


def test_build_node_project_fails_on_pre_build_error(tmp_path):
    """If the pre-build step fails, the main build should not run."""
    source = tmp_path / "src"
    output = tmp_path / "out"
    source.mkdir()
    (source / "package.json").write_text("{}")

    call_count = [0]

    class FailFirstRunner:
        def __init__(self):
            self.last_cmd = None

        def run(self, cmd, check=True):
            self.last_cmd = cmd
            call_count[0] += 1

            class R:
                # Fail the first call (pre-build), succeed the rest
                returncode = 1 if call_count[0] == 1 else 0

            return R()

    runner = FailFirstRunner()
    bm = BuildManager(runner, build_configs={}, node_configs={})

    config = {
        "source": str(source.resolve()),
        "output": str(output.resolve()),
        "description": "test",
        "build_cmd": "npm run build",
        "verify_file": "index.php",
        "pre_build_cmd": "composer install",
        "pre_build_image": "docker.io/library/composer:2",
    }

    success = bm.build_node_project("webclient", config)
    assert success is False
    # Only the pre-build should have run; main build should not start
    assert call_count[0] == 1


def test_build_node_project_skips_pre_build_when_not_configured(tmp_path):
    """Without pre_build_cmd, no pre-build container should run."""
    source = tmp_path / "src"
    output = tmp_path / "out"
    source.mkdir()
    (source / "package.json").write_text("{}")

    commands = []

    class TrackingRunner:
        def __init__(self):
            self.last_cmd = None

        def run(self, cmd, check=True):
            self.last_cmd = cmd
            commands.append(list(cmd))
            if isinstance(cmd, list) and "podman" in cmd:
                for c in cmd:
                    if isinstance(c, str) and c.endswith(":/output:Z"):
                        host_path = c.split(":/output:Z")[0]
                        Path(host_path).mkdir(parents=True, exist_ok=True)
                        (Path(host_path) / "main.js").write_text("// built")
                        break

            class R:
                returncode = 0

            return R()

    runner = TrackingRunner()
    bm = BuildManager(runner, build_configs={}, node_configs={})

    config = {
        "source": str(source.resolve()),
        "output": str(output.resolve()),
        "description": "test",
        "build_cmd": "npm run build",
        "verify_file": "main.js",
    }

    success = bm.build_node_project("agent", config)
    assert success is True
    # Only one command (the main build), no pre-build
    assert len(commands) == 1
