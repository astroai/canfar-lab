from __future__ import annotations

import os
from pathlib import Path

import pytest

from astroai_lab.config.settings import get_settings

CANFAR_SKILLS_SRC = (
    Path("/data/src/canfar-skills")
    if Path("/data/src/canfar-skills").is_dir()
    else Path(__file__).resolve().parent / "fixtures" / "canfar-skills"
)


def mock_canfar_skills_upstream(monkeypatch: pytest.MonkeyPatch) -> None:
    """Use local canfar-skills tree or test fixtures instead of GitHub."""
    import shutil

    from astroai_lab.agent import addons as addons_mod

    def _fake_refresh(cache_root: Path, repo: str, paths):  # noqa: ANN001
        if repo != "astroai/canfar-skills":
            return "failed", f"unexpected repo {repo}"
        path_list = [paths] if isinstance(paths, str) else list(paths)
        cache_root.mkdir(parents=True, exist_ok=True)
        for rel in path_list:
            src = CANFAR_SKILLS_SRC / rel
            dst = cache_root / rel
            if src.is_dir():
                if dst.exists():
                    shutil.rmtree(dst)
                shutil.copytree(src, dst)
            else:
                dst.mkdir(parents=True, exist_ok=True)
                name = Path(rel).name
                (dst / "SKILL.md").write_text(f"# {name}\n", encoding="utf-8")
        return "cloned", repo

    monkeypatch.setattr(addons_mod, "_refresh_upstream_repo", _fake_refresh)


@pytest.fixture(autouse=True)
def clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    # Clear any host environment variables that might pollute tests
    keys_to_remove = []
    for key in os.environ:
        if key.startswith("ASTROAI_LAB_") or key in (
            "UV_CACHE_DIR",
            "PIP_CACHE_DIR",
            "PIXI_CACHE_DIR",
            "RATTLER_CACHE_DIR",
            "MAMBA_PKGS_DIRS",
            "WORK",
            "SRCDIR",
            "SCRATCH",
            "PROJECT",
            "XDG_CACHE_HOME",
        ):
            keys_to_remove.append(key)

    for key in keys_to_remove:
        monkeypatch.delenv(key, raising=False)

    # Version probes can hang on some installed CLIs; keep unit tests offline.
    monkeypatch.setenv("ASTROAI_LAB_PROBE_VERSION", "0")

    # get_settings() caches a pydantic model that snapshots env vars at first
    # call; clear it so a previous test's monkeypatched WORK/SCRATCH cannot
    # leak into later tests through the cached object.
    get_settings.cache_clear()
