"""DeployManager anchors to the repo root, not the caller's CWD (w2 HIGH)."""

import os
import sys
from pathlib import Path

import pytest

proj = str(Path(__file__).resolve().parents[2])
if proj not in sys.path:
    sys.path.insert(0, proj)

import vispctl
from vispctl.deploy import DeployManager


def test_default_basedir_is_repo_root_not_cwd(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    dm = DeployManager()
    expected = Path(vispctl.__file__).resolve().parents[1]
    assert dm.basedir == expected
    assert dm.external_dir == expected / "external"
    assert Path(dm.config.path if hasattr(dm.config, "path") else expected).resolve() != Path(tmp_path)


def test_explicit_basedir_still_wins(tmp_path):
    dm = DeployManager(basedir=str(tmp_path))
    assert dm.basedir == tmp_path
