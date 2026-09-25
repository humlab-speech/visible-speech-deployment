from pathlib import Path

from vispctl.smoketest import PLAYWRIGHT_IMAGE, build_smoketest_command, run_smoketest


class FakeRunner:
    def __init__(self):
        self.commands = []

    def run(self, cmd, check=False):
        self.commands.append(cmd)

        class R:
            returncode = 0

        return R()


def test_refuses_to_run_outside_dev_mode():
    runner = FakeRunner()
    rc = run_smoketest(runner, Path("/srv/visp"), "prod", "visp.example.org")
    assert rc == 1
    assert runner.commands == []


def test_command_targets_base_domain_and_mounts_repositories_read_only():
    cmd = build_smoketest_command(Path("/srv/visp"), "recording", "visp.local", "Test 3", keep=False)
    assert cmd[:3] == ["podman", "run", "--rm"]
    assert PLAYWRIGHT_IMAGE in cmd
    assert "VISP_URL=https://visp.local" in cmd
    assert "VISP_PROJECT=Test 3" in cmd
    assert "VISP_KEEP=0" in cmd
    assert "/srv/visp/mounts/repositories:/repositories:ro" in cmd
    # Never relabel the shared repositories tree.
    assert not any(arg.endswith(":z") or arg.endswith(":Z") for arg in cmd)


def test_package_json_pins_the_image_playwright_version():
    import json

    package = json.loads((Path(__file__).parents[1] / "e2e" / "package.json").read_text())
    version = package["dependencies"]["playwright"]
    assert PLAYWRIGHT_IMAGE.endswith(f":v{version}-noble")
