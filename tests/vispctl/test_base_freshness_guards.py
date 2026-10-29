"""Per-class wording lines and streak-reset placement, pinned line-exact.

The grouped wording assertions elsewhere only require phrases to appear
somewhere in a mixed run, which lets a gone/credentials wording swap, a
refused result feeding the transport streak, a status reset in the wrong
place, and display-layer mislabelling all pass. These guards assert per
image line and cover the display path too.
"""

import pytest

import vispctl.build as build_mod
import vispctl.images as images_mod
from test_base_freshness import _OCTRA, _Runner, _err_statuses


def _line_for(out, img):
    """The single report line refresh_base_images printed for *img*."""
    hits = [ln for ln in out.splitlines() if f"{img}:" in ln]
    assert len(hits) == 1, (img, out)
    return hits[0]


def test_each_error_class_prints_its_own_wording(monkeypatch, capsys):
    """M1 guard: a 404 line must not speak of credentials, and vice versa."""
    runner = _Runner({})
    monkeypatch.setattr(
        images_mod,
        "ImageManager",
        _err_statuses({
            "a:1": ("HTTP 401", "auth"),
            "b:1": ("HTTP 404", "gone"),
            "c:1": ("no route", "transport"),
            "d:1": ("realm outside registry", "refused"),
        }),
    )
    build_mod.refresh_base_images(runner, _OCTRA, ["octra"], client=object())
    out = capsys.readouterr().out
    assert "credentials" in _line_for(out, "a:1")
    assert "not at the registry" not in _line_for(out, "a:1")
    assert "not at the registry" in _line_for(out, "b:1")
    assert "credentials" not in _line_for(out, "b:1")
    assert "not reachable" in _line_for(out, "c:1")
    assert "REFUSED" in _line_for(out, "d:1")
    assert "cached" not in _line_for(out, "d:1")  # refused is never an outage


def _mixed(specs):
    """specs: [(image, status, error, error_class)] in sweep (sorted) order."""

    class IM:
        def __init__(self, runner):
            self.runner = runner

        def base_freshness(self, client):
            return {
                img: {
                    "files": [("docker/octra/Dockerfile", None)],
                    "local_digests": set(),
                    "remote_digest": "sha256:z" if status in ("stale", "current") else None,
                    "error": err,
                    "error_class": klass,
                    "status": status,
                }
                for img, status, err, klass in specs
            }

    return IM


def test_refused_does_not_feed_the_transport_streak(monkeypatch, capsys):
    """M2 guard: a refusal is an ANSWERED question, so refused -> blip -> stale still pulls."""
    runner = _Runner({})
    monkeypatch.setattr(
        images_mod,
        "ImageManager",
        _mixed([
            ("a:1", "unknown", "realm outside registry", "refused"),
            ("b:1", "unknown", "no route", "transport"),
            ("c:1", "stale", None, None),
        ]),
    )
    build_mod.refresh_base_images(runner, _OCTRA, ["octra"], client=object())
    out = capsys.readouterr().out
    assert "two consecutive" not in out, out
    assert runner.pulled == ["docker.io/library/c:1"], out


def test_refused_then_two_blips_stops_only_at_two(monkeypatch, capsys):
    """The other edge: refused must not count, but the two real blips still stop."""
    runner = _Runner({})
    monkeypatch.setattr(
        images_mod,
        "ImageManager",
        _mixed([
            ("a:1", "unknown", "realm outside registry", "refused"),
            ("b:1", "unknown", "no route", "transport"),
            ("c:1", "unknown", "no route", "transport"),
            ("d:1", "stale", None, None),
        ]),
    )
    build_mod.refresh_base_images(runner, _OCTRA, ["octra"], client=object())
    out = capsys.readouterr().out
    assert "two consecutive" in out
    assert runner.pulled == []  # d:1 sits behind the stop


@pytest.mark.parametrize("answered", ["current", "stale", "absent"])
def test_answered_by_status_resets_the_streak(monkeypatch, capsys, answered):
    """M4 guard: current/stale/absent are answers too — blip, answer, blip, stale still pulls."""
    runner = _Runner({})
    monkeypatch.setattr(
        images_mod,
        "ImageManager",
        _mixed([
            ("a:1", "unknown", "no route", "transport"),
            ("b:1", answered, None, None),
            ("c:1", "unknown", "no route", "transport"),
            ("d:1", "stale", None, None),
        ]),
    )
    build_mod.refresh_base_images(runner, _OCTRA, ["octra"], client=object())
    out = capsys.readouterr().out
    assert "two consecutive" not in out, out
    pulled_b = answered in ("stale", "absent")
    want = (["docker.io/library/b:1"] if pulled_b else []) + ["docker.io/library/d:1"]
    assert runner.pulled == want, out


def test_first_error_does_not_abort_the_sweep(monkeypatch, capsys):
    """M3 guard: one answered failure must not silence the bases behind it."""
    runner = _Runner({})
    monkeypatch.setattr(
        images_mod,
        "ImageManager",
        _mixed([
            ("a:1", "unknown", "HTTP 404", "gone"),
            ("b:1", "unknown", "HTTP 401", "auth"),
            ("c:1", "stale", None, None),
            ("d:1", "absent", None, None),
        ]),
    )
    build_mod.refresh_base_images(runner, _OCTRA, ["octra"], client=object())
    out = capsys.readouterr().out
    assert "not at the registry" in out and "credentials" in out
    assert runner.pulled == ["docker.io/library/c:1", "docker.io/library/d:1"], out


# ---- M6: display layer (images base) had NO test at all -------------------

def _fresh(out):
    """The freshness lines display_base_images printed (ANSI stripped)."""
    import re

    body = re.sub(r"\x1b\[[0-9;]*m", "", out).split("=== Summary ===")[0]
    return [ln for ln in body.splitlines() if ln.startswith("     ") or "STALE" in ln]


def _display(tmp_path, monkeypatch, capsys, image, digests=None, exc=None, remote=None):
    """Run images.display_base_images() over one Dockerfile with one base."""

    class Client:
        def tag_digest(self, ref):
            if exc is not None:
                raise exc
            return remote

    monkeypatch.setattr(images_mod, "get_config", lambda: type("C", (), {"project_dir": tmp_path})())
    monkeypatch.setattr(images_mod, "RegistryClient", lambda *a, **k: Client())
    dockerfile = tmp_path / "docker" / "octra" / "Dockerfile"
    dockerfile.parent.mkdir(parents=True, exist_ok=True)
    dockerfile.write_text(f"FROM {image}\n")
    im = images_mod.ImageManager(_Runner(digests or {}))
    im.display_base_images(check_remote=True)
    out = capsys.readouterr().out
    return out, _fresh(out)


@pytest.mark.parametrize(
    "exc,want,never",
    [
        (images_mod.RegistryTransportError("no route"), "not reachable", ["credentials", "REFUSED", "not at the registry"]),
        (images_mod.RegistryHTTPStatusError("HTTP 404", 404), "not at the registry", ["credentials", "REFUSED", "not reachable"]),
        (images_mod.RegistryHTTPStatusError("HTTP 401", 401), "needs credentials", ["not at the registry", "REFUSED", "not reachable"]),
        (images_mod.RegistryTrustError("realm outside registry"), "REFUSED", ["not reachable", "credentials", "cached"]),
    ],
)
def test_display_base_images_prints_one_line_per_error_class(tmp_path, monkeypatch, capsys, exc, want, never):
    """M6 guard: the four classes must stay four distinct lines in 'images base'."""
    out, lines = _display(tmp_path, monkeypatch, capsys, "octra:1.0", exc=exc)
    assert any(want in ln for ln in lines), out
    for bad in never:
        assert not any(bad in ln for ln in lines), out


def test_display_base_images_status_lines(tmp_path, monkeypatch, capsys):
    """current / stale / absent each get their own line too (display layer had none)."""
    out, lines = _display(tmp_path, monkeypatch, capsys, "octra:1.0", remote="sha256:aaa",
                         digests={"octra:1.0": ["octra@sha256:aaa"]})
    # pin the substance, not the word 'current': the line must claim the registry
    # was asked and agreed, which a bare 'current' substring would not prove
    assert any("registry serves the digest we hold" in ln for ln in lines), out
    assert not any("not consulted" in ln or "NOT verified" in ln for ln in lines), out

    capsys.readouterr()
    out, lines = _display(tmp_path, monkeypatch, capsys, "octra:1.0", remote="sha256:bbb",
                         digests={"octra:1.0": ["octra@sha256:aaa"]})
    assert any("STALE" in ln for ln in lines), out
    # the Summary must agree with the detail line: count it, point at build,
    # and never print the green 'all pinned' line next to a stale base
    assert "Stale bases: 1" in out and "./visp.py build" in out, out
    assert "All image tags are pinned" not in out, out

    capsys.readouterr()
    out, lines = _display(tmp_path, monkeypatch, capsys, "octra:2.0", remote="sha256:ccc")
    assert any("not local yet" in ln for ln in lines), out
    assert "Stale bases" not in out, out  # absent is not stale
    assert "All image tags are pinned" in out, out


def test_display_base_images_offline_line_says_it_asked_nothing(tmp_path, monkeypatch, capsys):
    """AGENTS.md quotes this line verbatim; pin it so a reword cannot silently stale the doc.

    --offline must not even construct a registry client, let alone ask one.
    """

    def no_client(*a, **k):
        raise AssertionError("--offline built a RegistryClient")

    monkeypatch.setattr(images_mod, "get_config", lambda: type("C", (), {"project_dir": tmp_path})())
    monkeypatch.setattr(images_mod, "RegistryClient", no_client)
    dockerfile = tmp_path / "docker" / "octra" / "Dockerfile"
    dockerfile.parent.mkdir(parents=True, exist_ok=True)
    dockerfile.write_text("FROM octra:1.0\n")

    images_mod.ImageManager(_Runner({"octra:1.0": ["octra@sha256:aaa"]})).display_base_images(check_remote=False)
    out = capsys.readouterr().out
    lines = _fresh(out)
    assert any("unknown (registry not consulted: --offline)" in ln for ln in lines), out
    assert not any("current" in ln or "STALE" in ln for ln in lines), out


def test_consumer_reads_the_class_key_from_real_entries(tmp_path, monkeypatch, capsys):
    """The entry-shape pin only pushes a clean entry through the consumer.

    Its docstring claims a rename on either side must not ship green, but a
    consumer-only rename of the error_class key stays green through it because
    no errored entry ever reaches refresh_base_images there. This does that.
    """
    from vispctl.registry import RegistryHTTPStatusError

    from test_base_freshness import _Runner

    monkeypatch.setattr(images_mod, "get_config", lambda: type("C", (), {"project_dir": tmp_path})())
    dockerfile = tmp_path / "docker" / "octra" / "Dockerfile"
    dockerfile.parent.mkdir(parents=True, exist_ok=True)
    dockerfile.write_text("FROM octra:1.0\n")

    class Client:
        def tag_digest(self, ref):
            raise RegistryHTTPStatusError("octra registry answered HTTP 401", 401)

    real = images_mod.ImageManager(_Runner({}))
    assert real.base_freshness(Client())["octra:1.0"]["error_class"] == "auth"  # producer side
    pull_runner = _Runner({})
    build_mod.refresh_base_images(pull_runner, {"octra": {"context": "./docker/octra"}}, ["octra"], client=Client())
    out = capsys.readouterr().out
    assert "needs credentials" in out, out
    assert "answered oddly" not in out, out


def test_an_answer_between_two_blips_does_not_stop_the_probe_sweep(tmp_path, monkeypatch):
    """base_freshness' own reset: blip, answered, blip -> the next base is still asked."""
    from vispctl.registry import RegistryTransportError

    monkeypatch.setattr(images_mod, "get_config", lambda: type("C", (), {"project_dir": tmp_path})())
    (tmp_path / "docker" / "x").mkdir(parents=True)
    (tmp_path / "docker" / "x" / "Dockerfile").write_text(
        "FROM reg.example/a:1\nFROM reg.example/b:1\nFROM reg.example/c:1\nFROM reg.example/d:1\n"
    )

    calls = []

    class Client:
        def tag_digest(self, ref):
            calls.append(ref.repository)
            if ref.repository in ("a", "c"):
                raise RegistryTransportError("blip")
            return "sha256:aaaa"

    st = images_mod.ImageManager(_Runner({})).base_freshness(Client())
    assert calls == ["a", "b", "c", "d"]
    assert st["reg.example/d:1"]["error_class"] is None  # answered, not skipped


def test_display_skipped_marker_keeps_its_own_wording(tmp_path, monkeypatch, capsys):
    """The not-checked marker must not borrow any other class's sentence."""

    forbidden = ("not reachable", "credentials", "REFUSED", "not at the registry", "answered oddly")
    monkeypatch.setattr(images_mod, "get_config", lambda: type("C", (), {"project_dir": tmp_path})())
    (tmp_path / "docker" / "x").mkdir(parents=True)
    (tmp_path / "docker" / "x" / "Dockerfile").write_text(
        "FROM reg.example/a:1\nFROM reg.example/b:1\nFROM reg.example/c:1\n"
    )

    class Client:
        def __init__(self):
            self.n = 0

        def tag_digest(self, ref):
            from vispctl.registry import RegistryTransportError

            self.n += 1
            raise RegistryTransportError("no route to host")

    monkeypatch.setattr(images_mod, "RegistryClient", lambda *a, **k: Client())
    images_mod.ImageManager(_Runner({})).display_base_images(check_remote=True)
    out = capsys.readouterr().out
    skipped_lines = [ln for ln in out.splitlines() if "not checked (two unreachable" in ln]
    assert skipped_lines, out
    assert not any(word in ln for ln in skipped_lines for word in forbidden), skipped_lines
