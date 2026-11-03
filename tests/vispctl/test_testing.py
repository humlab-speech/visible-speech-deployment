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


def test_every_registered_test_has_a_script_and_the_cli_dispatches_to_it():
    # Round-2 BROKEN: module.NAME==name and callable(args.func) were true by
    # construction. Now parse through the REAL registration and invoke the
    # resolved func against a stubbed module.run - deleting the func wiring
    # (leaving the help-lambda) makes this red instead of vacuous.
    parser = argparse.ArgumentParser()
    add_test_parser(parser.add_subparsers())

    for name, module in TESTS.items():
        if hasattr(module, "SCRIPT"):
            assert (Path(__file__).parents[1] / "e2e" / module.SCRIPT).is_file()
        args = parser.parse_args(["test", name])
        called = []
        original = module.run
        module.run = lambda a, _m=module: called.append(_m.NAME) or 0
        try:
            args.func(args)
        finally:
            module.run = original
        assert called == [name]


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


# ── R2 pins: exit-code contract and visp.py wiring ───────────────────────────


class _RcRunner:
    def __init__(self, rc):
        self.rc = rc

    def run(self, cmd, check=False):
        rc = self.rc

        class R:
            returncode = rc

        return R()


def test_run_e2e_propagates_the_browser_test_exit_code(tmp_path):
    # rc 3 out of the container must reach the CLI; 'return 0' kept 5/5 green.
    rc = run_e2e(_RcRunner(3), tmp_path, "dev", "recording", "recording.mjs", {"VISP_URL": "x"})
    assert rc == 3


def test_run_e2e_success_path_returns_zero(tmp_path):
    rc = run_e2e(_RcRunner(0), tmp_path, "dev", "recording", "recording.mjs", {"VISP_URL": "x"})
    assert rc == 0


def test_visp_cli_wires_the_test_subcommands():
    # Deleting add_test_parser(subparsers) from visp.py kept the old suite
    # green because every test built its own parser (R2 finding).
    import subprocess
    import sys

    root = Path(__file__).parents[2]
    r = subprocess.run(
        [sys.executable, str(root / "visp.py"), "test", "--help"],
        capture_output=True,
        text=True,
        cwd=root,
    )
    assert r.returncode == 0
    assert "recording" in r.stdout
