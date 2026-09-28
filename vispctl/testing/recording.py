"""End-to-end test of the online recording pipeline, and of uploads to the same sessions.

Logs in, creates a recording session, records it with a re-take and a
mid-session reload, and checks that the latest takes reach the project's
EMU-DB. Then uploads files to the recorded session and to a new session, and
checks that uploads and recordings don't disturb each other. The sessions it
creates are deleted afterwards unless --keep is given. Dev mode only.
"""

from __future__ import annotations

import argparse

from ..config import get_config
from ..quadlets import get_current_mode
from ..runner import Colors, color
from .e2e import read_base_domain, run_e2e

NAME = "recording"
HELP = "Record and upload into a project's sessions in a headless browser (dev mode)"
SCRIPT = "recording.mjs"


def add_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--project", default="Test 3", help="Project to create the test sessions in")
    parser.add_argument("--keep", action="store_true", help="Keep the sessions the test creates")


def build_env(base_domain: str, project: str, keep: bool) -> dict[str, str]:
    return {
        "VISP_URL": f"https://{base_domain}",
        "VISP_PROJECT": project,
        "VISP_KEEP": "1" if keep else "0",
    }


def run(args: argparse.Namespace) -> int:
    cfg = get_config()
    base_domain = read_base_domain(cfg.project_dir)
    if not base_domain:
        print(color("BASE_DOMAIN is not set in .env", Colors.RED))
        return 1
    env = build_env(base_domain, args.project, args.keep)
    return run_e2e(cfg.runner, cfg.project_dir, get_current_mode(), NAME, SCRIPT, env)
