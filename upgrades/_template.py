"""VISP x.y.z — one line on what this release changes (shown in the upgrade plan).

Upgrade module for release x.y.z. './visp.py release new x.y.z --steps' copies
this template to upgrades/x.y.z.py. A release that needs no special work needs
no module at all: './visp.py upgrade' always checks out the pinned components,
rebuilds the images, pulls the third-party images and converges.

Write here whatever has to happen on a server going to this release — anything
Python can do, plus manual work only the operator can do. See vispctl/upgrade.py
for the order the phases run in. The rules that keep an upgrade safe:

- 'preflight' steps only look. Every pending release's preflight runs before
  anything changes, and in '--dry-run'.
- Every other step may be run again after it failed halfway (the operator fixes
  the cause and reruns 'upgrade', which resumes at the failed step). Prefer
  checking the actual state over assuming it, and give a step a done= check
  when the work may already have been done.
- A server may skip releases: going 1.0.0 → 1.3.0 runs the steps of 1.1.0,
  1.2.0 and 1.3.0, but builds and converges only once, with 1.3.0's images and
  quadlets. So a step must not count on an intermediate release's images being
  installed. If it has to (a database that only upgrades one major version at a
  time is the usual case — run the intermediate version in a throwaway container
  instead), or the release cannot be reached from too old a version, set
  MIN_FROM and the operator upgrades in two hops.
- Steps get ctx (vispctl.upgrade.UpgradeContext):
    ctx.mode, ctx.project_dir, ctx.from_version, ctx.to_version, ctx.env (.env + secrets)
    ctx.run([...]), ctx.podman(...), ctx.exec(container, ...), ctx.is_running(service)
    ctx.stop(...), ctx.start(...), ctx.restart(...), ctx.backup_mongo(label)
    ctx.info / ctx.warn / ctx.confirm(question) / ctx.fail(message)
    ctx.data — a dict saved with the upgrade's progress, to hand values to a later step
"""

from vispctl.upgrade import Step

# Optional: one line for the plan; defaults to the first line of the docstring.
# SUMMARY = "…"

# Optional: the oldest installed version that may upgrade straight to this release.
MIN_FROM = None


def _check_example(ctx):
    # Read-only: refuse early, before anything has changed.
    if ctx.mode == "prod" and not ctx.env.get("BASE_DOMAIN"):
        ctx.fail("BASE_DOMAIN is not set in .env")


def _migrate_example(ctx):
    ctx.info("doing the work")
    # e.g. ctx.exec("mongo", "mongosh", "--quiet", "--eval", "…")


# Examples — not run (only STEPS is). Copy what you need into STEPS and delete the rest.
EXAMPLE_STEPS = [
    Step("check-example", "Example preflight check", run=_check_example, phase="preflight"),
    Step("migrate-example", "Example migration while the old version still runs", run=_migrate_example),
    Step(
        "manual-example",
        "Example task for the operator",
        phase="post_apply",
        manual="""
Log in to https://<BASE_DOMAIN>/ and check that a project opens.
""",
    ),
]

STEPS: list[Step] = []
