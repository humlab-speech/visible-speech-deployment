"""Base-image freshness: visibility (images) and explicit refresh (build).

podman never re-resolves a tag it already has ('--pull=newer' is a measured
no-op on 5.7), so visp.py owns the check: ask the registry for the digest,
compare with local RepoDigests, pull only when the tag moved.
"""

import types

import pytest

import vispctl.build as build_mod
import vispctl.images as images_mod
from vispctl.images import ImageManager
from vispctl.registry import RegistryError


class _Runner:
    """Answers podman inspect/pull from scripted dicts."""

    def __init__(self, digests=None, pull_rc=0):
        self.digests = digests or {}  # image -> list[RepoDigests] or None for absent
        self.pull_rc = pull_rc
        self.pulled = []

    def run_quiet(self, cmd, **kwargs):
        if cmd[:3] == ["podman", "image", "inspect"]:
            image = cmd[-1]
            if image not in self.digests:
                return 1, "", "no such image"
            return 0, str(self.digests[image] or []).replace("'", '"'), ""
        if cmd[:2] == ["podman", "pull"]:
            self.pulled.append(cmd[-1])
            return self.pull_rc, "", "boom" if self.pull_rc else ""
        return 1, "", ""


class _Client:
    def __init__(self, digests=None, error=None):
        self.digests = digests or {}
        self.error = error
        self.asks = []

    def tag_digest(self, ref):
        self.asks.append(str(ref.tag))
        if self.error:
            raise RegistryError(self.error)
        return self.digests[ref.tag if hasattr(ref, "tag") else ref]


def _freshness_client_for(tag_digests):
    class C:
        def __init__(self):
            self.asks = []

        def tag_digest(self, ref):
            self.asks.append(ref)
            return tag_digests[ref.tag]

    return C()


@pytest.fixture
def scanned(tmp_path, monkeypatch):
    """One service Dockerfile with a registry base, and one localhost base."""
    df = tmp_path / "docker" / "octra" / "Dockerfile"
    df.parent.mkdir(parents=True)
    df.write_text("FROM node:24.15.0-bookworm-slim\nRUN true\n")
    monkeypatch.setattr(images_mod, "get_config", lambda: types.SimpleNamespace(project_dir=tmp_path))
    return tmp_path


def test_stale_when_registry_digest_not_among_local_ones(scanned):
    # local holds TWO digests (index + manifest); remote matches neither -> stale
    im = ImageManager(runner=_Runner({"node:24.15.0-bookworm-slim": ["node@sha256:aaaa", "node@sha256:bbbb"]}))
    st = im.base_freshness(_freshness_client_for({"24.15.0-bookworm-slim": "sha256:cccc"}))
    assert st["node:24.15.0-bookworm-slim"]["status"] == "stale"


def test_current_when_registry_digest_is_any_of_the_local_ones(scanned):
    # set membership, not equality against any single stored digest: the
    # registry digest must match whichever of the two RepoDigests it is,
    # independent of set iteration order (a first-digest comparison passes
    # or fails by hash seed otherwise)
    for match in ("sha256:aaaa", "sha256:bbbb"):
        im = ImageManager(runner=_Runner({"node:24.15.0-bookworm-slim": ["node@sha256:aaaa", "node@sha256:bbbb"]}))
        st = im.base_freshness(_freshness_client_for({"24.15.0-bookworm-slim": match}))
        assert st["node:24.15.0-bookworm-slim"]["status"] == "current", match


def test_absent_base_is_reported_and_skips_local_digests(scanned):
    im = ImageManager(runner=_Runner({}))
    st = im.base_freshness(_freshness_client_for({"24.15.0-bookworm-slim": "sha256:cccc"}))
    assert st["node:24.15.0-bookworm-slim"]["status"] == "absent"


def test_unreachable_registry_is_unknown_not_stale(scanned):
    im = ImageManager(runner=_Runner({"node:24.15.0-bookworm-slim": ["node@sha256:aaaa"]}))
    st = im.base_freshness(_Client(error="no route"))
    entry = st["node:24.15.0-bookworm-slim"]
    assert entry["status"] == "unknown" and entry["error"] == "no route"


def test_offline_display_never_claims_current_or_stale(scanned):
    im = ImageManager(runner=_Runner({"node:24.15.0-bookworm-slim": ["node@sha256:aaaa"]}))
    st = im.base_freshness(None)
    assert st["node:24.15.0-bookworm-slim"]["status"] == "unknown"


def test_localhost_bases_are_not_asked_of_any_registry(tmp_path, monkeypatch):
    df = tmp_path / "docker" / "x" / "Dockerfile"
    df.parent.mkdir(parents=True)
    df.write_text("FROM localhost:5000/team/app:1.2\n")
    monkeypatch.setattr(images_mod, "get_config", lambda: types.SimpleNamespace(project_dir=tmp_path))
    im = ImageManager(runner=_Runner({}))
    assert im.base_freshness(_Client(error="should not be called")) == {}


# --- build-time refresh ---


def _statuses(st_map):
    class IM:
        def __init__(self, runner):
            self.runner = runner

        def base_freshness(self, client):
            return {
                img: {
                    "files": [("docker/octra/Dockerfile", None)],
                    "local_digests": set(),
                    "remote_digest": None,
                    "error": err,
                    "error_class": (spec + (None, None))[2] if err else None,
                    "status": st,
                }
                for img, spec in st_map.items()
                for st, err in [spec[:2]]
            }

    return IM


def test_stale_and_absent_get_pulled_current_does_not(monkeypatch):
    runner = _Runner({})
    monkeypatch.setattr(
        images_mod,
        "ImageManager",
        _statuses({"a:1": ("stale", None), "b:1": ("current", None), "c:1": ("absent", None)}),
    )
    build_mod.refresh_base_images(runner, {"octra": {"context": "./docker/octra"}}, ["octra"], client=object())
    assert sorted(runner.pulled) == ["docker.io/library/a:1", "docker.io/library/c:1"]


def test_unreachable_registry_is_a_transport_failure_not_a_green_light(monkeypatch, capsys):
    """Transport class end-to-end: warn, pull nothing, never raise (the build proceeds)."""
    runner = _Runner({})
    monkeypatch.setattr(images_mod, "ImageManager", _statuses({"a:1": ("unknown", "no route", "transport")}))
    build_mod.refresh_base_images(runner, {"octra": {"context": "./docker/octra"}}, ["octra"], client=object())
    out = capsys.readouterr().out
    assert runner.pulled == []
    assert "registry not reachable" in out  # the transport wording, not the 'answered oddly' catch-all
    assert "building from cached base" in out


def test_failed_pull_warns_without_raising(monkeypatch, capsys):
    runner = _Runner({}, pull_rc=1)
    monkeypatch.setattr(images_mod, "ImageManager", _statuses({"a:1": ("stale", None)}))
    build_mod.refresh_base_images(  # must not raise
        runner, {"octra": {"context": "./docker/octra"}}, ["octra"], client=object()
    )
    assert "could not pull a:1" in capsys.readouterr().out


def test_no_pull_skips_the_whole_phase(monkeypatch):
    called = []
    monkeypatch.setattr(images_mod, "ImageManager", lambda runner: called.append("im"))
    runner = _Runner({})
    build_mod.refresh_base_images(runner, {"octra": {"context": "./docker/octra"}}, ["octra"], no_pull=True)
    assert called == [] and runner.pulled == []


def test_bases_of_other_services_are_left_alone(monkeypatch):
    runner = _Runner({})
    monkeypatch.setattr(images_mod, "ImageManager", _statuses({"a:1": ("stale", None)}))
    build_mod.refresh_base_images(runner, {"octra": {"context": "./docker/apache"}}, ["octra"], client=object())
    assert runner.pulled == []


def test_cmd_build_wires_no_pull_through(monkeypatch):
    seen = {}

    def fake_refresh(runner, build_configs, ordered, no_pull=False, client=None):
        seen["no_pull"] = no_pull

    monkeypatch.setattr(build_mod, "refresh_base_images", fake_refresh)
    monkeypatch.setattr(build_mod, "BuildManager", _StubBM)
    monkeypatch.setattr(build_mod, "Runner", lambda: _Runner({}))
    args = types.SimpleNamespace(
        services=["apache"], list=False, no_cache=False, pull=False, config=None, force=True, no_pull=True
    )
    build_mod.cmd_build(
        args,
        runner=_Runner({}),
        build_configs={"apache": build_mod.BUILD_CONFIGS["apache"]},
        node_configs={},
        all_buildable=["apache"],
    )
    assert seen["no_pull"] is True


class _StubBM:
    def __init__(self, runner, build_configs=None, node_configs=None):
        pass

    def check_version_drift(self, ordered, mode):
        return [], False

    def prepare_build_context(self, s, c):
        return True

    def run_builds(self, ordered, **kw):
        return {"success": list(ordered), "failed": [], "skipped": []}


def test_pull_names_are_fully_qualified(monkeypatch):
    """Absent bases must be pulled by canonical name; short names fail to resolve."""
    runner = _Runner({})
    monkeypatch.setattr(
        images_mod,
        "ImageManager",
        _statuses({"httpd:2.4.67": ("absent", None), "quay.io/jupyter/x:r-4.5.2": ("stale", None)}),
    )
    build_mod.refresh_base_images(runner, {"octra": {"context": "./docker/octra"}}, ["octra"], client=object())
    assert sorted(runner.pulled) == ["docker.io/library/httpd:2.4.67", "quay.io/jupyter/x:r-4.5.2"]


def test_entry_shape_is_pinned_and_drives_refresh(scanned, monkeypatch):
    """refresh_base_images consumes real base_freshness entries verbatim.

    The producer key set is pinned and the pull decision is driven by a
    real clean entry; the class-key READ on errored entries is pinned by
    test_consumer_reads_the_class_key_from_real_entries,
    and the wording and streak placement by test_base_freshness_guards.
    Originally this docstring claimed either-side renames die here alone —
    a consumer-only error_class rename stayed green on it; the pull
    decision here is driven by the real producer, not by a hand-written
    fake entry.
    """
    real = ImageManager(runner=_Runner({}))  # nothing local -> absent
    monkeypatch.setattr(images_mod, "ImageManager", lambda *a, **k: real)
    client = _freshness_client_for({"24.15.0-bookworm-slim": "sha256:cccc"})
    statuses = real.base_freshness(client)
    assert set(statuses["node:24.15.0-bookworm-slim"]) == {"files", "local_digests", "remote_digest", "error", "error_class", "status"}
    pull_runner = _Runner({})
    build_mod.refresh_base_images(pull_runner, {"octra": {"context": "./docker/octra"}}, ["octra"], client=client)
    assert pull_runner.pulled == ["docker.io/library/node:24.15.0-bookworm-slim"]


def test_offline_flag_reaches_base_subcommand_from_either_position(monkeypatch):
    import sys

    import visp

    seen = []
    monkeypatch.setattr(visp, "cmd_images_base", lambda args, *a, **k: seen.append(getattr(args, "offline", False)))
    for argv, want in (
        (["visp.py", "images", "--offline", "base"], True),
        (["visp.py", "images", "base", "--offline"], True),
        (["visp.py", "images", "base"], False),
    ):
        monkeypatch.setattr(sys, "argv", argv)
        seen.clear()
        try:
            visp.main()
        except SystemExit:
            pass
        assert seen == [want], argv


# ---- four error classes + fail-fast streak (base-image-freshness rev) ----

def _err_statuses(spec_map, files=None):
    class IM:
        def __init__(self, runner):
            self.runner = runner

        def base_freshness(self, client):
            return {
                img: {
                    "files": files or [("docker/octra/Dockerfile", None)],
                    "local_digests": set(),
                    "remote_digest": None,
                    "error": err,
                    "error_class": klass,
                    "status": "unknown" if err else "current",
                }
                for img, (err, klass) in spec_map.items()
            }
    return IM


_OCTRA = {"octra": {"context": "./docker/octra"}}


def test_refused_is_loud_and_never_says_cached(monkeypatch, capsys):
    runner = _Runner({})
    monkeypatch.setattr(images_mod, "ImageManager", _err_statuses({"a:1": ("realm outside registry", "refused")}))
    build_mod.refresh_base_images(runner, _OCTRA, ["octra"], client=object())
    out = capsys.readouterr().out
    assert "REFUSED" in out and "investigate" in out
    assert "building from cached" not in out  # security event, not an outage


def test_auth_and_gone_are_answered_and_do_not_stop_the_sweep(monkeypatch, capsys):
    runner = _Runner({})
    monkeypatch.setattr(
        images_mod,
        "ImageManager",
        _err_statuses({"a:1": ("HTTP 401", "auth"), "b:1": ("HTTP 404", "gone"), "c:1": ("no route", "transport")}),
    )
    build_mod.refresh_base_images(runner, _OCTRA, ["octra"], client=object())
    out = capsys.readouterr().out
    assert "needs credentials" in out and "not at the registry" in out
    assert "two consecutive" not in out  # answered questions never feed the streak


def test_two_transport_failures_stop_the_sweep(monkeypatch, capsys):
    runner = _Runner({})
    monkeypatch.setattr(
        images_mod,
        "ImageManager",
        _err_statuses({
            "a:1": ("no route", "transport"),
            "b:1": ("no route", "transport"),
            "c:1": ("no route", "transport"),  # never reached
        }),
    )
    build_mod.refresh_base_images(runner, _OCTRA, ["octra"], client=object())
    assert "two consecutive registry transport failures" in capsys.readouterr().out


def test_one_transport_then_answered_never_stops_the_sweep(monkeypatch, capsys):
    """The amendment's case: blip, answered, blip, real work — no silence."""
    runner = _Runner({})
    monkeypatch.setattr(
        images_mod,
        "ImageManager",
        _err_statuses({
            "a:1": ("no route", "transport"),
            "b:1": ("HTTP 404", "gone"),        # answered -> resets
            "c:1": ("HTTP 401", "auth"),         # answered -> resets
            "d:1": ("realm outside registry", "refused"),  # answered -> resets
        }),
    )
    build_mod.refresh_base_images(runner, _OCTRA, ["octra"], client=object())
    assert "two consecutive" not in capsys.readouterr().out


def test_answered_then_single_blip_then_work_all_gets_checked(monkeypatch, capsys):
    """After a transport blip, one answered question must let a later stale base still pull."""
    runner = _Runner({})

    class IM:
        def __init__(self, runner):
            self.runner = runner

        def base_freshness(self, client):
            return {
                "a:1": {"files": [("docker/octra/Dockerfile", None)], "local_digests": set(),
                        "remote_digest": None, "error": "no route", "error_class": "transport", "status": "unknown"},
                "b:1": {"files": [("docker/octra/Dockerfile", None)], "local_digests": set(),
                        "remote_digest": None, "error": "HTTP 404", "error_class": "gone", "status": "unknown"},
                "c:1": {"files": [("docker/octra/Dockerfile", None)], "local_digests": set(),
                        "remote_digest": None, "error": "no route", "error_class": "transport", "status": "unknown"},
                "d:1": {"files": [("docker/octra/Dockerfile", None)], "local_digests": set(),
                        "remote_digest": "sha256:z", "error": None, "error_class": None, "status": "stale"},
            }

    monkeypatch.setattr(images_mod, "ImageManager", IM)
    build_mod.refresh_base_images(runner, _OCTRA, ["octra"], client=object())
    assert "two consecutive" not in capsys.readouterr().out
    assert runner.pulled == ["docker.io/library/d:1"]  # d still reached


def _claims(build_configs, ordered):
    return build_mod._service_base_claims(build_configs, ordered)


def test_membership_is_exact_path_not_directory_prefix():
    # a repo-root context must NOT claim every Dockerfile
    assert _claims({"web": {"context": ".", "dockerfile": "docker/web/Dockerfile"}}, ["web"]) == {"docker/web/Dockerfile"}
    # context-only defaults to <ctx>/Dockerfile (podman's own default)
    assert _claims({"octra": {"context": "./docker/octra"}}, ["octra"]) == {"docker/octra/Dockerfile"}


def test_membership_follows_dockerfile_out_of_the_context():
    # artic builds docker/artic/Dockerfile from external/artic via ../../
    claims = _claims({"artic": {"context": "./external/artic", "dockerfile": "../../docker/artic/Dockerfile"}}, ["artic"])
    assert claims == {"docker/artic/Dockerfile"}


def test_real_configs_claim_one_dockerfile_each():
    from vispctl.build import BUILD_CONFIGS
    claims = _claims(BUILD_CONFIGS, sorted(BUILD_CONFIGS))
    # every real buildable claims exactly one path, none claim a bare directory
    for svc in BUILD_CONFIGS:
        one = _claims({svc: BUILD_CONFIGS[svc]}, [svc])
        assert len(one) == 1, svc
        path = next(iter(one))
        assert path.endswith(("Dockerfile", "Containerfile")), (svc, path)
    # the exact set is stable and no service claims a bare directory
    assert claims == {
        "docker/apache/Dockerfile",
        "docker/artic/Dockerfile",
        "docker/podman-socket-proxy/Dockerfile",
        "docker/session-manager/jupyter-session/Dockerfile",
        "docker/session-proxy/Dockerfile",
        "docker/tratt/Dockerfile",
        "external/emu-webapp-server/docker/Dockerfile",
        "external/session-manager/Dockerfile",
        "external/WhisperVault/container/Containerfile",
        "external/wsrng-server/Dockerfile",
    }


def test_node_only_build_never_contacts_the_registry(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("ImageManager built for a node-only build")

    monkeypatch.setattr(images_mod, "ImageManager", boom)
    runner = _Runner({})
    build_mod.refresh_base_images(runner, {}, ["webclient"], client=object())  # no context => no claims
    assert runner.pulled == []


def test_error_class_maps_registry_exceptions():
    """_error_class must map the real exception family, not just fake strings."""
    from vispctl.images import _error_class
    from vispctl.registry import (
        RegistryError,
        RegistryHTTPStatusError,
        RegistryTransportError,
        RegistryTrustError,
    )

    assert _error_class(RegistryTransportError("x")) == "transport"
    assert _error_class(RegistryTrustError("x")) == "refused"
    assert _error_class(RegistryHTTPStatusError("x", 404)) == "gone"
    assert _error_class(RegistryHTTPStatusError("x", 401)) == "auth"
    assert _error_class(RegistryHTTPStatusError("x", 403)) == "auth"
    assert _error_class(RegistryHTTPStatusError("x", 500)) == "other"
    assert _error_class(RegistryError("x")) == "other"


def test_real_exception_flows_to_auth_wording(tmp_path, monkeypatch, capsys):
    """A real 401 exception (not a faked class) reaches BOTH reporters as credential wording.

    The bases swept have to come from a real scan, so the Dockerfile lives in a
    tmp project dir — a claim that points at a nonexistent Dockerfile would
    leave refresh_base_images with nothing to print and the assertions vacuous.
    """
    from vispctl.registry import RegistryHTTPStatusError

    monkeypatch.setattr(images_mod, "get_config", lambda: type("C", (), {"project_dir": tmp_path})())
    dockerfile = tmp_path / "docker" / "x" / "Dockerfile"
    dockerfile.parent.mkdir(parents=True)
    dockerfile.write_text("FROM x:1.0\n")

    class Client:
        def tag_digest(self, ref):
            raise RegistryHTTPStatusError("registry.example.com answered HTTP 401 for repo", 401)

    runner = _Runner({})
    st = images_mod.ImageManager(runner).base_freshness(Client())
    assert [e["error_class"] for e in st.values()] == ["auth"], st

    build_mod.refresh_base_images(runner, {"x": {"context": "./docker/x"}}, ["x"], client=Client())
    out = capsys.readouterr().out
    assert "needs credentials" in out, out  # the wording the name promises, on the refresh path too
    assert "HTTP 401" in out, out  # the underlying reason is not swallowed
    assert "not reachable" not in out and "REFUSED" not in out, out

def test_two_transport_failures_stop_before_remaining_work(monkeypatch, capsys):
    """The stop is at TWO, and it protects the rest: a stale base after the
    second blip must NOT be pulled (the loop broke, it never saw it)."""
    runner = _Runner({})

    class IM:
        def __init__(self, runner):
            self.runner = runner

        def base_freshness(self, client):
            return {
                "a:1": {"files": [("docker/octra/Dockerfile", None)], "local_digests": set(),
                        "remote_digest": None, "error": "no route", "error_class": "transport", "status": "unknown"},
                "b:1": {"files": [("docker/octra/Dockerfile", None)], "local_digests": set(),
                        "remote_digest": None, "error": "no route", "error_class": "transport", "status": "unknown"},
                "c:1": {"files": [("docker/octra/Dockerfile", None)], "local_digests": set(),
                        "remote_digest": "sha256:z", "error": None, "error_class": None, "status": "stale"},
            }

    monkeypatch.setattr(images_mod, "ImageManager", IM)
    build_mod.refresh_base_images(runner, _OCTRA, ["octra"], client=object())
    out = capsys.readouterr().out
    assert "two consecutive" in out
    assert runner.pulled == []  # c:1 never reached