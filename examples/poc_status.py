#!/usr/bin/env python3
"""PoC script showing how to use vispctl Runner + ServiceManager."""

from vispctl.runner import Runner
from vispctl.service import load_default_services
from vispctl.service_manager import ServiceManager


def main():
    runner = Runner()
    services = load_default_services()
    mgr = ServiceManager(runner, services)
    mgr.status()


if __name__ == "__main__":
    main()
