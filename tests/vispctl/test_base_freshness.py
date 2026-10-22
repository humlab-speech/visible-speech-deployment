"""Tests for vispctl.base_freshness — six-state base-image verdicts for built images.

Hermetic by construction: fake runners answer ``podman images --format json``,
fake registry clients answer ``tag_digest``, and every Dockerfile is synthetic
under ``tmp_path``. No podman, no network. Idioms follow test_images.py.

The module under test is imported through the ``bf`` fixture rather than at the
top of the file, so this file collects while the module is still in flight.
"""

from __future__ import annotations

import dataclasses
import json
import re
from pathlib import Path

import pytest

from vispctl.images import ImageManager
from vispctl.registry import RegistryError

_ANSI = re.compile(r"\x1b\[[0-9;]*m")

NODE = "node:24.16.0-alpine3.22"
NODE_KEY = f"library/{NODE}"  # repository:tag as ImageRef.parse sees it
DIG_A = "sha256:191c9f00"
DIG_B = "sha256:8106d433"
DIG_MOVED = "sha256:2abea424"

BUILT_WSRNG = {"Id": "built1", "Names": ["localhost/visp-wsrng-server:latest"], "RepoDigests": []}
BUILT_TRATT = {"Id": "built2", "Names": ["localhost/visp-tratt:latest"], "RepoDigests": []}
LOCAL_NODE = {
    "Id": "base1",
    "Names": [f"docker.io/library/{NODE}"],
    "RepoDigests": [f"docker.io/library/node@{DIG_A}", f"docker.io/library/node@{DIG_B}"],
}

CFG_WSRNG = {"context": "./docker/wsrng", "dockerfile": "Dockerfile", "image": "visp-wsrng-server"}
CFG_TRATT = {"context": "./docker/tratt", "dockerfile": "Dockerfile", "image": "visp-tratt"}
CFG_APACHE = {"context": "./docker/apache", "dockerfile": "Dockerfile", "image": "visp-apache"}


@pytest.fixture
def bf():
    """vispctl.base_freshness, imported lazily (see module docstring)."""
    from vispctl import base_freshness

    return base_freshness


def _plain(text: str) -> str:
    """Strip colour codes: display assertions are about wording, never about colours."""
    return _ANSI.sub("", text)


def _write(root, rel: str, text: str):
    path = root.joinpath(*rel.split("/"))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


def _cfg(context: str, dockerfile: str, target: str | None = None) -> dict:
    cfg = {"context": context, "dockerfile": dockerfile, "image": "visp-svc"}
    if target:
        cfg["target"] = target
    return cfg


def _item(bf, **kw):
    """Build a BaseFreshness, deriving snapshot whether the module stores it as a field or a property."""
    args = {
        "service": "svc",
        "ref": NODE,
        "role": "runtime",
        "stage_name": None,
        "built_image_id": "built1",
        "recorded_digests": frozenset(),
        "local_digests": frozenset(),
        "remote_digest": None,
        "remote_error": None,
        "checked_at": None,
        "check_source": "none",
    }
    args.update(kw)
    if any(f.name == "snapshot" for f in dataclasses.fields(bf.BaseFreshness)):
        args["snapshot"] = bf.is_snapshot_ref(args["ref"])
    return bf.BaseFreshness(**args)


class _PodmanImagesRunner:
    """Fake runner answering `podman images --format json` and nothing else."""

    def __init__(self, images):
        self.images = images
        self.calls = []

    def run_quiet(self, cmd):
        self.calls.append(list(cmd))
        if cmd[:3] == ["podman", "images", "--format"]:
            return 0, json.dumps(self.images), ""
        return 1, "", ""


class _FakeClient:
    """Registry stand-in: answers tag_digest for 'repository:tag' keys, records what was asked."""

    def __init__(self, digests):
        self.digests = digests  # value: digest string, or an exception to raise
        self.seen: list[str] = []

    def tag_digest(self, ref):
        key = f"{ref.repository}:{ref.tag}"
        self.seen.append(key)
        value = self.digests[key]  # KeyError if the ref should never have been looked up
        if isinstance(value, Exception):
            raise value
        return value

    def list_tags(self, ref):
        raise AssertionError("base freshness only needs the tag digest")


class _NoClient:
    def __getattr__(self, name):
        raise AssertionError("registry must not be contacted when check_remote=False")


def _seed_lock(bf, tmp_path, *, built_id="built1", ref=NODE, digests=(), checks=None) -> dict:
    """Write a base-lock.json describing one built image (and optionally cached checks)."""
    bases = {ref: {"stage": "runtime", "digests": list(digests), "id": "base1"}} if digests else {}
    lock = {
        "images": {
            built_id: {
                "built_at": "2026-10-22T06:00:00+00:00",
                "pull_policy": "newer",
                "bases": bases,
            }
        },
        "checks": checks or {},
    }
    bf.save_lock(tmp_path, lock)
    return lock


# ── 1. Six-state verdict machine ───────────────────────────────────────────────


def test_state_fresh_when_registry_still_serves_a_recorded_digest(bf):
    """Set membership, not equality: the registry digest may be the second RepoDigest tail."""
    item = _item(
        bf,
        check_source="live",
        recorded_digests=[DIG_A, DIG_B],
        local_digests=[DIG_A, DIG_B],
        remote_digest=DIG_B,
    )
    assert item.state == "fresh"


def test_state_moved_when_the_tag_now_serves_something_else(bf):
    item = _item(
        bf, check_source="live", recorded_digests=[DIG_A, DIG_B], local_digests=[DIG_A], remote_digest=DIG_MOVED
    )
    assert item.state == "moved"


def test_state_moved_beats_immobile_for_a_snapshot_tag(bf):
    """A snapshot tag whose recorded digest does not match is still moved, never immobile."""
    item = _item(
        bf,
        ref="debian:trixie-20260406",
        check_source="live",
        recorded_digests=[DIG_A],
        remote_digest=DIG_MOVED,
    )
    assert item.state == "moved"


def test_state_immobile_for_a_matched_snapshot_tag(bf):
    item = _item(
        bf,
        ref="debian:trixie-20260406",
        check_source="live",
        recorded_digests=[DIG_A],
        remote_digest=DIG_A,
    )
    assert item.snapshot is True
    assert item.state == "immobile"


def test_state_heuristic_without_a_lock_record_either_way(bf):
    """No lock record means we only compare the local base copy — a guess, in both directions."""
    differs = _item(bf, check_source="live", local_digests=[DIG_A], remote_digest=DIG_MOVED)
    matches = _item(bf, check_source="live", local_digests=[DIG_MOVED], remote_digest=DIG_MOVED)
    assert (differs.state, matches.state) == ("heuristic", "heuristic")


def test_state_never_checked_beats_everything(bf):
    """No check source means no verdict — even when a fetch error was captured."""
    silent = _item(bf, check_source="none")
    errored = _item(bf, check_source="none", remote_error="cannot reach registry-1.docker.io")
    assert silent.state == "never-checked"
    assert errored.state == "never-checked"


def test_state_offline_when_a_cached_check_shares_the_place_of_a_failed_fetch(bf):
    """cache + error is offline even though the digests would compare (rule ordering)."""
    item = _item(
        bf,
        ref="nginx:alpine",
        check_source="cache",
        remote_error="cannot reach registry-1.docker.io: timed out",
        recorded_digests=[DIG_A],
        remote_digest=DIG_MOVED,
        checked_at="2026-10-20T05:00:00+00:00",
    )
    assert item.state == "offline"
    assert item._digest_verdict() == "moved"  # the evidence still backs the qualified wording


def test_state_offline_for_any_cached_check_even_without_an_error(bf):
    """A cached digest records the past: no bare fresh/heuristic from cache.

    The case a stale cache and a deliberate ``--offline`` share — nothing was
    contacted today, so the verdict is never presented unqualified.
    """
    cached_fresh = _item(
        bf,
        check_source="cache",
        recorded_digests=[DIG_A],
        remote_digest=DIG_A,
        checked_at="2026-10-20T05:00:00+00:00",
    )
    cached_guess = _item(
        bf,
        check_source="cache",
        local_digests=[DIG_A],
        remote_digest=DIG_MOVED,
        checked_at="2026-10-20T05:00:00+00:00",
    )

    assert (cached_fresh.state, cached_guess.state) == ("offline", "offline")
    assert (cached_fresh._digest_verdict(), cached_guess._digest_verdict()) == ("fresh", "heuristic")


def test_remediation_is_build_then_apply_for_that_service(bf):
    """apply only exists for services that own a quadlet, and takes exactly one of them."""
    assert (
        _item(bf, service="artic").remediation({"artic", "apache"}) == "./visp.py build artic && ./visp.py apply artic"
    )


def test_remediation_drops_apply_for_a_service_without_a_quadlet(bf):
    """jupyter-session is built but has no unit: ./visp.py apply would refuse the name."""
    item = _item(bf, service="jupyter-session")

    assert item.remediation({"artic"}) == (
        "./visp.py build jupyter-session  (no quadlet to apply — picked up when the next session starts)"
    )
    assert "./visp.py apply" not in item.remediation({"artic"})
    # an unconfirmed caller gets the shape of the command, never a guessed target
    assert (
        item.remediation() == "./visp.py build jupyter-session  (then ./visp.py apply <service>, one service per run)"
    )


# ── 2. Snapshot refs ──────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "ref, expected",
    [
        ("debian:trixie-20260406", True),
        ("debian:bookworm-20260505-slim", True),
        (NODE, False),  # dotted patch pin: content still moves
        ("alpine:3.23", False),
        ("linuxserver/code-server:version-v3.11.1", False),
        ("quay.io/jupyter/datascience-notebook", False),  # no tag → latest → can move
        ("localhost:5000/team/app", False),  # ':' before the last '/' is a port, not a tag
    ],
)
def test_is_snapshot_ref(bf, ref, expected):
    assert bf.is_snapshot_ref(ref) is expected


# ── 3. Stage graph: which FROM refs a build actually consumes ─────────────────


def test_stage_bases_single_stage_normalizes_display_ref(bf, tmp_path):
    _write(tmp_path, "docker/nginx/Dockerfile", "FROM docker.io/library/nginx:1.30.0-alpine3.23\nRUN apk add curl\n")

    assert bf.stage_bases(tmp_path, _cfg(".", "./docker/nginx/Dockerfile")) == [
        ("nginx:1.30.0-alpine3.23", "runtime", None)
    ]


def test_stage_bases_multi_stage_names_build_stages_and_dedups(bf, tmp_path):
    """Build stages feeding the target (directly or transitively) are bases; stage names are not."""
    _write(
        tmp_path,
        "docker/artic/Dockerfile",
        "FROM docker.io/library/node:22-bookworm-slim AS build\n"
        "RUN npm ci\n"
        "FROM node:22-bookworm-slim AS assets\n"
        "COPY --from=build /dist /dist\n"
        "FROM docker.io/library/nginx:alpine AS production\n"
        "COPY --from=assets /dist /usr/share/nginx/html\n",
    )

    out = bf.stage_bases(tmp_path, _cfg("./docker/artic", "Dockerfile"))

    assert [(ref, role) for ref, role, _ in out] == [("node:22-bookworm-slim", "build"), ("nginx:alpine", "runtime")]
    assert out[-1][2] == "production"
    assert "build" not in [ref for ref, _, _ in out] and "assets" not in [ref for ref, _, _ in out]


def test_stage_bases_follows_a_cross_stage_from_reference(bf, tmp_path):
    """`FROM <earlier-stage>` is internal, not a base; its own base still counts as build."""
    _write(
        tmp_path,
        "docker/session-manager/jupyter-session/Dockerfile",
        "FROM docker.io/node:20.20.2-alpine3.22 AS container_agent_builder\n"
        "RUN npm ci\n"
        "FROM container_agent_builder AS agent_assets\n"
        "COPY --from=container_agent_builder /app /opt/agent\n"
        "FROM quay.io/jupyter/datascience-notebook:r-4.5.2\n"
        "COPY --from=agent_assets /opt/agent /opt/agent\n",
    )

    out = bf.stage_bases(tmp_path, _cfg("./docker/session-manager/jupyter-session", "Dockerfile"))

    assert [ref for ref, _, _ in out] == ["node:20.20.2-alpine3.22", "quay.io/jupyter/datascience-notebook:r-4.5.2"]
    assert [role for _, role, _ in out] == ["build", "runtime"]


REPO_ROOT = Path(__file__).resolve().parents[2]


def test_stage_bases_resolves_runtime_base_through_internal_from_chain(bf, tmp_path):
    """A target stage FROM'd from an earlier stage gets the registry base at the chain head."""
    _write(
        tmp_path,
        "docker/x/Dockerfile",
        "FROM docker.io/library/debian:trixie-20261020 AS base\n"
        "RUN apt-get update\n"
        "FROM base AS webclient-build\n"
        "RUN npm ci\n"
        "FROM base AS production\n"
        "COPY --from=webclient-build /out/ /var/www/html/\n",
    )

    out = bf.stage_bases(tmp_path, _cfg("./docker/x", "Dockerfile", target="production"))

    assert out == [
        ("debian:trixie-20261020", "build", "webclient-build"),
        ("debian:trixie-20261020", "runtime", "production"),
    ]


def test_stage_bases_resolves_a_two_hop_runtime_chain(bf, tmp_path):
    """final <- r_packages <- base resolves through both hops to the quay.io head."""
    _write(
        tmp_path,
        "docker/y/Dockerfile",
        "FROM quay.io/jupyter/datascience-notebook:r-4.5.2 AS base\n"
        "RUN install-r\n"
        "FROM base AS r_packages\n"
        "RUN install-more\n"
        "FROM r_packages AS final\n"
        "RUN nothing\n",
    )

    out = bf.stage_bases(tmp_path, _cfg("./docker/y", "Dockerfile"))

    assert out == [("quay.io/jupyter/datascience-notebook:r-4.5.2", "runtime", "final")]


def test_stage_bases_inherits_build_deps_of_a_from_inherited_stage(bf, tmp_path):
    """The reviewer's graph: `FROM base AS final` ships what `base` COPY --from'd.

    Following only the target's own copies lost node:22 entirely — the artifact
    enters the image in `base`, which `final` inherits, so its build base counts.
    """
    _write(
        tmp_path,
        "docker/z/Dockerfile",
        "FROM docker.io/library/node:22 AS build\n"
        "RUN true\n"
        "FROM docker.io/library/alpine:3.23 AS base\n"
        "COPY --from=build /usr/local/bin/node /usr/bin/node\n"
        "FROM base AS final\n",
    )
    # ... and two hops down the chain the dependency still counts (transitive, not adjacent)
    _write(
        tmp_path,
        "docker/z2/Dockerfile",
        "FROM docker.io/library/node:22 AS build\n"
        "RUN true\n"
        "FROM docker.io/library/alpine:3.23 AS base\n"
        "COPY --from=build /usr/local/bin/node /usr/bin/node\n"
        "FROM base AS mid\n"
        "RUN true\n"
        "FROM mid AS final\n",
    )

    for docker_dir in ("docker/z", "docker/z2"):
        assert bf.stage_bases(tmp_path, _cfg(f"./{docker_dir}", "Dockerfile")) == [
            ("node:22", "build", "build"),  # the artifact source, inherited via FROM
            ("alpine:3.23", "runtime", "final"),  # named once, as the runtime base only
        ]


def test_stage_bases_on_the_real_apache_dockerfile(bf):
    """apache production's FROM base chain must land on a debian runtime base."""
    out = bf.stage_bases(REPO_ROOT, _cfg("./docker/apache", "Dockerfile", target="production"))

    runtime = [ref for ref, role, _ in out if role == "runtime"]
    assert len(runtime) == 1 and runtime[0].startswith("debian:"), out
    assert any(role == "build" for _, role, _ in out)


def test_stage_bases_on_the_real_jupyter_session_dockerfile(bf):
    """The jupyter final stage chains back to the quay.io notebook runtime base."""
    out = bf.stage_bases(REPO_ROOT, _cfg("./docker/session-manager/jupyter-session", "Dockerfile"))

    assert ("quay.io/jupyter/datascience-notebook:r-4.5.2", "runtime") in [(ref, role) for ref, role, _ in out]
    assert ("node:20.20.2-alpine3.22", "build") in [(ref, role) for ref, role, _ in out]


def test_stage_bases_resolves_numeric_copy_from_index(bf, tmp_path):
    """COPY --from=1 is the second stage (zero-based); unreferenced stage 0 is not a base."""
    _write(
        tmp_path,
        "docker/svc/Dockerfile",
        "FROM docker.io/library/node:20-alpine AS tools\n"
        "FROM docker.io/library/node:22.22.2-bookworm AS build\n"
        "RUN npm ci\n"
        "FROM docker.io/library/alpine:3.23\n"
        "COPY --from=1 /app /app\n",
    )

    out = bf.stage_bases(tmp_path, _cfg("./docker/svc", "Dockerfile"))

    assert [(ref, role) for ref, role, _ in out] == [("node:22.22.2-bookworm", "build"), ("alpine:3.23", "runtime")]


def test_stage_bases_target_selects_the_named_stage(bf, tmp_path):
    _write(
        tmp_path,
        "docker/tratt/Dockerfile",
        "FROM docker.io/node:24.15.0 AS build\n"
        "RUN npm ci\n"
        "FROM docker.io/httpd:2.4.67\n"
        "COPY --from=build /app /usr/share/apache2/htdocs\n",
    )

    assert bf.stage_bases(tmp_path, _cfg("./docker/tratt", "Dockerfile", target="build")) == [
        ("node:24.15.0", "runtime", "build")
    ]
    # An unknown target falls back to the last stage.
    fallback = bf.stage_bases(tmp_path, _cfg("./docker/tratt", "Dockerfile", "production"))
    assert [(ref, role) for ref, role, _ in fallback] == [
        ("node:24.15.0", "build"),
        ("httpd:2.4.67", "runtime"),
    ]


def test_stage_bases_drops_refs_pinned_by_digest(bf, tmp_path):
    """A @sha256: FROM cannot move, so there is nothing to check and nothing to report."""
    _write(
        tmp_path,
        "docker/svc/Dockerfile",
        "FROM python:3.12.11-slim@sha256:00112233445566778899aabbccddeeff00112233445566778899aabbccddeeff AS build\n"
        "FROM docker.io/library/nginx:alpine\n",
    )
    assert bf.stage_bases(tmp_path, _cfg("./docker/svc", "Dockerfile")) == [("nginx:alpine", "runtime", None)]

    _write(tmp_path, "docker/pinned/Dockerfile", "FROM python:3.12.11-slim@sha256:00112233445566778899aabbccddeeff\n")
    assert bf.stage_bases(tmp_path, _cfg("./docker/pinned", "Dockerfile")) == []


def test_stage_bases_resolves_dockerfile_path_like_build_does(bf, tmp_path):
    """whisperx's Containerfile lives under the context; `./` refs are project-relative."""
    _write(tmp_path, "external/WhisperVault/container/Containerfile", "FROM python:3.12.11-slim\n")
    whisperx = {"context": "./external/WhisperVault", "dockerfile": "container/Containerfile", "image": "visp-whisperx"}
    assert bf.stage_bases(tmp_path, whisperx) == [("python:3.12.11-slim", "runtime", None)]

    # artic builds from the external context with a path that climbs back into docker/.
    _write(tmp_path, "docker/artic/Dockerfile", "FROM docker.io/library/node:22-bookworm-slim\n")
    assert bf.stage_bases(tmp_path, _cfg("./external/artic", "../../docker/artic/Dockerfile")) == [
        ("node:22-bookworm-slim", "runtime", None)
    ]

    # A leading './' is project-relative even when the context is elsewhere.
    _write(tmp_path, "docker/apache/Dockerfile", "FROM docker.io/library/debian:trixie-20260406 AS base\n")
    assert bf.stage_bases(tmp_path, _cfg("./external/webclient", "./docker/apache/Dockerfile")) == [
        ("debian:trixie-20260406", "runtime", "base")
    ]


def test_stage_bases_missing_dockerfile_is_not_an_error(bf, tmp_path):
    assert bf.stage_bases(tmp_path, _cfg(".", "./docker/ghost/Dockerfile")) == []


# ── 4. scan_base_images sees Containerfiles ────────────────────────────────────


def test_scan_base_images_includes_containerfiles(tmp_path, monkeypatch):
    """whisperx's base lives in external/**/container/Containerfile — it must be visible (README finding 1)."""
    from vispctl import images as images_mod

    _write(tmp_path, "external/WhisperVault/container/Containerfile", "FROM python:3.12.11-slim\n")
    _write(tmp_path, "docker/nginx/Dockerfile", "FROM docker.io/library/nginx:alpine\n")
    _write(tmp_path, "docker/ARCHIVE/old/Dockerfile", "FROM docker.io/library/httpd:2.4.67\n")
    _write(tmp_path, "external/app/node_modules/pkg/Dockerfile", "FROM docker.io/library/node:20-alpine\n")

    monkeypatch.setattr(images_mod, "get_config", lambda: type("C", (), {"project_dir": tmp_path})())

    base = ImageManager(runner=None).scan_base_images()

    assert base["python:3.12.11-slim"] == [("external/WhisperVault/container/Containerfile", None)]
    assert base["nginx:alpine"] == [("docker/nginx/Dockerfile", None)]
    assert "httpd:2.4.67" not in base  # ARCHIVE still excluded
    assert "node:20-alpine" not in base  # node_modules still excluded


# ── 5. Lock file ──────────────────────────────────────────────────────────────


def test_lock_round_trip_under_visp_dir(bf, tmp_path):
    lock = {
        "images": {"sha256:aa": {"built_at": "2026-10-22T06:00:00+00:00", "pull_policy": "newer", "bases": {}}},
        "checks": {NODE: {"remote_digest": DIG_A, "checked_at": "2026-10-22T06:31:00+00:00"}},
    }

    bf.save_lock(tmp_path, lock)

    assert (tmp_path / ".visp" / "base-lock.json").exists()
    assert bf.load_lock(tmp_path) == lock


def test_load_lock_degrades_to_empty(bf, tmp_path):
    assert bf.load_lock(tmp_path) == {}

    lock_file = tmp_path / ".visp" / "base-lock.json"
    lock_file.parent.mkdir(parents=True)
    lock_file.write_text("{not json")
    assert bf.load_lock(tmp_path) == {}


def test_record_build_merges_by_built_image_id(bf, tmp_path):
    """Each build merges into the saved lock (Phase 2's caller flow) — nothing else is clobbered."""
    node_base = {NODE: {"stage": "runtime", "digests": [DIG_A], "id": "base1"}}
    nginx_base = {"nginx:alpine": {"stage": "runtime", "digests": [DIG_B], "id": "base2"}}
    checks = {NODE: {"remote_digest": DIG_A, "checked_at": "2026-10-22T06:31:00+00:00"}}

    lock = bf.record_build(tmp_path, "sha256:aaa", node_base, "2026-10-22T06:00:00+00:00", "newer")
    assert not (tmp_path / ".visp" / "base-lock.json").exists()  # callers save
    bf.save_lock(tmp_path, lock)

    lock = bf.record_build(tmp_path, "sha256:bbb", nginx_base, "2026-10-22T07:00:00+00:00", "missing")
    lock["checks"] = checks
    bf.save_lock(tmp_path, lock)

    stored = bf.load_lock(tmp_path)
    assert stored["images"]["sha256:aaa"]["bases"] == node_base
    assert stored["images"]["sha256:bbb"]["pull_policy"] == "missing"

    # Rebuilding the same id replaces its bases; other images and checks survive.
    lock = bf.record_build(tmp_path, "sha256:aaa", nginx_base, "2026-11-01T06:00:00+00:00", "newer")
    assert lock["images"]["sha256:aaa"]["bases"] == nginx_base
    bf.save_lock(tmp_path, lock)

    stored = bf.load_lock(tmp_path)
    assert stored["images"]["sha256:aaa"]["bases"] == nginx_base
    assert stored["images"]["sha256:bbb"] == {
        "built_at": "2026-10-22T07:00:00+00:00",
        "pull_policy": "missing",
        "bases": nginx_base,
    }
    assert stored["checks"] == checks


# ── local_image_state ──────────────────────────────────────────────────────────


def test_local_image_state_one_call_normalized_names_and_built_tags(bf):
    runner = _PodmanImagesRunner(
        [
            {**LOCAL_NODE, "Names": [f"docker.io/library/{NODE}", "docker.io/library/node:24-alpine"]},
            BUILT_WSRNG,
            {"Id": "built3", "Names": ["localhost/visp-tratt:1.2.3"], "RepoDigests": []},
        ]
    )

    bases, built = bf.local_image_state(runner)

    assert runner.calls == [["podman", "images", "--format", "json"]]  # one podman call
    assert bases[NODE] == {"id": "base1", "digests": {DIG_A, DIG_B}}
    assert bases["node:24-alpine"]["id"] == "base1"
    assert built["localhost/visp-wsrng-server"]["id"] == "built1"
    assert "localhost/visp-tratt" not in built  # only :latest tags name our built images


def test_local_image_state_tolerates_unparsable_output(bf):
    class _Noise:
        def run_quiet(self, cmd):
            return 0, "not json at all", ""

    assert bf.local_image_state(_Noise()) == ({}, {})


# ── 6. get_base_freshness ──────────────────────────────────────────────────────


def test_get_base_freshness_fresh_when_the_recorded_digest_still_wins(bf, tmp_path):
    _write(tmp_path, "docker/wsrng/Dockerfile", f"FROM docker.io/library/{NODE}\n")
    _seed_lock(bf, tmp_path, digests=[DIG_A])
    client = _FakeClient({NODE_KEY: DIG_A})

    items = bf.get_base_freshness(
        _PodmanImagesRunner([BUILT_WSRNG, LOCAL_NODE]),
        project_dir=tmp_path,
        client=client,
        build_configs={"wsrng-server": CFG_WSRNG},
    )

    assert len(items) == 1
    (item,) = items
    assert (item.service, item.ref, item.role, item.stage_name) == ("wsrng-server", NODE, "runtime", None)
    assert item.built_image_id == "built1"
    assert item.recorded_digests == frozenset({DIG_A})
    assert item.local_digests == frozenset({DIG_A, DIG_B})
    assert item.remote_digest == DIG_A
    assert item.check_source == "live"
    assert item.state == "fresh"
    assert client.seen == [NODE_KEY]


def test_get_base_freshness_moved_when_the_recorded_digest_is_gone(bf, tmp_path):
    _write(tmp_path, "docker/wsrng/Dockerfile", f"FROM {NODE}\n")
    _seed_lock(bf, tmp_path, digests=[DIG_A, DIG_B])
    client = _FakeClient({NODE_KEY: DIG_MOVED})

    items = bf.get_base_freshness(
        _PodmanImagesRunner([BUILT_WSRNG, LOCAL_NODE]),
        project_dir=tmp_path,
        client=client,
        build_configs={"wsrng-server": CFG_WSRNG},
    )

    assert items[0].state == "moved"
    assert items[0].remote_digest == DIG_MOVED


def test_get_base_freshness_heuristic_without_a_lock_record(bf, tmp_path):
    """Pre-Phase-2 images have no record: compare the local base copy, call it a guess."""
    _write(tmp_path, "docker/wsrng/Dockerfile", f"FROM {NODE}\n")
    client = _FakeClient({NODE_KEY: DIG_MOVED})

    items = bf.get_base_freshness(
        _PodmanImagesRunner([BUILT_WSRNG, LOCAL_NODE]),
        project_dir=tmp_path,
        client=client,
        build_configs={"wsrng-server": CFG_WSRNG},
    )

    assert items[0].recorded_digests == frozenset()
    assert items[0].local_digests == frozenset({DIG_A, DIG_B})
    assert items[0].state == "heuristic"
    assert items[0].check_source == "live"


def test_get_base_freshness_never_checked_offline_without_cache(bf, tmp_path):
    _write(tmp_path, "docker/wsrng/Dockerfile", "FROM alpine:3.23\n")

    items = bf.get_base_freshness(
        _PodmanImagesRunner([BUILT_WSRNG]),
        project_dir=tmp_path,
        check_remote=False,
        client=_NoClient(),
        build_configs={"wsrng-server": CFG_WSRNG},
    )

    assert [(i.service, i.state, i.check_source) for i in items] == [("wsrng-server", "never-checked", "none")]
    assert items[0].remote_digest is None and items[0].remote_error is None


def test_get_base_freshness_offline_falls_back_to_the_cached_check(bf, tmp_path):
    """A failed live check must not lose the cached verdict, its date, or the file."""
    _write(tmp_path, "docker/wsrng/Dockerfile", f"FROM {NODE}\n")
    seeded = _seed_lock(
        bf,
        tmp_path,
        digests=[DIG_A],
        checks={NODE: {"remote_digest": DIG_MOVED, "checked_at": "2026-10-20T05:00:00+00:00"}},
    )
    client = _FakeClient({NODE_KEY: RegistryError("cannot reach registry-1.docker.io: timed out")})

    items = bf.get_base_freshness(
        _PodmanImagesRunner([BUILT_WSRNG, LOCAL_NODE]),
        project_dir=tmp_path,
        client=client,
        build_configs={"wsrng-server": CFG_WSRNG},
    )

    (item,) = items
    assert item.state == "offline"
    assert item.check_source == "cache"
    assert item.remote_digest == DIG_MOVED
    assert item.checked_at == "2026-10-20T05:00:00+00:00"
    assert "cannot reach registry-1.docker.io" in item.remote_error
    assert json.loads((tmp_path / ".visp" / "base-lock.json").read_text()) == seeded


def test_get_base_freshness_caches_successful_checks(bf, tmp_path):
    """The live pass must persist checks (merged, with a UTC checked_at) for the next offline run."""
    _write(tmp_path, "docker/wsrng/Dockerfile", f"FROM {NODE}\n")
    _seed_lock(bf, tmp_path, digests=[DIG_A], checks={"alpine:3.23": {"remote_digest": DIG_B, "checked_at": "x"}})

    items = bf.get_base_freshness(
        _PodmanImagesRunner([BUILT_WSRNG, LOCAL_NODE]),
        project_dir=tmp_path,
        client=_FakeClient({NODE_KEY: DIG_MOVED}),
        build_configs={"wsrng-server": CFG_WSRNG},
    )
    assert items[0].state == "moved"

    stored = json.loads((tmp_path / ".visp" / "base-lock.json").read_text())
    assert stored["checks"][NODE]["remote_digest"] == DIG_MOVED
    assert stored["checks"][NODE]["checked_at"].endswith("+00:00")
    assert stored["checks"]["alpine:3.23"] == {"remote_digest": DIG_B, "checked_at": "x"}
    assert stored["images"]["built1"]["bases"][NODE]["digests"] == [DIG_A]


def test_get_base_freshness_cache_only_verdict_and_default_project_dir(bf, tmp_path, monkeypatch):
    """`images base` shape: cache-only, no network, project_dir from get_config()."""
    _write(tmp_path, "docker/apache/Dockerfile", "FROM docker.io/library/debian:trixie-20260406\n")
    _seed_lock(
        bf,
        tmp_path,
        built_id="built-apache",
        ref="debian:trixie-20260406",
        digests=[DIG_A],
        checks={"debian:trixie-20260406": {"remote_digest": DIG_A, "checked_at": "2026-10-20T05:00:00+00:00"}},
    )
    monkeypatch.setattr(bf, "get_config", lambda: type("C", (), {"project_dir": tmp_path})())

    items = bf.get_base_freshness(
        _PodmanImagesRunner([{"Id": "built-apache", "Names": ["localhost/visp-apache:latest"], "RepoDigests": []}]),
        check_remote=False,
        client=_NoClient(),
        build_configs={"apache": CFG_APACHE},
    )

    assert [(i.service, i.state, i.check_source) for i in items] == [("apache", "offline", "cache")]
    assert items[0]._digest_verdict() == "immobile"  # cached evidence still says immobile, just not "today"
    assert items[0].checked_at == "2026-10-20T05:00:00+00:00"


def test_get_base_freshness_excludes_unbuilt_services(bf, tmp_path):
    _write(tmp_path, "docker/wsrng/Dockerfile", f"FROM {NODE}\n")
    _write(tmp_path, "docker/tratt/Dockerfile", "FROM docker.io/httpd:2.4.67\n")
    client = _FakeClient({NODE_KEY: DIG_A})

    items = bf.get_base_freshness(
        _PodmanImagesRunner([BUILT_WSRNG, LOCAL_NODE]),
        project_dir=tmp_path,
        client=client,
        build_configs={"wsrng-server": CFG_WSRNG, "tratt": CFG_TRATT},
    )

    assert [i.service for i in items] == ["wsrng-server"]
    assert client.seen == [NODE_KEY]  # tratt's base was never looked up


def test_get_base_freshness_fetches_a_shared_base_once(bf, tmp_path):
    _write(tmp_path, "docker/wsrng/Dockerfile", f"FROM docker.io/library/{NODE}\n")
    _write(tmp_path, "docker/tratt/Dockerfile", f"FROM {NODE}\nRUN true\n")
    client = _FakeClient({NODE_KEY: DIG_MOVED})

    items = bf.get_base_freshness(
        _PodmanImagesRunner([BUILT_WSRNG, BUILT_TRATT, LOCAL_NODE]),
        project_dir=tmp_path,
        client=client,
        build_configs={"wsrng-server": CFG_WSRNG, "tratt": CFG_TRATT},
    )

    assert sorted(i.service for i in items) == ["tratt", "wsrng-server"]
    assert all(i.state == "heuristic" for i in items)
    assert client.seen == [NODE_KEY]  # one HEAD for a base two services share


def test_get_base_freshness_reports_build_stage_role(bf, tmp_path):
    """artic: node is the build stage, nginx is what actually ships."""
    _write(
        tmp_path,
        "docker/artic/Dockerfile",
        "FROM docker.io/library/node:22-bookworm-slim AS build\n"
        "RUN npm ci\n"
        "FROM docker.io/library/nginx:alpine AS production\n"
        "COPY --from=build /dist /usr/share/nginx/html\n",
    )
    client = _FakeClient({"library/node:22-bookworm-slim": DIG_A, "library/nginx:alpine": DIG_B})

    items = bf.get_base_freshness(
        _PodmanImagesRunner([{"Id": "built-artic", "Names": ["localhost/visp-artic:latest"], "RepoDigests": []}]),
        project_dir=tmp_path,
        client=client,
        build_configs={
            "artic": {
                "context": "./docker/artic",
                "dockerfile": "Dockerfile",
                "image": "visp-artic",
                "target": "production",
            }
        },
    )

    assert [(i.ref, i.role, i.stage_name, i.state) for i in items] == [
        ("node:22-bookworm-slim", "build", "build", "heuristic"),
        ("nginx:alpine", "runtime", "production", "heuristic"),
    ]
    assert sorted(client.seen) == ["library/nginx:alpine", "library/node:22-bookworm-slim"]


# ── 7. Fan-out and display ─────────────────────────────────────────────────────


def test_fan_out_groups_unique_service_roles_by_base(bf):
    items = [
        _item(bf, service="artic", ref="nginx:alpine", role="runtime"),
        _item(bf, service="artic", ref="node:22-bookworm-slim", role="build", stage_name="build"),
        _item(bf, service="nginx", ref="nginx:alpine", role="runtime"),
        _item(bf, service="artic", ref="nginx:alpine", role="runtime"),  # duplicate
    ]

    assert bf.fan_out(items) == {
        "nginx:alpine": [("artic", "runtime"), ("nginx", "runtime")],
        "node:22-bookworm-slim": [("artic", "build")],
    }


def test_format_base_line_fresh_and_moved(bf):
    fresh = _plain(
        bf.format_base_line(
            _item(bf, ref="nginx:alpine", check_source="live", recorded_digests=[DIG_A], remote_digest=DIG_A)
        )
    )
    assert fresh.startswith("  ") and "Base:" in fresh and "nginx:alpine" in fresh
    assert "fresh" in fresh and "immobile" not in fresh

    moved = _plain(
        bf.format_base_line(
            _item(
                bf,
                ref="nginx:alpine",
                role="build",
                check_source="live",
                recorded_digests=["sha256:abcdef0123456789"],
                remote_digest="sha256:2abea424fedcba98",
            )
        )
    )
    assert moved.startswith("  ") and "Base:" in moved and "MOVED" in moved
    assert "abcdef012345" in moved and "2abea424fedc" in moved  # first 12 hex of built-on vs now


def test_format_base_line_qualifies_every_cached_verdict(bf):
    """No cached line may describe the registry as it is today, in any verdict flavour."""
    stale = "2026-10-20T05:00:00+00:00"
    cached_guess = _plain(
        bf.format_base_line(
            _item(
                bf,
                check_source="cache",
                local_digests=[DIG_A],
                remote_digest=DIG_MOVED,
                checked_at=stale,
            )
        )
    )
    cached_fresh = _plain(
        bf.format_base_line(
            _item(bf, check_source="cache", recorded_digests=[DIG_A], remote_digest=DIG_A, checked_at=stale)
        )
    )

    assert "offline" in cached_guess and "2026-10-20" in cached_guess
    assert "today" not in cached_guess and "✓" not in cached_guess
    assert "local" in cached_guess.lower()  # still says it only compared the local copy
    assert cached_fresh.startswith("  ") and "cached fresh as of 2026-10-20" in cached_fresh
    assert "✓" not in cached_fresh  # an offline green is never printed as a confirmed green


def test_format_base_line_degrades_honestly(bf):
    never = _plain(bf.format_base_line(_item(bf, ref="alpine:3.23", check_source="none")))
    assert never.startswith("  ") and "Base:" in never and "never" in never.lower() and "MOVED" not in never

    offline = _plain(
        bf.format_base_line(
            _item(
                bf,
                ref="nginx:alpine",
                check_source="cache",
                remote_error="cannot reach registry-1.docker.io: timed out",
                recorded_digests=[DIG_A],
                remote_digest=DIG_MOVED,
                checked_at="2026-10-20T05:00:00+00:00",
            )
        )
    )
    assert "2026-10-20" in offline and "MOVED" in offline  # cached verdict + evidence age

    immobile = _plain(
        bf.format_base_line(
            _item(bf, ref="debian:trixie-20260406", check_source="live", recorded_digests=[DIG_A], remote_digest=DIG_A)
        )
    )
    assert "immobile" in immobile

    guess_local = _plain(
        bf.format_base_line(_item(bf, check_source="live", local_digests=[DIG_MOVED], remote_digest=DIG_MOVED))
    )
    guess_older = _plain(
        bf.format_base_line(_item(bf, check_source="live", local_digests=[DIG_A], remote_digest=DIG_MOVED))
    )
    assert "local" in guess_local.lower() and "local" in guess_older.lower()
    assert guess_local != guess_older  # the two flavours must not read the same
    # ... and both say they compare the local copy, never the baked-in content.
    assert all("baked" in line.lower() for line in (guess_local, guess_older))


def test_format_summary_empty_without_items(bf):
    assert bf.format_summary([], checked=True) == []


def test_format_summary_offline_keeps_the_not_fresh_count(bf):
    """The trap: cached (offline) verdicts must still count and still be named.

    Filtering the not-fresh line on ``state`` instead of the digest verdict would
    drop every cached row, so an air-gapped run would read as all-clear.
    """
    cached_move = _item(
        bf,
        service="artic",
        ref="nginx:alpine",
        check_source="cache",
        recorded_digests=[DIG_A],
        remote_digest=DIG_MOVED,
        checked_at="2026-10-20T05:00:00+00:00",
    )
    cached_guess = _item(
        bf,
        service="hs-wsr-client",
        ref="httpd:2.4",
        check_source="cache",
        local_digests=["sha256:aaa111"],
        remote_digest=DIG_MOVED,
        checked_at="2026-10-19T05:00:00+00:00",
    )

    text = "\n".join(_plain(line) for line in bf.format_summary([cached_move, cached_guess], checked=False))

    assert "2 of 2 images are NOT FRESH" in text
    assert "./visp.py build artic hs-wsr-client" in text
    assert "nginx:alpine" in text and "artic (runtime)" in text
    assert "newest cached check 2026-10-20" in text  # the age of the evidence, not just "not checked"


def test_format_summary_counts_names_remediation_and_fan_out(bf):
    moved = _item(
        bf,
        service="artic",
        ref="nginx:alpine",
        check_source="live",
        recorded_digests=[DIG_A],
        remote_digest=DIG_MOVED,
        checked_at="2026-10-22T06:31:00+00:00",
    )
    guess = _item(
        bf,
        service="hs-wsr-client",
        ref="httpd:2.4",
        check_source="live",
        local_digests=["sha256:aaa111"],
        remote_digest=DIG_MOVED,
    )
    ok = _item(
        bf,
        service="nginx",
        ref="nginx:alpine",
        check_source="live",
        recorded_digests=[DIG_A],
        remote_digest=DIG_A,
    )

    lines = [_plain(line) for line in bf.format_summary([moved, guess, ok], checked=True, applyable={"artic"})]
    text = "\n".join(lines)

    assert "2 of 3" in text and "NOT FRESH" in text
    builds = [line for line in lines if "./visp.py build" in line]
    applies = [line for line in lines if "./visp.py apply" in line]
    assert builds == ["    ./visp.py build artic hs-wsr-client"]  # build takes them all at once
    assert applies == ["    ./visp.py apply artic"]  # apply takes one, and only names an applyable service
    assert "hs-wsr-client has no quadlet to apply" in text
    assert "refresh" in text.lower()  # the build is what pulls the refreshed base
    assert "nginx:alpine" in text and "(runtime)" in text
    assert "Registry last checked" in text and "2026-10-22" in text
    assert "CVE" in lines[-1] and "digest" in lines[-1].lower()


def test_format_summary_with_an_empty_applyable_set_still_says_what_happens(bf):
    """Confirmed "nothing here is applyable" is not the same as "nobody looked".

    A machine whose only stale image is a session image must not be invited to run
    `./visp.py apply jupyter-session`, which the CLI would refuse.
    """
    item = _item(
        bf,
        service="jupyter-session",
        ref="quay.io/jupyter/datascience-notebook:r-4.5.2",
        check_source="live",
        recorded_digests=[DIG_A],
        remote_digest=DIG_MOVED,
    )

    text = "\n".join(_plain(line) for line in bf.format_summary([item], checked=True, applyable=set()))

    assert "./visp.py build jupyter-session" in text
    assert "jupyter-session has no quadlet to apply — picked up when the next session starts" in text
    assert "./visp.py apply" not in text


def test_printed_apply_targets_resolve_in_the_real_service_definitions(bf):
    """The reviewer's CLI coverage: ./visp.py apply takes one service and refuses the rest.

    Every apply target printed must be a name ``apply`` accepts (a container service
    with a quadlet), every applyable built service must get its own line, and the
    built-but-unapplyable ones must get the next-session guidance instead. Pulled
    third-party quadlets (mongo, matomo) are not build targets and must never appear.
    """
    from vispctl.build import BUILD_CONFIGS
    from vispctl.service import DEFAULT_SERVICES, container_service_names

    stale = [
        _item(
            bf,
            service=svc,
            ref="nginx:alpine",
            check_source="live",
            recorded_digests=[DIG_A],
            remote_digest=DIG_MOVED,
        )
        for svc in BUILD_CONFIGS
    ]
    applyable = container_service_names() & set(BUILD_CONFIGS)
    lines = [_plain(line) for line in bf.format_summary(stale, checked=True, applyable=applyable)]

    targets = [line.split()[-1] for line in lines if line.strip().startswith("./visp.py apply")]
    quadlets = {s.name for s in DEFAULT_SERVICES if s.type == "container"}
    built_without_quadlet = set(BUILD_CONFIGS) - applyable

    assert targets  # the mapping is not vacuously true
    assert set(targets) == applyable  # exactly one apply line per applyable built service
    assert set(targets) <= quadlets  # every target really resolves as an apply argument
    assert set(targets) & {"mongo", "mongo-express", "matomo", "matomo-db"} == set()
    assert {"jupyter-session", "session-proxy"} <= built_without_quadlet
    for svc in built_without_quadlet:
        assert any(line.strip().startswith(f"{svc} has no quadlet to apply") for line in lines)


def test_format_summary_last_checked_and_always_the_caveat(bf):
    old = _item(
        bf,
        service="artic",
        ref="nginx:alpine",
        check_source="cache",
        remote_digest=DIG_MOVED,
        recorded_digests=[DIG_A],
        checked_at="2026-10-20T05:00:00+00:00",
    )
    recent = _item(
        bf,
        service="nginx",
        ref="nginx:alpine",
        check_source="cache",
        remote_digest=DIG_A,
        checked_at="2026-10-22T06:31:00+00:00",
    )

    lines = [_plain(line) for line in bf.format_summary([old, recent], checked=True)]
    assert any("Registry last checked" in line and "2026-10-22" in line for line in lines)

    offline_lines = [_plain(line) for line in bf.format_summary([old], checked=False)]
    assert "not checked" in "\n".join(offline_lines).lower()
    assert "CVE" in offline_lines[-1]

    fresh_only = [_plain(line) for line in bf.format_summary([recent], checked=True)]
    assert "CVE" in fresh_only[-1]


def test_get_base_freshness_fresh_on_a_later_recorded_digest(bf, tmp_path):
    """The lock keeps every RepoDigest tail: the registry serving a later one is still fresh."""
    _write(tmp_path, "docker/wsrng/Dockerfile", f"FROM {NODE}\n")
    _seed_lock(bf, tmp_path, digests=[DIG_A, DIG_B])
    client = _FakeClient({NODE_KEY: DIG_B})

    items = bf.get_base_freshness(
        _PodmanImagesRunner([BUILT_WSRNG, LOCAL_NODE]),
        project_dir=tmp_path,
        client=client,
        build_configs={"wsrng-server": CFG_WSRNG},
    )

    assert items[0].recorded_digests == frozenset({DIG_A, DIG_B})
    assert items[0].state == "fresh"


def test_format_summary_counts_images_not_base_rows(bf):
    """One image with two bad bases is one not-fresh image, not two (DESIGN 3.2)."""
    stale_rows = [
        _item(
            bf,
            service="artic",
            ref="nginx:alpine",
            check_source="live",
            recorded_digests=[DIG_A],
            remote_digest=DIG_MOVED,
        ),
        _item(
            bf,
            service="artic",
            ref="node:22-bookworm-slim",
            role="build",
            check_source="live",
            recorded_digests=[DIG_A],
            remote_digest=DIG_MOVED,
        ),
    ]
    fresh = _item(
        bf,
        service="tratt",
        ref="httpd:2.4.67",
        check_source="live",
        recorded_digests=[DIG_A],
        remote_digest=DIG_A,
    )

    plain = "\n".join(_plain(line) for line in bf.format_summary(stale_rows + [fresh], checked=True))

    assert "1 of 2 images are NOT FRESH" in plain


def test_format_summary_names_the_registry_that_failed(bf):
    """A full outage reads as an outage, not as a wall of silent never-checked."""
    outage = "cannot reach registry-1.docker.io: timed out"
    down = [
        _item(bf, service="artic", ref=NODE, check_source="none", remote_error=outage),
        _item(bf, service="artic", ref="httpd:2.4", role="build", check_source="none", remote_error=outage),
    ]

    lines = [_plain(line) for line in bf.format_summary(down, checked=True)]
    text = "\n".join(lines)
    failed = [line for line in lines if "check failed" in line]
    assert len(failed) == 1  # one line naming the host, never one per base
    assert "registry-1.docker.io" in failed[0]
    assert "not checked" not in text  # an outage must not also read as --offline

    healthy = _item(
        bf,
        service="svc",
        ref=NODE,
        check_source="live",
        recorded_digests=[DIG_A],
        remote_digest=DIG_A,
        checked_at="2026-10-22T06:31:00+00:00",
    )
    plain = "\n".join(_plain(line) for line in bf.format_summary([healthy], checked=True))
    assert "check failed" not in plain


def _offline_display(bf, monkeypatch, tmp_path, capsys, cached_digest: str) -> str:
    """Run the real ``--offline`` display for one built image whose only evidence is a cached check."""
    from vispctl import images as images_mod

    _write(tmp_path, "docker/wsrng/Dockerfile", f"FROM {NODE}\n")
    _seed_lock(
        bf,
        tmp_path,
        digests=[DIG_A],
        checks={NODE: {"remote_digest": cached_digest, "checked_at": "2026-10-20T05:00:00+00:00"}},
    )
    monkeypatch.setattr(bf, "get_config", lambda: type("C", (), {"project_dir": tmp_path})())
    monkeypatch.setattr(bf, "BUILD_CONFIGS", {"wsrng-server": CFG_WSRNG})

    class Boom:
        def __init__(self, *a, **k):
            raise AssertionError("registry must not be contacted when check_remote=False")

    monkeypatch.setattr(bf, "RegistryClient", Boom)
    found = {"visp-wsrng-server": {"tag": "latest", "size": "1 kB", "created": "yesterday"}}
    monkeypatch.setattr(
        images_mod.ImageManager, "get_visp_images", lambda self: (found, {"visp-wsrng-server": "wsrng-server"})
    )

    images_mod.ImageManager(_PodmanImagesRunner([BUILT_WSRNG, LOCAL_NODE])).display_visp_images(check_remote=False)
    return _plain(capsys.readouterr().out)


def test_display_visp_images_offline_never_constructs_a_registry_client(bf, monkeypatch, tmp_path, capsys):
    """Air-gapped operators run --offline: no registry, and no unqualified green either."""
    out = _offline_display(bf, monkeypatch, tmp_path, capsys, DIG_A)

    assert "Base:" in out and NODE in out
    assert "offline — cached fresh as of 2026-10-20" in out
    assert "✓ fresh" not in out


def test_display_visp_images_offline_still_flags_a_cached_move(bf, monkeypatch, tmp_path, capsys):
    """The per-base rebuild hint must survive --offline, or an air-gapped run goes silent."""
    out = _offline_display(bf, monkeypatch, tmp_path, capsys, DIG_MOVED)

    assert "offline — cached MOVED as of 2026-10-20" in out
    # wsrng-server owns a quadlet, so the real wiring may name its apply target
    assert "→ ./visp.py build wsrng-server && ./visp.py apply wsrng-server" in out


# ── 8. Terminal sanitization (F1) ──────────────────────────────────────────────


def test_printable_marks_only_the_control_characters():
    """The sanitizer runs on data, before our own colour codes are wrapped around it."""
    from vispctl.runner import printable

    assert printable("node:24.16.0-alpine3.22") == "node:24.16.0-alpine3.22"
    assert printable("a\x1b]52;c;x\x07b\x00c") == "a·]52;c;x·b·c"


def test_hostile_dockerfile_text_renders_without_control_bytes(bf, tmp_path):
    """A stage name carrying OSC-52 (set clipboard) must print inert, never reach the terminal."""
    hostile = "evil\x1b]52;c;aGVsbG8=\x07"
    _write(tmp_path, "docker/x/Dockerfile", f"FROM docker.io/library/nginx:alpine AS {hostile}\n")

    ((ref, _role, _stage),) = bf.stage_bases(tmp_path, _cfg("./docker/x", "Dockerfile"))
    line = _plain(bf.format_base_line(_item(bf, ref=ref, check_source="none")))

    assert "\x1b" not in line and "\x07" not in line  # zero control bytes past our own colour codes
    assert "evil" in line and "·" in line  # the literal is still shown, marked


def test_unparsable_from_line_is_reported_but_never_fetched(bf, tmp_path):
    """FROM ${BASE} names no registry ref: show the literal, send nothing to a registry."""
    _write(tmp_path, "docker/wsrng/Dockerfile", "ARG BASE=docker.io/library/nginx:alpine\nFROM ${BASE}\n")

    items = bf.get_base_freshness(
        _PodmanImagesRunner([BUILT_WSRNG]),
        project_dir=tmp_path,
        client=_NoClient(),
        build_configs={"wsrng-server": CFG_WSRNG},
    )

    assert [(i.service, i.state, i.check_source) for i in items] == [("wsrng-server", "never-checked", "none")]
    assert items[0].ref == "${BASE}"


# ── 9. What the summary may claim (F2) ─────────────────────────────────────────


def test_summary_splits_a_moved_base_from_an_unrecorded_one(bf):
    """Two different kinds of stale must not print as one confirmed verdict."""
    moved = _item(
        bf,
        service="artic",
        ref="nginx:alpine",
        check_source="live",
        recorded_digests=[DIG_A],
        remote_digest=DIG_MOVED,
    )
    guess = _item(
        bf,
        service="hs-wsr-client",
        ref="httpd:2.4",
        check_source="live",
        local_digests=["sha256:aaa111"],
        remote_digest=DIG_MOVED,
    )

    text = "\n".join(_plain(line) for line in bf.format_summary([moved, guess], checked=True, applyable=set()))

    assert "2 of 2 images are NOT FRESH" in text
    assert "1 image: base moved behind the tag" in text
    assert "1 image: no build record (heuristic)" in text
    assert "--pull=newer makes the verdict exact" in text


def test_summary_prints_only_the_non_zero_half_of_the_split(bf):
    moved = _item(
        bf,
        service="artic",
        ref="nginx:alpine",
        check_source="live",
        recorded_digests=[DIG_A],
        remote_digest=DIG_MOVED,
    )

    text = "\n".join(_plain(line) for line in bf.format_summary([moved], checked=True, applyable={"artic"}))

    assert "1 image: base moved behind the tag" in text
    assert "no build record" not in text


def test_never_checked_is_excluded_from_the_stale_count_and_the_remediation(bf):
    """No registry evidence is not evidence of staleness — offline or outage alike."""
    silent_offline = _item(bf, service="tratt", ref="node:22", check_source="none")
    outage = _item(
        bf,
        service="artic",
        ref="nginx:alpine",
        check_source="none",
        remote_error="cannot reach registry-1.docker.io: timed out",
    )
    moved = _item(
        bf,
        service="nginx",
        ref="httpd:2.4.67",
        check_source="live",
        recorded_digests=[DIG_A],
        remote_digest=DIG_MOVED,
    )

    assert [bf.needs_rebuild(b) for b in (silent_offline, outage)] == [False, False]
    assert bf.needs_rebuild(moved) is True

    text = "\n".join(
        _plain(line) for line in bf.format_summary([silent_offline, outage, moved], checked=True, applyable=set())
    )

    assert "1 of 3 images are NOT FRESH" in text  # the silent rows count, but only in the denominator
    assert "./visp.py build nginx" in text and "tratt" not in text and "artic" not in text.split("check failed")[0]


def test_a_heuristic_row_is_not_reported_as_a_move(bf):
    """is_moved drives the per-base rebuild hint: a guess must not order a build && apply."""
    guess = _item(bf, check_source="live", local_digests=[DIG_A], remote_digest=DIG_MOVED)
    cached_move = _item(bf, check_source="cache", recorded_digests=[DIG_A], remote_digest=DIG_MOVED)
    never = _item(bf, check_source="none")

    assert [bf.is_moved(b) for b in (guess, never)] == [False, False]
    assert bf.is_moved(cached_move) is True  # an offline move still earns its command line


def test_display_visp_images_prints_no_rebuild_arrow_for_a_heuristic_base(bf, monkeypatch, tmp_path, capsys):
    from vispctl import images as images_mod

    _write(tmp_path, "docker/wsrng/Dockerfile", f"FROM {NODE}\n")
    monkeypatch.setattr(bf, "get_config", lambda: type("C", (), {"project_dir": tmp_path})())
    monkeypatch.setattr(bf, "BUILD_CONFIGS", {"wsrng-server": CFG_WSRNG})
    monkeypatch.setattr(bf, "RegistryClient", lambda *a, **k: _FakeClient({NODE_KEY: DIG_MOVED}))
    found = {"visp-wsrng-server": {"tag": "latest", "size": "1 kB", "created": "yesterday"}}
    monkeypatch.setattr(
        images_mod.ImageManager, "get_visp_images", lambda self: (found, {"visp-wsrng-server": "wsrng-server"})
    )

    images_mod.ImageManager(_PodmanImagesRunner([BUILT_WSRNG, LOCAL_NODE])).display_visp_images(check_remote=True)
    out = _plain(capsys.readouterr().out)

    assert "heuristic" in out and "→ ./visp.py build" not in out
    assert "1 of 1 images are NOT FRESH" in out  # the guess is still named, just not as a move


# ── 10. A built image with no readable definition (F3) ────────────────────────


WHISPERX = {"context": "./external/WhisperVault", "dockerfile": "container/Containerfile", "image": "visp-whisperx"}
BUILT_WHISPERX = {"Id": "built-wx", "Names": ["localhost/visp-whisperx:latest"], "RepoDigests": []}


def _missing_definition_items(bf, tmp_path):
    """whisperx is built, but ``external/`` is gitignored: nothing names its bases."""
    return bf.get_base_freshness(
        _PodmanImagesRunner([BUILT_WHISPERX]),
        project_dir=tmp_path,
        client=_NoClient(),
        build_configs={"whisperx": WHISPERX},
    )


def test_a_built_service_without_its_build_definition_still_gets_a_row(bf, tmp_path):
    """Silence here would shrink the report and the denominator (DESIGN 3.x: never read as safe)."""
    (item,) = _missing_definition_items(bf, tmp_path)  # a live pass, and no fetch happened

    assert item.service == "whisperx" and item.role == "runtime"
    assert item.ref == "no build definition at ./external/WhisperVault/container/Containerfile"
    assert (item.state, item.check_source) == ("never-checked", "none")
    assert "Base:" in bf.format_base_line(item)


def test_a_missing_build_definition_still_counts_in_the_denominator(bf, tmp_path):
    moved = _item(
        bf,
        service="artic",
        ref="nginx:alpine",
        check_source="live",
        recorded_digests=[DIG_A],
        remote_digest=DIG_MOVED,
    )

    text = "\n".join(
        _plain(line) for line in bf.format_summary(_missing_definition_items(bf, tmp_path) + [moved], checked=False)
    )

    assert "1 of 2 images are NOT FRESH" in text


def test_a_digest_pinned_dockerfile_is_not_reported_as_missing(bf, tmp_path):
    """Stages exist and nothing can move: an empty answer is the honest one."""
    _write(tmp_path, "docker/pinned/Dockerfile", f"FROM nginx:alpine@sha256:{'0' * 64}\n")

    assert bf.stage_bases(tmp_path, _cfg("./docker/pinned", "Dockerfile")) == []

    items = bf.get_base_freshness(
        _PodmanImagesRunner([{"Id": "b", "Names": ["localhost/visp-svc:latest"], "RepoDigests": []}]),
        project_dir=tmp_path,
        client=_NoClient(),
        build_configs={"svc": {"context": "./docker/pinned", "dockerfile": "Dockerfile", "image": "visp-svc"}},
    )

    assert items == []


# ── 11. What the real Dockerfile lexer accepts and the scanner must too (F4) ───


def _one(bf, tmp_path, name: str, text: str, target: str | None = None):
    """stage_bases over a synthetic Dockerfile of its own."""
    _write(tmp_path, f"docker/{name}/Dockerfile", text)
    return bf.stage_bases(tmp_path, _cfg(f"./docker/{name}", "Dockerfile", target=target))


def test_continued_lines_still_name_their_base(bf, tmp_path):
    """A backslash joins the line before anything is parsed, as it does in buildah."""
    assert _one(
        bf,
        tmp_path,
        "cont-copy",
        "FROM docker.io/library/node:22 AS build\n"
        "RUN npm ci\n"
        "FROM docker.io/library/nginx:alpine AS production\n"
        "COPY --chown=1000:1000 \\\n"
        "     --from=build \\\n"
        "     /dist /usr/share/nginx/html\n",
    ) == [("node:22", "build", "build"), ("nginx:alpine", "runtime", "production")]

    assert _one(
        bf,
        tmp_path,
        "cont-from",
        "FROM \\\n    docker.io/library/debian:trixie-20261020 AS base\nRUN true\n",
    ) == [("debian:trixie-20261020", "runtime", "base")]


def test_a_variable_ref_is_kept_out_of_the_registry_client(bf, tmp_path):
    """FROM ${BASE_IMAGE} names nothing we can resolve: keep the literal, fetch nothing."""
    assert _one(bf, tmp_path, "arg", "ARG BASE_IMAGE=docker.io/library/debian:trixie\nFROM ${BASE_IMAGE}\n") == [
        ("${BASE_IMAGE}", "runtime", None)
    ]
    assert bf.fetchable("${BASE_IMAGE}") is False
    assert bf.fetchable("debian:trixie-20261020") is True


def test_a_heredoc_body_is_data_not_stages(bf, tmp_path):
    """A phantom FROM inside a heredoc must not become a stage: it would also shift a COPY --from=<n>."""
    assert _one(
        bf,
        tmp_path,
        "heredoc",
        "FROM docker.io/library/node:22 AS tools\n"
        "RUN <<EOF\n"
        "FROM bogus:1\n"
        "EOF\n"
        "FROM docker.io/library/debian:trixie AS build\n"
        "RUN npm ci\n"
        "FROM docker.io/library/nginx:alpine\n"
        "COPY --from=1 /out /out\n",
    ) == [("debian:trixie", "build", "build"), ("nginx:alpine", "runtime", None)]


def test_stage_names_match_whatever_case_they_were_declared_in(bf, tmp_path):
    """buildah lowercases stage names; a case-sensitive lookup reads 'FROM base' as a registry image."""
    assert _one(
        bf,
        tmp_path,
        "case",
        "FROM docker.io/library/debian:trixie AS Base\n" "RUN true\n" "FROM base AS final\n" "COPY --from=BASE /a /b\n",
    ) == [("debian:trixie", "runtime", "final")]


def test_a_space_separated_from_flag_does_not_swallow_the_ref(bf, tmp_path):
    """'--platform linux/amd64' is one flag; an unknown '--foo' must not eat the token after it either."""
    assert _one(bf, tmp_path, "flag", "FROM --platform linux/amd64 docker.io/library/debian:trixie-20261020\n") == [
        ("debian:trixie-20261020", "runtime", None)
    ]

    assert _one(bf, tmp_path, "flag2", "FROM --foo docker.io/library/nginx:alpine\n") == [
        ("nginx:alpine", "runtime", None)
    ]
