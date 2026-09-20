"""Keep agent runtimes (databases, session stores) off the shared NFS home.

Two CANFAR sessions share ``/arc/home``; each has its own scratch. Agents
that keep SQLite databases or append-heavy stores under ``$HOME`` corrupt or
contend when two sessions use them at once (``flock`` is unreliable on NFS).

Policy:
- *Config* stays on ``$HOME`` (durable, small, read-mostly) — MCP servers,
  settings, auth.
- *Runtime* — session history, transcripts, SQLite stores, telemetry, native
  addons, browser sandboxes — is redirected to the session's scratch via
  symlinks from the well-known home paths. Scratch dies with the session;
  that is acceptable and documented.

Compliant apps follow ``XDG_DATA_HOME`` (already scratch-backed by
``session_env``). The entries below are for agents that hardcode their
runtime locations under ``$HOME`` (Claude Code, DeepSeek Harness, and Oh My
Pi / ``omp`` today). Existing real directories are migrated into scratch only
when small (``MIGRATE_LIMIT_MB``), except paths listed in
``AGENT_RUNTIME_FORCE_DIRS`` which always move — those are known CephFS
latency bombs (hundreds of MB of natives / Chrome / SQLite).

DeepSeek Harness state is only *partly* hardcoded: ``astroai studio`` points its
own profile's session root, full-text index and spill files at the state root
(see :mod:`astroai_lab.studio_profile`). The two directories below cover every
*other* dsh profile — ``web``, ``headless``, ``astroai panel run`` — whose
shipped defaults resolve under the harness home. Their durable configuration
(``settings.yaml``, ``.credentials.yaml``, ``profiles/``) deliberately stays on
``$HOME``.

Oh My Pi (``omp``) defaults to ``~/.omp`` unless ``$XDG_{DATA,CACHE,STATE}_HOME/omp``
already exists (see upstream ``DirResolver``). Seeding those XDG roots (see
:func:`ensure_omp_xdg_roots`) makes *new* writes land on scratch; the
``.omp/...`` force-relocate paths clean up installs that already wrote to /arc.
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

#: Oh My Pi — natives (~360MB dlopen), Puppeteer Chrome (~380MB), SQLite WAL
#: DBs, session transcripts, composer autosaves, daemon sockets, and logs.
#: All of these are catastrophic over CephFS /arc/home.
OMP_RUNTIME_DIRS: tuple[str, ...] = (
    ".omp/natives",
    ".omp/puppeteer",
    ".omp/agent",
    ".omp/run",
    ".omp/logs",
)

# Home-relative runtime paths that must be per-session. Order matters only
# for readability; parents are created as needed.
AGENT_RUNTIME_DIRS: tuple[str, ...] = (
    ".claude/projects",
    ".claude/todos",
    ".claude/statsig",
    ".claude/shell-snapshots",
    *DSH_RUNTIME_DIRS,
    *OMP_RUNTIME_DIRS,
)

#: Always relocate even when larger than :data:`MIGRATE_LIMIT_MB`.
AGENT_RUNTIME_FORCE_DIRS: frozenset[str] = frozenset(OMP_RUNTIME_DIRS)

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


def ensure_omp_xdg_roots(*xdg_homes: Path) -> list[str]:
    """Create ``$XDG_*/omp`` so omp's DirResolver prefers XDG over ``~/.omp``.

    Upstream only redirects when the XDG app root already exists. Returns
    action labels for any roots that were created.
    """
    actions: list[str] = []
    for root in xdg_homes:
        if not root:
            continue
        target = root / "omp"
        if target.is_dir():
            continue
        target.mkdir(parents=True, exist_ok=True)
        actions.append(f"seed:xdg-omp:{root.name}")
    return actions


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
    - real dir ≤ :data:`MIGRATE_LIMIT_MB` (or in :data:`AGENT_RUNTIME_FORCE_DIRS`)
      → move to scratch, symlink back, report ``relocated:<name>``
    - real dir over the limit and not forced → leave, report ``skipped:<name>``
    Returns human-readable action lines (empty when everything was in place).
    """
    actions: list[str] = []
    if not data_root.is_dir() and not dry_run:
        data_root.mkdir(parents=True, exist_ok=True)
    for rel in AGENT_RUNTIME_DIRS:
        src = home / rel
        dst = data_root / rel.replace(".", "_", 1)
        force = rel in AGENT_RUNTIME_FORCE_DIRS
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
        if size > limit and not force:
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
