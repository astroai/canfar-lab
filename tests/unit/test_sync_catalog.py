from __future__ import annotations

from pathlib import Path

import pytest

from canfar_lab.errors import LabError
from canfar_lab.sync import catalog


def _workspace_toml(tmp_path: Path, *, extra_archived: bool = True) -> Path:
    body = """
[repositories."astroai/torchsky"]
lifecycle = "active"
remote_policy = "fork"

[repositories."astroai/retired"]
lifecycle = "archived"
"""
    if not extra_archived:
        body = body.split('[repositories."astroai/retired"]')[0]
    path = tmp_path / "workspace.toml"
    path.write_text(body.strip() + "\n", encoding="utf-8")
    return path


def test_load_catalog_explicit_repos_file(tmp_path):
    path = _workspace_toml(tmp_path)
    repos = catalog.load_catalog(tmp_path / "root", repos_file=path)
    assert repos == ["astroai/torchsky"]


def test_load_catalog_missing_repos_file_raises(tmp_path):
    with pytest.raises(LabError):
        catalog.load_catalog(tmp_path, repos_file=tmp_path / "nope.toml")


def test_load_catalog_empty_repos_file_raises(tmp_path):
    path = tmp_path / "workspace.toml"
    path.write_text("[settings]\ncolor = 'blue'\n", encoding="utf-8")
    with pytest.raises(LabError):
        catalog.load_catalog(tmp_path, repos_file=path)


def test_load_catalog_workspace_toml_merges_clones(tmp_path):
    root = tmp_path / "src"
    root.mkdir()
    _workspace_toml(root)
    (root / "astroai" / "torchsky" / ".git").mkdir(parents=True)
    (root / "sfabbro" / "msa" / ".git").mkdir(parents=True)
    repos = catalog.load_catalog(root)
    assert repos[0] == "astroai/torchsky"
    assert "sfabbro/msa" in repos
    assert "astroai/retired" not in repos


def test_load_catalog_defaults_without_workspace(tmp_path):
    root = tmp_path / "pure"
    root.mkdir()
    assert catalog.load_catalog(root) == catalog.DEFAULT_REPOSITORIES


def test_parse_toml_fallback(tmp_path):
    path = _workspace_toml(tmp_path)
    data = catalog._parse_toml_fallback(path)
    assert data["repositories"]["astroai/torchsky"]["lifecycle"] == "active"
    assert data["repositories"]["astroai/retired"]["lifecycle"] == "archived"


def test_normalize_selection_short_names_and_dedupe():
    cat = ["astroai/torchsky", "astroai/uspm", "sfabbro/msa"]
    assert catalog.normalize_selection(cat, None) == cat
    assert catalog.normalize_selection(cat, ["torchsky", "torchsky"]) == ["astroai/torchsky"]
    assert catalog.normalize_selection(cat, ["torchsky", "sfabbro/msa"]) == [
        "astroai/torchsky",
        "sfabbro/msa",
    ]
    # Unknown selections pass through (bootstrap uses them to clone).
    assert catalog.normalize_selection(cat, ["brand/new"]) == ["brand/new"]


def test_repo_path_layout(tmp_path):
    assert catalog.repo_path(tmp_path, "astroai/x") == tmp_path / "astroai" / "x"
    assert catalog.repo_path(tmp_path, "flat") == tmp_path / "flat"


def test_scan_clones_skips_hidden_and_plain_dirs(tmp_path):
    (tmp_path / "astroai" / "a" / ".git").mkdir(parents=True)
    (tmp_path / "astroai" / "plain").mkdir()
    (tmp_path / ".cache" / "b" / ".git").mkdir(parents=True)
    assert catalog.scan_clones(tmp_path) == ["astroai/a"]
