"""Read-only access to public container registries, for third-party images.

The quadlets pin registry images by tag (matomo:5.8.0, mongo:6.0.27). Two
things can make such a pin out of date:

- a newer version exists under another tag, and
- the registry rebuilt the *same* tag (official images are rebuilt whenever
  their base image gets a security fix), so the digest moved while the tag
  did not.

This module answers both from the registry's HTTP API (OCI distribution
spec): ``list_tags()`` for the first, ``tag_digest()`` for the second.
Only anonymous access is supported, which is all public images need.

Digests: for a multi-arch tag, ``tag_digest()`` returns the digest of the
image *index*. Podman records that same digest in the image's RepoDigests
when it pulls by tag, so the two can be compared directly.
"""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Callable, Iterable

DOCKER_HUB_API = "registry-1.docker.io"

# Ask for the index first so multi-arch tags report the digest podman stores.
_MANIFEST_ACCEPT = ", ".join(
    [
        "application/vnd.oci.image.index.v1+json",
        "application/vnd.docker.distribution.manifest.list.v2+json",
        "application/vnd.oci.image.manifest.v1+json",
        "application/vnd.docker.distribution.manifest.v2+json",
    ]
)


class RegistryError(Exception):
    """The registry could not be reached or gave an unusable answer."""


@dataclass(frozen=True)
class ImageRef:
    """A parsed image reference such as ``docker.io/library/matomo:5.8.0``."""

    registry: str  # host the API lives on (docker.io → registry-1.docker.io)
    repository: str  # library/matomo
    tag: str | None
    digest: str | None = None  # set when the reference is pinned by @sha256:…

    @classmethod
    def parse(cls, ref: str) -> "ImageRef":
        name, _, digest = ref.partition("@")
        tag = None
        # A ':' after the last '/' separates the tag; one before it is a registry port.
        if ":" in name.rsplit("/", 1)[-1]:
            name, tag = name.rsplit(":", 1)

        first, _, rest = name.partition("/")
        if rest and ("." in first or ":" in first or first == "localhost"):
            registry, repository = first, rest
        else:
            registry, repository = "docker.io", name

        if registry in ("docker.io", "index.docker.io"):
            registry = DOCKER_HUB_API
            if "/" not in repository:
                repository = f"library/{repository}"

        if not digest and tag is None:
            tag = "latest"
        return cls(registry, repository, tag, digest or None)


class RegistryClient:
    """Minimal anonymous client for the registry v2 API.

    Bearer tokens are fetched on the first 401 and cached per repository.
    Uses HEAD for digests: on Docker Hub, HEAD requests do not count against
    the anonymous pull rate limit.
    """

    def __init__(self, timeout: float = 15.0, opener: Callable = urllib.request.urlopen):
        self.timeout = timeout
        self._open = opener
        self._tokens: dict[tuple[str, str], str] = {}

    def list_tags(self, ref: ImageRef) -> list[str]:
        """Return every tag in the repository, following pagination."""
        tags: list[str] = []
        url = f"https://{ref.registry}/v2/{ref.repository}/tags/list"
        while url:
            with self._request(ref, url) as resp:
                try:
                    tags.extend(json.load(resp).get("tags") or [])
                except ValueError as e:
                    raise RegistryError(f"invalid tag list from {ref.registry}: {e}") from e
                url = _next_link(resp.headers.get("Link"), ref.registry)
        return tags

    def tag_digest(self, ref: ImageRef, tag: str | None = None) -> str:
        """Return the digest the registry currently serves for *tag* (default: ref's own tag)."""
        tag = tag or ref.tag
        url = f"https://{ref.registry}/v2/{ref.repository}/manifests/{tag}"
        with self._request(ref, url, method="HEAD", headers={"Accept": _MANIFEST_ACCEPT}) as resp:
            digest = resp.headers.get("Docker-Content-Digest")
        if not digest:
            raise RegistryError(f"{ref.registry} sent no digest for {ref.repository}:{tag}")
        return digest

    def _request(self, ref: ImageRef, url: str, method: str = "GET", headers: dict | None = None):
        key = (ref.registry, ref.repository)
        for attempt in range(2):
            hdrs = dict(headers or {})
            if key in self._tokens:
                hdrs["Authorization"] = f"Bearer {self._tokens[key]}"
            try:
                return self._open(urllib.request.Request(url, headers=hdrs, method=method), timeout=self.timeout)
            except urllib.error.HTTPError as e:
                if e.code == 401 and attempt == 0 and key not in self._tokens:
                    self._tokens[key] = self._fetch_token(e.headers.get("WWW-Authenticate", ""))
                    continue
                raise RegistryError(f"{ref.registry} answered HTTP {e.code} for {ref.repository}") from e
            except (urllib.error.URLError, OSError) as e:
                reason = getattr(e, "reason", e)
                raise RegistryError(f"cannot reach {ref.registry}: {reason}") from e
        raise RegistryError(f"{ref.registry} refused anonymous access to {ref.repository}")

    def _fetch_token(self, challenge: str) -> str:
        """Exchange a ``WWW-Authenticate: Bearer realm=…`` challenge for an anonymous token."""
        if not challenge.lower().startswith("bearer "):
            raise RegistryError(f"unsupported registry auth challenge: {challenge or '(none)'}")
        params = dict(re.findall(r'(\w+)="([^"]*)"', challenge))
        realm = params.pop("realm", None)
        if not realm:
            raise RegistryError(f"registry auth challenge has no realm: {challenge}")
        url = f"{realm}?{urllib.parse.urlencode(params)}"
        try:
            with self._open(urllib.request.Request(url), timeout=self.timeout) as resp:
                body = json.load(resp)
        except (urllib.error.URLError, OSError, ValueError) as e:
            raise RegistryError(f"could not get a registry token from {realm}: {e}") from e
        token = body.get("token") or body.get("access_token")
        if not token:
            raise RegistryError(f"no token in the answer from {realm}")
        return token


def _next_link(link: str | None, registry: str) -> str | None:
    """Return the absolute URL of a ``Link: <…>; rel="next"`` header, if any."""
    if not link:
        return None
    m = re.search(r'<([^>]+)>\s*;\s*rel="?next"?', link)
    if not m:
        return None
    return urllib.parse.urljoin(f"https://{registry}/", m.group(1))


# --- Version tags ---------------------------------------------------------

_VERSION_TAG = re.compile(r"^(v?)(\d+(?:\.\d+)*)(.*)$")


def _version_key(tag: str):
    """Split a version tag into (shape, version, variant numbers), or None.

    Tags only compare when they have the same shape: the same prefix, the
    same number of version components and the same variant suffix with its
    numbers blanked out. So ``5.8.0`` compares with ``5.14.0`` but not with
    ``5.14`` (a moving tag) or ``5.14.0-fpm`` (another variant), and
    ``1.30.0-alpine3.23`` compares with ``1.30.1-alpine3.24``. Pre-releases
    (``8.0.0-rc1``) have a different suffix, so a stable pin never sees them.
    """
    m = _VERSION_TAG.match(tag)
    if not m:
        return None
    prefix, version, suffix = m.groups()
    numbers = tuple(int(n) for n in version.split("."))
    shape = (prefix, len(numbers), re.sub(r"\d+", "#", suffix))
    variant = tuple(int(n) for n in re.findall(r"\d+", suffix))
    return shape, numbers, variant


def is_version_tag(tag: str | None) -> bool:
    return bool(tag) and _version_key(tag) is not None


def newest_by_major(current: str | None, tags: Iterable[str]) -> dict[int, str]:
    """Map each major version to its newest tag that is newer than *current*.

    Only tags of the same shape as *current* count (see ``_version_key``).
    Empty when nothing newer exists or *current* is not a version tag.
    """
    cur = _version_key(current) if current else None
    if cur is None:
        return {}
    shape, numbers, variant = cur

    best: dict[int, tuple] = {}
    for tag in tags:
        key = _version_key(tag)
        if key is None or key[0] != shape:
            continue
        rank = (key[1], key[2])
        if rank <= (numbers, variant):
            continue
        major = key[1][0]
        if major not in best or rank > best[major][0]:
            best[major] = (rank, tag)
    return {major: tag for major, (_, tag) in sorted(best.items())}


def newer_versions(current: str | None, tags: Iterable[str]) -> tuple[str | None, str | None]:
    """Return (newest tag in the current major, newest tag in a later major).

    Either is None when nothing newer exists, or when *current* is not a
    version tag at all (``latest``, ``bookworm``).
    """
    by_major = newest_by_major(current, tags)
    if not by_major:
        return None, None
    current_major = _version_key(current)[1][0]
    later = [tag for major, tag in by_major.items() if major > current_major]
    return by_major.get(current_major), (later[-1] if later else None)


def major_of(tag: str | None) -> int | None:
    key = _version_key(tag) if tag else None
    return key[1][0] if key else None


def is_older(tag: str, than: str | None) -> bool:
    """True if *tag* is a lower version than *than* (both of the same shape)."""
    a, b = _version_key(tag), _version_key(than) if than else None
    return bool(a and b and a[0] == b[0] and (a[1], a[2]) < (b[1], b[2]))
