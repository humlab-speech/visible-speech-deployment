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
    bm = BuildManager(
        runner, build_configs={}, node_configs={"container-agent": {"source": "no"}}
    )

    success = bm.build_node_project("container-agent", config, no_cache=True)
    assert success is True
    assert runner.last_cmd is not None
    assert runner.last_cmd[0] == "podman"
    assert "node:fake" in runner.last_cmd
