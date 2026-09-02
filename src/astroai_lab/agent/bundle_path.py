from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path


@lru_cache
def bundle_root() -> Path:
    env = os.environ.get("ASTROAI_LAB_AGENT_BUNDLE", "").strip()
    if env and Path(env).is_dir():
        return Path(env)
    pkg = Path(__file__).resolve().parent.parent / "data" / "agent"
    if pkg.is_dir():
        return pkg
    raise FileNotFoundError(f"Agent bundle not found: {pkg}")


def bundled_skill_src(name: str) -> Path:
    """Legacy lookup for bundled SKILL.md trees (no longer shipped by AstroAI).

    Skills install via ``npx skills``. Kept for one-shot reconcile cleanup of
    old ``.astroai-managed`` trees that referenced package paths.
    """
    root = bundle_root()
    for rel in (Path("skills") / name, Path("cursor") / "skills" / name):
        src = root / rel
        if (src / "SKILL.md").is_file():
            return src
    return root / "skills" / name
