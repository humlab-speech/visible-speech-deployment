"""Tests for vispctl.registry — image refs, version tags, and the registry client."""

import io
import json
import urllib.error

import pytest

from vispctl.registry import ImageRef, RegistryClient, RegistryError, is_version_tag, newer_versions

# --- ImageRef.parse ---


@pytest.mark.parametrize(
    "ref, expected",
    [
        ("docker.io/library/matomo:5.8.0", ("registry-1.docker.io", "library/matomo", "5.8.0", None)),
        ("mongo:6.0.27", ("registry-1.docker.io", "library/mongo", "6.0.27", None)),
        ("docker.io/cirrusid/simplesamlphp:v2.4.2", ("registry-1.docker.io", "cirrusid/simplesamlphp", "v2.4.2", None)),
        ("quay.io/jupyter/datascience-notebook:r-4.5.2", ("quay.io", "jupyter/datascience-notebook", "r-4.5.2", None)),
        ("localhost:5000/team/app:1.2", ("localhost:5000", "team/app", "1.2", None)),
        ("nginx", ("registry-1.docker.io", "library/nginx", "latest", None)),
        ("docker.io/library/nginx@sha256:abc", ("registry-1.docker.io", "library/nginx", None, "sha256:abc")),
        ("nginx:1.30.0@sha256:abc", ("registry-1.docker.io", "library/nginx", "1.30.0", "sha256:abc")),
    ],
)
def test_image_ref_parse(ref, expected):
    r = ImageRef.parse(ref)
    assert (r.registry, r.repository, r.tag, r.digest) == expected


# --- newer_versions ---

MATOMO_TAGS = ["5", "5.8", "5.8.0", "5.8.0-fpm", "5.9.0", "5.14.0", "5.14.0-apache", "5.14", "latest", "fpm-alpine"]


def test_newer_versions_within_major_compares_numerically():
    """5.14.0 beats 5.9.0 (numeric, not lexical); moving and variant tags are ignored."""
    assert newer_versions("5.8.0", MATOMO_TAGS) == ("5.14.0", None)


def test_newer_versions_reports_new_major_separately():
    tags = ["6.0.27", "6.0.28", "7.0.5", "8.0.20", "8.0.20-rc1", "9.0.4", "9", "9.0"]
    assert newer_versions("6.0.27", tags) == ("6.0.28", "9.0.4")


def test_newer_versions_skips_prereleases():
    assert newer_versions("8.0.0", ["8.0.1-rc1", "9.0.0-rc0"]) == (None, None)


def test_newer_versions_follows_variant_numbers():
    """A base-image bump in the suffix (alpine3.23 → 3.24) still compares, same shape."""
    tags = ["1.30.0-alpine3.23", "1.30.1-alpine3.24", "1.30.1", "1.30.1-alpine"]
    assert newer_versions("1.30.0-alpine3.23", tags) == ("1.30.1-alpine3.24", None)


def test_newer_versions_keeps_v_prefix_apart():
    assert newer_versions("v2.4.2", ["v2.5.0", "2.6.0", "v3.0.0"]) == ("v2.5.0", "v3.0.0")


def test_newer_versions_none_when_current_is_newest():
    assert newer_versions("5.14.0", MATOMO_TAGS) == (None, None)


@pytest.mark.parametrize("tag", ["latest", "bookworm", "stable-alpine", None])
def test_newer_versions_non_version_tags(tag):
    assert newer_versions(tag, MATOMO_TAGS) == (None, None)
    assert not is_version_tag(tag)


# --- RegistryClient ---


class _Resp(io.BytesIO):
    def __init__(self, body=b"", headers=None):
        super().__init__(body)
        self.headers = headers or {}


class _FakeOpener:
    """Simulates a registry that wants a bearer token and paginates tags."""

    def __init__(self):
        self.requests = []

    def __call__(self, req, timeout=None):
        url = req.full_url
        auth = req.get_header("Authorization")
        self.requests.append((req.get_method(), url, auth))

        if url.startswith("https://auth.registry.example/token"):
            assert "service=registry.example" in url and "scope=repository%3Alibrary%2Fmatomo%3Apull" in url
            return _Resp(json.dumps({"token": "T"}).encode())
        if auth != "Bearer T":
            challenge = 'Bearer realm="https://auth.registry.example/token",service="registry.example",'
            challenge += 'scope="repository:library/matomo:pull"'
            raise urllib.error.HTTPError(url, 401, "Unauthorized", {"WWW-Authenticate": challenge}, None)
        if url.endswith("/tags/list"):
            link = '</v2/library/matomo/tags/list?last=5.8.0&n=2>; rel="next"'
            return _Resp(json.dumps({"tags": ["5.7.0", "5.8.0"]}).encode(), {"Link": link})
        if "last=5.8.0" in url:
            return _Resp(json.dumps({"tags": ["5.14.0"]}).encode())
        if "/manifests/5.8.0" in url:
            assert req.get_method() == "HEAD"
            assert "application/vnd.oci.image.index.v1+json" in req.get_header("Accept")
            return _Resp(headers={"Docker-Content-Digest": REAL_DIGEST})
        raise AssertionError(f"unexpected request {url}")


def _ref():
    return ImageRef("registry.example", "library/matomo", "5.8.0")


REAL_DIGEST = "sha256:" + "191c9f00" * 8  # a real digest is 64 hex after the algorithm


def test_client_fetches_token_once_and_follows_pagination():
    opener = _FakeOpener()
    client = RegistryClient(opener=opener)

    assert client.list_tags(_ref()) == ["5.7.0", "5.8.0", "5.14.0"]
    assert client.tag_digest(_ref()) == REAL_DIGEST

    token_requests = [r for r in opener.requests if r[1].startswith("https://auth.registry.example/")]
    assert len(token_requests) == 1  # cached for the second page and the digest
    assert opener.requests[-1][0] == "HEAD"


def test_client_wraps_network_errors():
    def opener(req, timeout=None):
        raise urllib.error.URLError("no route to host")

    with pytest.raises(RegistryError, match="cannot reach registry.example"):
        RegistryClient(opener=opener).list_tags(_ref())


def test_client_reports_http_errors():
    def opener(req, timeout=None):
        raise urllib.error.HTTPError(req.full_url, 404, "Not Found", {}, None)

    with pytest.raises(RegistryError, match="HTTP 404"):
        RegistryClient(opener=opener).tag_digest(_ref())


# --- Registry boundary: where a challenge or a Link may send the client (F5) ---


def _challenge(realm: str) -> dict:
    return {
        "WWW-Authenticate": f'Bearer realm="{realm}",service="registry.example",'
        'scope="repository:library/matomo:pull"'
    }


def _challenging_client(realm: str):
    """A registry that asks for a token from *realm* and otherwise answers normally."""

    def opener(req, timeout=None):
        url = req.full_url
        if url.startswith(realm):
            return _Resp(json.dumps({"token": "T"}).encode())
        if req.get_header("Authorization") != "Bearer T":
            raise urllib.error.HTTPError(url, 401, "Unauthorized", _challenge(realm), None)
        return _Resp(json.dumps({"tags": ["5.8.0"]}).encode())

    return RegistryClient(opener=opener)


def test_a_token_realm_may_live_on_the_registrys_own_auth_host():
    """The real Docker Hub shape: API on registry-1.docker.io, token on auth.docker.io."""
    client = _challenging_client("https://auth.docker.io/token")
    ref = ImageRef("registry-1.docker.io", "library/matomo", "5.8.0")

    assert client.list_tags(ref) == ["5.8.0"]


@pytest.mark.parametrize(
    "realm",
    [
        "https://evil.example/token",  # a different operator's domain
        "http://auth.registry.example/token",  # the right domain, but cleartext
    ],
)
def test_a_token_realm_off_the_registrys_domain_is_refused(realm):
    """A challenge may not route the pull intent (and the token it buys) elsewhere."""
    with pytest.raises(RegistryError, match="realm"):
        _challenging_client(realm).list_tags(_ref())


def test_pagination_stops_at_a_link_off_the_registry():
    """An absolute Link to someone else's host ends pagination; it is never followed."""

    def opener(req, timeout=None):
        url = req.full_url
        assert url == f"https://registry.example/v2/{'library/matomo'}/tags/list", f"followed a link to {url}"
        return _Resp(
            json.dumps({"tags": ["5.7.0"]}).encode(),
            {"Link": '<https://evil.example/v2/library/matomo/tags/list?last=5.7.0>; rel="next"'},
        )

    assert RegistryClient(opener=opener).list_tags(_ref()) == ["5.7.0"]


def test_an_unusable_digest_header_is_refused():
    """A digest header is outside input: shape-check it before it becomes a verdict or a terminal write."""

    def opener(req, timeout=None):
        return _Resp(headers={"Docker-Content-Digest": "sha256:remote\x1b]0;owned\x07"})

    with pytest.raises(RegistryError, match="unusable digest"):
        RegistryClient(opener=opener).tag_digest(_ref())
