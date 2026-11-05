"""VISP 1.1.0 — security release: patched images, MongoDB 6.0 → 7.0.

MongoDB moves from 6.0 to 7.0. Its data directory has to be at
featureCompatibilityVersion (FCV) 6.0 before a 7.0 binary opens it, and is
raised to 7.0 afterwards. The FCV is raised in a throwaway mongo:7.0 container
after the new images are built and before the new quadlets start, so it does
not depend on which mongo image the target release ships — a server going
straight to a later release (say with MongoDB 8.0) still passes through 7.0.

MongoDB 8.0 is not in this release: 8.0.x refuses to start on Linux 6.19 and
newer (SERVER-121912), which up-to-date development machines already run.

Downtime: from the 'mongo-7.0' step until the converge has started everything
again — the services that use MongoDB are stopped for the FCV change.
"""

from vispctl import mongo_upgrade
from vispctl.upgrade import Step

MONGO_7 = "docker.io/library/mongo:7.0.45"

# Everything that talks to MongoDB, stopped while its data directory changes.
MONGO_USERS = ["apache", "session-manager", "wsrng-server", "emu-webapp-server"]


def _data_dir(ctx):
    return ctx.project_dir / "mounts" / "mongo" / "data"


def _check_mongo(ctx):
    if not ctx.is_running("mongo"):
        ctx.fail("mongo is not running; start it (./visp.py start mongo) so its version can be checked")
    version, fcv = mongo_upgrade.live_versions()
    ctx.info(f"MongoDB {version}, featureCompatibilityVersion {fcv}")
    if mongo_upgrade.major(fcv) >= (7, 0):
        ctx.info("already at FCV 7.0 or later — the MongoDB steps will be skipped")
    elif mongo_upgrade.major(version) != (6, 0):
        ctx.fail(f"expected MongoDB 6.0 (the 1.0.0 image), found {version}; upgrade it to 6.0 by hand first")
    if not _data_dir(ctx).is_dir():
        ctx.fail(f"{_data_dir(ctx)} not found")


def _live_fcv_at_least(ctx, wanted):
    if ctx.data.get("mongo_fcv") and mongo_upgrade.major(ctx.data["mongo_fcv"]) >= wanted:
        return True
    if not ctx.is_running("mongo"):
        return False
    return mongo_upgrade.major(mongo_upgrade.live_versions()[1]) >= wanted


def _fcv_6(ctx):
    # A database that came up from 5.0 may still be at FCV 5.0.
    mongo_upgrade.set_live_fcv("6.0")
    ctx.info("featureCompatibilityVersion is 6.0")


def _mongo_7(ctx):
    users = MONGO_USERS + (["mongo-express"] if ctx.mode == "dev" else [])
    ctx.info(f"stopping {', '.join(users)} and mongo")
    ctx.stop(*users, "mongo")
    fcv = mongo_upgrade.hop(ctx, MONGO_7, _data_dir(ctx))
    ctx.info(f"data directory is at FCV {fcv}; the converge starts mongo 7.0 and everything else")


def _matomo_update(ctx):
    # Matomo migrates its database only when asked; harmless when there is nothing to do.
    ctx.exec("matomo", "php", "/var/www/html/console", "core:update", "--yes", "--no-interaction")


STEPS = [
    Step("check-mongo", "MongoDB is 6.0 and running", run=_check_mongo, phase="preflight"),
    Step(
        "mongo-fcv-6.0",
        "set MongoDB's featureCompatibilityVersion to 6.0",
        run=_fcv_6,
        done=lambda ctx: _live_fcv_at_least(ctx, (6, 0)),
    ),
    Step(
        "mongo-7.0",
        "stop the MongoDB users, raise the data directory to FCV 7.0 in a throwaway mongo:7.0",
        run=_mongo_7,
        phase="post_build",
        done=lambda ctx: _live_fcv_at_least(ctx, (7, 0)),
    ),
    Step(
        "matomo-core-update",
        "let Matomo migrate its database (core:update)",
        run=_matomo_update,
        phase="post_apply",
        done=lambda ctx: not ctx.is_running("matomo"),
    ),
]
