"""Install the skills the Studio assistant reads on a CANFAR session.

dsh scans ``~/.agents/skills`` (its user-agents root) at chat time. A session
prepare fills that directory before the assistant starts, so the first message
already has the platform skill and the AstroAI session rules.
"""

from __future__ import annotations

import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from functools import partial
from pathlib import Path

from canfar_lab.studio_paths import agents_skills_dir

CANFAR_SKILL_REPO = "https://raw.githubusercontent.com/opencadc/canfar/main/skills/canfar"
CANFAR_SKILL_FILES = (
    "SKILL.md",
    "references/batch.md",
    "references/data.md",
    "references/python.md",
)
_SESSION_SKILL = "canfar-session"


def session_skill_source() -> Path:
    return (
        Path(__file__).resolve().parent / "data" / "agent" / "skills" / _SESSION_SKILL / "SKILL.md"
    )


def _fetch_one(rel: str, timeout: float) -> tuple[str, str] | None:
    request = urllib.request.Request(
        f"{CANFAR_SKILL_REPO}/{rel}",
        headers={
            "User-Agent": "canfar-lab/studio-prepare (+https://github.com/astroai/canfar-lab)",
            "Accept": "text/plain",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310
            text = response.read().decode("utf-8")
    except (OSError, urllib.error.URLError, TimeoutError, UnicodeError):
        return None
    if rel == "SKILL.md" and "name: canfar" not in text:
        return None
    if not text.strip():
        return None
    return rel, text


def fetch_canfar_skill_files(*, timeout: float = 8.0) -> dict[str, str] | None:
    """The published ``opencadc/canfar`` skill, or None when any file is missing.

    The four files download together, so a dead host costs one timeout, not four.
    """
    with ThreadPoolExecutor(max_workers=len(CANFAR_SKILL_FILES)) as pool:
        results = list(pool.map(partial(_fetch_one, timeout=timeout), CANFAR_SKILL_FILES))
    fetched: dict[str, str] = {}
    for item in results:
        if item is None:
            return None
        rel, text = item
        fetched[rel] = text
    return fetched


def _write_tree(root: Path, files: dict[str, str]) -> None:
    for rel, text in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text if text.endswith("\n") else text + "\n", encoding="utf-8")


def ensure_platform_skills(home: Path) -> list[str]:
    """Install session rules and the canfar client skill into ``~/.agents/skills``."""
    actions: list[str] = []
    root = agents_skills_dir(home)
    source = session_skill_source()
    if source.is_file():
        dest = root / _SESSION_SKILL
        _write_tree(dest, {"SKILL.md": source.read_text(encoding="utf-8")})
        actions.append(f"skill {_SESSION_SKILL} → {dest}")
    else:
        actions.append(f"WARN session skill missing from package ({source})")

    files = fetch_canfar_skill_files()
    if files is None:
        actions.append(
            "WARN canfar skill not installed — the assistant will retry on the next prepare"
        )
        return actions
    dest = root / "canfar"
    _write_tree(dest, files)
    actions.append(f"skill canfar → {dest}")
    return actions
