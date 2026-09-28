import argparse
import json
from pathlib import Path

from vispctl.testing import TESTS, add_test_parser, recording
from vispctl.testing.e2e import PLAYWRIGHT_IMAGE, build_e2e_command, run_e2e


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
    rc = run_e2e(runner, Path("/srv/visp"), "prod", "recording", "recording.mjs", {})
    assert rc == 1
    assert runner.commands == []


def test_recording_command_targets_base_domain_and_mounts_repositories_read_only():
    env = recording.build_env("visp.local", "Test 3", keep=False)
    cmd = build_e2e_command(Path("/srv/visp"), recording.SCRIPT, env)
    assert cmd[:3] == ["podman", "run", "--rm"]
    assert PLAYWRIGHT_IMAGE in cmd
    assert "VISP_URL=https://visp.local" in cmd
    assert "VISP_PROJECT=Test 3" in cmd
    assert "VISP_KEEP=0" in cmd
    assert "/srv/visp/mounts/repositories:/repositories:ro" in cmd
    assert cmd[-1].endswith("node recording.mjs")
    # Never relabel the shared repositories tree.
    assert not any(arg.endswith(":z") or arg.endswith(":Z") for arg in cmd)


def test_every_registered_test_has_a_script_and_a_subcommand():
    parser = argparse.ArgumentParser()
    add_test_parser(parser.add_subparsers())
    for name, module in TESTS.items():
        assert module.NAME == name
        if hasattr(module, "SCRIPT"):
            assert (Path(__file__).parents[1] / "e2e" / module.SCRIPT).is_file()
        args = parser.parse_args(["test", name])
        assert callable(args.func)


def test_recording_options():
    parser = argparse.ArgumentParser()
    add_test_parser(parser.add_subparsers())
    args = parser.parse_args(["test", "recording", "--project", "Test 4", "--keep"])
    assert args.project == "Test 4"
    assert args.keep is True


def test_package_json_pins_the_image_playwright_version():
    package = json.loads((Path(__file__).parents[1] / "e2e" / "package.json").read_text())
    version = package["dependencies"]["playwright"]
    assert PLAYWRIGHT_IMAGE.endswith(f":v{version}-noble")
