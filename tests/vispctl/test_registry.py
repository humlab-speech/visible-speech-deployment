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
            return _Resp(headers={"Docker-Content-Digest": "sha256:remote"})
        raise AssertionError(f"unexpected request {url}")


def _ref():
    return ImageRef("registry.example", "library/matomo", "5.8.0")


def test_client_fetches_token_once_and_follows_pagination():
    opener = _FakeOpener()
    client = RegistryClient(opener=opener)

    assert client.list_tags(_ref()) == ["5.7.0", "5.8.0", "5.14.0"]
    assert client.tag_digest(_ref()) == "sha256:remote"

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


# --- realm/pagination host trust boundary ---


@pytest.mark.parametrize(
    ("registry", "realm"),
    [
        # Shared providers: last-two-label matching made every tenant one host.
        ("mycorp.azurecr.io", "https://evil.azurecr.io/token"),
        ("abc123.dkr.ecr.eu-west-1.amazonaws.com", "https://evil.amazonaws.com/token"),
        ("safe.library.co.uk", "https://evil.co.uk/token"),
        ("192.168.2.10:5000", "https://10.0.2.10:5000/token"),
        # Scheme and netloc traps: Python and curl disagree on where '@' and
        # backslash put the host, so we refuse rather than pick a reading.
        ("registry.example", "http://auth.registry.example/token"),
        ("registry.example", "https://user:pw@evil.test/token"),
        # userinfo even when the visible host is the registry's: real registries
        # never do this, and other parsers may send the request elsewhere.
        ("registry.example", "https://token@registry.example/token"),
        ("registry.example", "https://evil.test\\@registry.example/token"),
        ("registry.example", "https://[broken/token"),
        # IPv6 host parsing: cutting the host at the first colon turned
        # "[::1]:5000" into "" — and every trailing-dot realm into a trusted
        # subdomain of the empty string.
        ("[::1]:5000", "https://evil.example./token"),
        # The Docker Hub token delegate is only ever consulted for Docker Hub.
        ("quay.io", "https://auth.docker.io/token"),
        # Ambiguous port: urllib would open docker.io:443 while a naive parse
        # sees another host:port pair; control chars diverge parsers likewise.
        ("docker.io", "https://docker.io:443:443/token"),
        ("docker.io", "https://docker.io/to\nken"),
    ],
)
def test_token_realm_outside_the_registry_is_refused(registry, realm):
    def opener(req, timeout=None):
        raise urllib.error.HTTPError(
            req.full_url, 401, "Unauthorized", {"WWW-Authenticate": f'Bearer realm="{realm}"'}, None
        )

    with pytest.raises(RegistryError, match="realm"):
        RegistryClient(opener=opener).list_tags(ImageRef(registry, "lib/app", "1"))


def test_token_realm_on_a_subdomain_of_the_registry_is_allowed():
    seen = []

    def opener(req, timeout=None):
        url = req.full_url
        seen.append(url)
        if url.startswith("https://auth.registry.example/token"):
            return _Resp(json.dumps({"token": "T"}).encode())
        if req.get_header("Authorization") != "Bearer T":
            raise urllib.error.HTTPError(
                url,
                401,
                "Unauthorized",
                {"WWW-Authenticate": 'Bearer realm="https://auth.registry.example/token",service="s"'},
                None,
            )
        return _Resp(json.dumps({"tags": ["1.0"]}).encode())

    assert RegistryClient(opener=opener).list_tags(_ref()) == ["1.0"]
    assert any(u.startswith("https://auth.registry.example/") for u in seen)


def test_docker_hub_token_delegate_is_allowed():
    """registry-1.docker.io challenges on auth.docker.io — the real Hub shape."""
    challenge = 'Bearer realm="https://auth.docker.io/token",service="registry.docker.io"'

    def opener(req, timeout=None):
        url = req.full_url
        if url.startswith("https://auth.docker.io/token"):
            return _Resp(json.dumps({"token": "T"}).encode())
        if req.get_header("Authorization") != "Bearer T":
            raise urllib.error.HTTPError(url, 401, "Unauthorized", {"WWW-Authenticate": challenge}, None)
        return _Resp(json.dumps({"tags": ["5.8.0"]}).encode())

    ref = ImageRef("registry-1.docker.io", "library/matomo", "5.8.0")
    assert RegistryClient(opener=opener).list_tags(ref) == ["5.8.0"]


def test_pagination_link_on_a_subdomain_of_the_registry_is_dropped():
    """Pages come from the exact registry host; a subdomain Link passes a
    loosened subdomain check and must still never be fetched."""

    def opener(req, timeout=None):
        url = req.full_url
        if url.startswith("https://registry.example/token"):
            return _Resp(json.dumps({"token": "T"}).encode())
        if url.startswith("https://tok.registry.example/"):
            raise AssertionError(f"client followed a subdomain pagination lure to {url}")
        if req.get_header("Authorization") != "Bearer T":
            raise urllib.error.HTTPError(
                url,
                401,
                "Unauthorized",
                {"WWW-Authenticate": 'Bearer realm="https://registry.example/token",service="s"'},
                None,
            )
        return _Resp(
            json.dumps({"tags": ["1.0"]}).encode(),
            {"Link": '<https://tok.registry.example/v2/lib/app/tags/list?n=2>; rel="next"'},
        )

    ref = ImageRef("registry.example", "lib/app", "1.0")
    assert RegistryClient(opener=opener).list_tags(ref) == ["1.0"]


@pytest.mark.parametrize(
    ("registry", "realm"),
    [
        ("[::1]:5000", "https://[::1]:5000/token"),  # its own IPv6 realm (host cut at ':' saw "")
        ("docker.io", "https://docker.io:443/token"),  # explicit default port is still itself
        ("docker.io", "https://DOCKER.IO/token"),  # host comparison is case-insensitive
    ],
)
def test_legit_realms_still_work(registry, realm):
    challenge = f'Bearer realm="{realm}"'

    def opener(req, timeout=None):
        url = req.full_url
        if "token" in url:
            return _Resp(json.dumps({"token": "T"}).encode())
        if req.get_header("Authorization") != "Bearer T":
            raise urllib.error.HTTPError(url, 401, "Unauthorized", {"WWW-Authenticate": challenge}, None)
        return _Resp(json.dumps({"tags": ["1"]}).encode())

    assert RegistryClient(opener=opener).list_tags(ImageRef(registry, "lib/app", "1")) == ["1"]


@pytest.mark.parametrize(
    "target",
    [
        "https://evil.test/v2/lib/app/tags/list?n=2",  # foreign host
        "https://auth.docker.io/v2/lib/app/tags/list?n=2",  # token delegate is not a page source
        "http://registry.example/v2/lib/app/tags/list?n=2",  # cleartext downgrade
    ],
)
def test_pagination_link_off_the_registry_is_dropped(target):
    """A hostile Link keeps the first page instead of handing the token to a stranger."""

    def opener(req, timeout=None):
        assert "/tags/list" in req.full_url and "n=2" not in req.full_url, f"client followed {target}"
        return _Resp(
            json.dumps({"tags": ["1.0"]}).encode(),
            {"Link": f'<{target}>; rel="next"'},
        )

    assert RegistryClient(opener=opener).list_tags(_ref()) == ["1.0"]


def test_pagination_link_on_the_registry_is_followed():
    def opener(req, timeout=None):
        if "n=2" in req.full_url:
            return _Resp(json.dumps({"tags": ["1.1"]}).encode())
        return _Resp(
            json.dumps({"tags": ["1.0"]}).encode(),
            {"Link": f'<https://{_ref().registry}/v2/lib/app/tags/list?n=2>; rel="next"'},
        )

    assert RegistryClient(opener=opener).list_tags(_ref()) == ["1.0", "1.1"]


# --- the real default opener (no injected fake) ---


def _serve(handler_cls):
    import http.server
    import threading

    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler_cls)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


# classes below need the module at test-body scope too
import http.server  # noqa: E402


def test_default_opener_is_callable_and_never_follows_redirects(monkeypatch):
    """The default opener is used for real here: 200 works, 302 raises, the target is never touched.

    no_proxy keeps the loopback servers out of a configured HTTP_PROXY
    (urllib proxies 127.0.0.1 by default); the opener itself stays the
    production default, which is the point of the test.
    """
    monkeypatch.setenv("no_proxy", "127.0.0.1,localhost")
    monkeypatch.setenv("NO_PROXY", "127.0.0.1,localhost")

    class Target(http.server.BaseHTTPRequestHandler):
        hits = 0

        def do_GET(self):
            type(self).hits += 1
            self.send_response(418)
            self.end_headers()

    class Source(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path == "/ok":
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b"hello")
            else:
                self.send_response(302)
                self.send_header("Location", f"http://127.0.0.1:{target.server_address[1]}/taken")
                self.end_headers()

    target = _serve(Target)
    source = _serve(Source)
    try:
        client = RegistryClient()  # production default, not a fake
        with client._open(urllib.request.Request(f"http://127.0.0.1:{source.server_address[1]}/ok")) as resp:
            assert resp.read() == b"hello"
        with pytest.raises(urllib.error.HTTPError):
            client._open(urllib.request.Request(f"http://127.0.0.1:{source.server_address[1]}/hop"))
        assert Target.hits == 0
    finally:
        source.shutdown()
        target.shutdown()


def test_pagination_link_on_the_token_delegate_is_dropped():
    """auth.docker.io answers 401s but never serves pages: a Link there is a lure."""

    def opener(req, timeout=None):
        url = req.full_url
        if url.startswith("https://auth.docker.io/token"):
            return _Resp(json.dumps({"token": "T"}).encode())
        if "auth.docker.io/v2" in url:
            raise AssertionError(f"client followed a pagination lure to {url}")
        if req.get_header("Authorization") != "Bearer T":
            raise urllib.error.HTTPError(
                url,
                401,
                "Unauthorized",
                {"WWW-Authenticate": 'Bearer realm="https://auth.docker.io/token",service="s"'},
                None,
            )
        return _Resp(
            json.dumps({"tags": ["5.8.0"]}).encode(),
            {"Link": '<https://auth.docker.io/v2/library/matomo/tags/list?n=2>; rel="next"'},
        )

    ref = ImageRef("registry-1.docker.io", "library/matomo", "5.8.0")
    assert RegistryClient(opener=opener).list_tags(ref) == ["5.8.0"]
