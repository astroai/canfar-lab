from __future__ import annotations

import json
from pathlib import Path

import pytest

from astroai_lab.version import PACKAGE_VERSION, display_version, version_info


def test_package_version_matches_pyproject() -> None:
    import tomllib

    pyproject = Path(__file__).resolve().parents[2] / "pyproject.toml"
    data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
    assert data["project"]["version"] == PACKAGE_VERSION


def test_display_version_without_direct_url(monkeypatch: pytest.MonkeyPatch) -> None:
    from importlib.metadata import PackageNotFoundError

    monkeypatch.setattr(
        "astroai_lab.version.distribution",
        lambda name: (_ for _ in ()).throw(PackageNotFoundError(name)),
    )
    assert display_version() == PACKAGE_VERSION
    info = version_info()
    assert info["commit"] is None
    assert info["display"] == PACKAGE_VERSION


def test_display_version_appends_git_commit(monkeypatch: pytest.MonkeyPatch) -> None:
    class Dist:
        def read_text(self, name: str) -> str | None:
            if name != "direct_url.json":
                return None
            return json.dumps(
                {
                    "url": "https://github.com/astroai/canfar-lab.git",
                    "vcs_info": {
                        "vcs": "git",
                        "commit_id": "2f7e99deaf6f82a0bf4027a39ca79397f735bd83",
                    },
                }
            )

    monkeypatch.setattr("astroai_lab.version.distribution", lambda name: Dist())
    assert display_version() == f"{PACKAGE_VERSION}+g2f7e99de"
    info = version_info()
    assert info["commit"] == "2f7e99deaf6f82a0bf4027a39ca79397f735bd83"
    assert info["display"] == f"{PACKAGE_VERSION}+g2f7e99de"
