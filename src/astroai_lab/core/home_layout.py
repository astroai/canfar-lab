"""Keep agent runtimes (databases, session stores) off the shared NFS home.

Two CANFAR sessions share ``/arc/home``; each has its own scratch. Agents
that keep SQLite databases or append-heavy stores under ``$HOME`` corrupt or
contend when two sessions use them at once (``flock`` is unreliable on NFS).

Policy:
- *Config* stays on ``$HOME`` (durable, small, read-mostly) — MCP servers,
  settings, auth.
- *Runtime* — session history, transcripts, SQLite stores, telemetry — is
  redirected to the session's scratch via symlinks from the well-known home
  paths. Scratch dies with the session; that is acceptable and documented.

Compliant apps follow ``XDG_DATA_HOME`` (already scratch-backed by
``session_env``). The entries below are for agents that hardcode their
runtime locations under ``$HOME`` (Claude Code and the DeepSeek Harness today).
Existing real directories are migrated into scratch only when small
(``MIGRATE_LIMIT_MB``); anything bigger is left in place and reported.

DeepSeek Harness state is only *partly* hardcoded: ``astroai studio`` points its
own profile's session root, full-text index and spill files at the state root
(see :mod:`astroai_lab.studio_profile`). The two directories below cover every
*other* dsh profile — ``web``, ``headless``, ``astroai panel run`` — whose
shipped defaults resolve under the harness home. Their durable configuration
(``settings.yaml``, ``.credentials.yaml``, ``profiles/``) deliberately stays on
``$HOME``.
"""

from __future__ import annotations

import shutil
from pathlib import Path

#: Harness-home children that hold session-scale runtime data. dsh's shipped
#: defaults are ``dshHomePath('sessions')`` and ``dshHomePath('storages')``.
DSH_RUNTIME_DIRS: tuple[str, ...] = (
    ".dsh/sessions",
    ".dsh/storages",
)

# Home-relative runtime paths that must be per-session. Order matters only
# for readability; parents are created as needed.
AGENT_RUNTIME_DIRS: tuple[str, ...] = (
    ".claude/projects",
    ".claude/todos",
    ".claude/statsig",
    ".claude/shell-snapshots",
    *DSH_RUNTIME_DIRS,
)

MIGRATE_LIMIT_MB = 200


def _dir_size_bytes(path: Path) -> int:
    total = 0
    for p in path.rglob("*"):
        try:
            if p.is_file():
                total += p.stat().st_size
        except OSError:
            continue
    return total


def relocate_agent_runtime(
    home: Path,
    data_root: Path,
    *,
    dry_run: bool = False,
) -> list[str]:
    """Point known agent runtime dirs at *data_root* via symlinks.

    Idempotent and conservative:
    - missing → create symlink (fresh homes)
    - already a symlink → leave
    - real dir ≤ :data:`MIGRATE_LIMIT_MB` → move to scratch, symlink back,
      report ``relocated:<name>``
    - real dir over the limit → leave, report ``skipped:<name> (too big)``
    Returns human-readable action lines (empty when everything was in place).
    """
    actions: list[str] = []
    if not data_root.is_dir() and not dry_run:
        data_root.mkdir(parents=True, exist_ok=True)
    for rel in AGENT_RUNTIME_DIRS:
        src = home / rel
        dst = data_root / rel.replace(".", "_", 1)
        if src.is_symlink():
            try:
                target = src.resolve(strict=False)
            except OSError:
                target = None
            # Recreate when the link is dangling or points outside this session.
            under_data = False
            if target is not None:
                try:
                    under_data = target == data_root or data_root in target.parents
                except (OSError, ValueError):
                    under_data = False
            if under_data and target is not None and target.exists():
                continue
            if dry_run:
                actions.append(f"relink:{rel}")
                continue
            src.unlink(missing_ok=True)
            dst.mkdir(parents=True, exist_ok=True)
            src.parent.mkdir(parents=True, exist_ok=True)
            src.symlink_to(dst, target_is_directory=True)
            actions.append(f"relink:{rel}")
            continue
        if not src.exists():
            if dry_run:
                actions.append(f"link:{rel}")
                continue
            dst.mkdir(parents=True, exist_ok=True)
            src.parent.mkdir(parents=True, exist_ok=True)
            src.symlink_to(dst, target_is_directory=True)
            actions.append(f"link:{rel}")
            continue
        size = _dir_size_bytes(src)
        limit = MIGRATE_LIMIT_MB * 1024 * 1024
        if size > limit:
            actions.append(f"skipped:{rel} ({size >> 20}MB > {MIGRATE_LIMIT_MB}MB — move manually)")
            continue
        if dry_run:
            actions.append(f"relocate:{rel}")
            continue
        dst.parent.mkdir(parents=True, exist_ok=True)
        if dst.exists():
            # Prior scratch copy from an earlier session — scratch wins.
            shutil.rmtree(dst)
        shutil.move(str(src), str(dst))
        src.symlink_to(dst, target_is_directory=True)
        actions.append(f"relocate:{rel}")
    return actions
