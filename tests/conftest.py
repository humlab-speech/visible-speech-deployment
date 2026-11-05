"""Test isolation: the caller's git environment, and the network.

Git
---

Several test modules create nested repositories with plain `git` subprocesses
(and the code under test, GitRepository/DeployManager, does the same). When
pytest itself runs from a git hook — this repo's pre-commit `pytest` hook —
git exports GIT_DIR, GIT_WORK_TREE, GIT_INDEX_FILE and GIT_PREFIX into the
hook environment, and in a LINKED WORKTREE those are absolute paths: every
nested `git add`/`checkout`/`init` then operates on the invoking worktree's
own index and on the shared config (a leaked `git init` flips `core.bare`
there). This was reproduced live: one hooked commit wiped a worktree index
and marked the shared repo bare. Stripping the variables per test makes the
suite hermetic with respect to the caller's repository.

Network
-------
No test may reach a host other than loopback. Code such as the base-image
refresh treats an unreachable registry as "build from the cached base", so a
test that accidentally reached Docker Hub passed either way: online it made
real requests (and could wait out the 15 s timeout), offline it passed too.
The guard therefore does not rely on the code noticing the refusal: it
records every attempt and fails the test at teardown.
"""

import socket

import pytest

_GIT_HOOK_ENV_VARS = (
    "GIT_DIR",
    "GIT_WORK_TREE",
    "GIT_INDEX_FILE",
    "GIT_PREFIX",
    "GIT_COMMON_DIR",
    "GIT_OBJECT_DIRECTORY",
    "GIT_ALTERNATE_OBJECT_DIRECTORIES",
)


@pytest.fixture(autouse=True)
def _hermetic_git_env(monkeypatch):
    for var in _GIT_HOOK_ENV_VARS:
        monkeypatch.delenv(var, raising=False)


def _is_loopback(host) -> bool:
    if host is None:
        return True
    if isinstance(host, bytes):
        host = host.decode(errors="replace")
    host = str(host)
    return host in ("localhost", "::1") or host.startswith("127.")


@pytest.fixture(autouse=True)
def network_guard(monkeypatch):
    """Refuse non-loopback connections and fail the test that tried one.

    Yields the list of refused hosts; a test that provokes a refusal on purpose
    asserts on it and clears it.
    """
    attempts: list[str] = []
    real_connect = socket.socket.connect
    real_getaddrinfo = socket.getaddrinfo

    def connect(sock, address):
        if sock.family in (socket.AF_INET, socket.AF_INET6) and not _is_loopback(address[0]):
            attempts.append(str(address[0]))
            raise OSError(f"network access is blocked in tests: {address[0]}")
        return real_connect(sock, address)

    def getaddrinfo(host, *args, **kwargs):
        if not _is_loopback(host):
            attempts.append(str(host))
            raise socket.gaierror(f"network access is blocked in tests: {host}")
        return real_getaddrinfo(host, *args, **kwargs)

    monkeypatch.setattr(socket.socket, "connect", connect)
    monkeypatch.setattr(socket, "getaddrinfo", getaddrinfo)
    yield attempts
    if attempts:
        pytest.fail(
            f"test tried to reach the network: {', '.join(sorted(set(attempts)))} "
            "(stub the client, or serve on 127.0.0.1)",
            pytrace=False,
        )
