"""Automated tests of a running VISP installation: ``./visp.py test <name>``.

Each test is a module in this package that defines:

- ``NAME``: the name it runs under (``./visp.py test <NAME>``)
- ``HELP``: a one-line description for ``./visp.py test --help``
- ``add_arguments(parser)``: its own command-line options
- ``run(args) -> int``: runs it and returns an exit code

and is listed in ``TESTS`` below. Browser tests share the Playwright runner in
``e2e.py``.
"""

from __future__ import annotations

import argparse
import sys

from . import recording

TESTS = {module.NAME: module for module in (recording,)}


def add_test_parser(subparsers) -> None:
    """Register ``test`` and one subcommand per test on visp.py's parser."""
    p_test = subparsers.add_parser("test", help="Run automated tests against the running system")
    p_test.set_defaults(func=lambda args: p_test.print_help())
    tests = p_test.add_subparsers(dest="test_name", metavar="TEST")
    for module in TESTS.values():
        p = tests.add_parser(module.NAME, help=module.HELP, description=module.__doc__)
        module.add_arguments(p)
        p.set_defaults(func=_runner(module))


def _runner(module):
    def run(args: argparse.Namespace) -> None:
        rc = module.run(args)
        if rc != 0:
            sys.exit(rc)

    return run
