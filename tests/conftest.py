"""Isolation for the git-touching tests.

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
"""

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
