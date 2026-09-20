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
``session_env``). The entries below cover agents that hardcode runtime under
``$HOME``. Soft migrate when ≤ ``MIGRATE_LIMIT_MB``; ``AGENT_RUNTIME_FORCE_DIRS``
always move (SQLite WAL / natives / Chrome / unbounded transcripts).

Also see :mod:`astroai_lab.shell.session_env` for ``CODEX_SQLITE_HOME`` /
``HERMES_HOME`` / XDG / ``PUPPETEER_CACHE_DIR`` env redirects.
"""

from __future__ import annotations

import shutil
from pathlib import Path

#: dsh session logs + KV stores. These stay on ``$HOME`` (durable across CANFAR
#: sessions — see docs/STUDIO.md). Listed only so :func:`repair_dsh_durable_dirs`
#: can undo older scratch symlinks that break Studio Connect.
DSH_RUNTIME_DIRS: tuple[str, ...] = (
    ".dsh/sessions",
    ".dsh/storages",
)

#: Oh My Pi — natives (~360MB dlopen), Puppeteer Chrome (~380MB), SQLite WAL
#: DBs, session transcripts, composer autosaves, daemon sockets, and logs.
OMP_RUNTIME_DIRS: tuple[str, ...] = (
    ".omp/natives",
    ".omp/puppeteer",
    ".omp/agent",
    ".omp/run",
    ".omp/logs",
)

#: Claude Code — remaining append-heavy / cache trees beyond projects/todos.
CLAUDE_RUNTIME_DIRS: tuple[str, ...] = (
    ".claude/projects",
    ".claude/todos",
    ".claude/statsig",
    ".claude/shell-snapshots",
    ".claude/file-history",
    ".claude/paste-cache",
    ".claude/image-cache",
    ".claude/debug",
    ".claude/plans",
    ".claude/tasks",
    ".claude/session-env",
)

#: Cursor Agent CLI — chats/*/store.db (SQLite) + project transcripts.
CURSOR_RUNTIME_DIRS: tuple[str, ...] = (
    ".cursor/chats",
    ".cursor/projects",
    ".cursor/ai-tracking",
)

#: Codex CLI — JSONL rollouts + SQLite state (also CODEX_SQLITE_HOME in session_env).
CODEX_RUNTIME_DIRS: tuple[str, ...] = (
    ".codex/sessions",
    ".codex/sqlite",
    ".codex/log",
)

#: Pi (earendil) — session JSONL trees (auth/settings stay under ~/.pi/agent/).
PI_RUNTIME_DIRS: tuple[str, ...] = (".pi/agent/sessions",)

#: OpenClaw — per-agent SQLite + workspace state (config JSON stays).
OPENCLAW_RUNTIME_DIRS: tuple[str, ...] = (
    ".openclaw/agents",
    ".openclaw/state",
    ".openclaw/workspace",
)

# Home-relative runtime paths that must be per-session.
# Intentionally excludes :data:`DSH_RUNTIME_DIRS` — dsh workspaces are durable
# on ``$HOME/.dsh``; scratch-linking them leaves dangling symlinks next boot
# (agent-setup stamp skips re-link → Studio never captures a web token).
AGENT_RUNTIME_DIRS: tuple[str, ...] = (
    *CLAUDE_RUNTIME_DIRS,
    *OMP_RUNTIME_DIRS,
    *CURSOR_RUNTIME_DIRS,
    *CODEX_RUNTIME_DIRS,
    *PI_RUNTIME_DIRS,
    *OPENCLAW_RUNTIME_DIRS,
)

#: Always relocate even when larger than :data:`MIGRATE_LIMIT_MB`.
AGENT_RUNTIME_FORCE_DIRS: frozenset[str] = frozenset(
    (
        *OMP_RUNTIME_DIRS,
        *CURSOR_RUNTIME_DIRS,
        *CODEX_RUNTIME_DIRS,
        *PI_RUNTIME_DIRS,
        *OPENCLAW_RUNTIME_DIRS,
        ".claude/projects",
        ".claude/file-history",
        ".claude/paste-cache",
        ".claude/image-cache",
    )
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


def seed_hermes_home(hermes_home: Path, home: Path, *, dry_run: bool = False) -> list[str]:
    """Point Hermes at scratch via ``HERMES_HOME``; copy durable config once.

    Hermes stores ``state.db`` (SQLite WAL) next to ``config.yaml`` under
    ``HERMES_HOME`` (default ``~/.hermes``). Moving only the DB is awkward, so
    we relocate the whole home to scratch and adopt ``config.yaml`` from /arc
    when present.
    """
    actions: list[str] = []
    src_cfg = home / ".hermes" / "config.yaml"
    dst_cfg = hermes_home / "config.yaml"
    if dry_run:
        if not hermes_home.is_dir():
            actions.append("seed:hermes-home")
        if src_cfg.is_file() and not dst_cfg.is_file():
            actions.append("seed:hermes-config")
        return actions
    hermes_home.mkdir(parents=True, exist_ok=True)
    if src_cfg.is_file() and not dst_cfg.is_file():
        shutil.copy2(src_cfg, dst_cfg)
        actions.append("seed:hermes-config")
    return actions


#: Env keys that must be in the process/shell so agent CLIs write off /arc.
AGENT_SCRATCH_ENV_KEYS: tuple[str, ...] = (
    "XDG_CACHE_HOME",
    "XDG_DATA_HOME",
    "XDG_STATE_HOME",
    "PUPPETEER_CACHE_DIR",
    "CODEX_SQLITE_HOME",
    "HERMES_HOME",
    "ASTROAI_LAB_BIN_DIR",
    "ASTROAI_LAB_NPM_PREFIX",
    "NPM_CONFIG_PREFIX",
    "NPM_CONFIG_CACHE",
    "UV_CACHE_DIR",
    "PIP_CACHE_DIR",
)


def apply_agent_scratch_env(exports: dict[str, str]) -> list[str]:
    """Push scratch redirects into ``os.environ`` for this process + children."""
    import os

    applied: list[str] = []
    for key in AGENT_SCRATCH_ENV_KEYS:
        val = exports.get(key)
        if not val:
            continue
        if os.environ.get(key) == val:
            continue
        os.environ[key] = val
        applied.append(f"env:{key}")
    return applied


def hermes_home_dir(*, home: Path | None = None) -> Path:
    """Resolved Hermes state directory (``HERMES_HOME`` when session-backed).

    Honors ``HERMES_HOME`` only when it lives under *home* or under
    ``$SCRATCH`` (CANFAR session). Otherwise keep the legacy ``~/.hermes``
    path so unit tests that only flip ``HOME`` stay self-contained.
    """
    import os

    home = home or Path.home()
    existing = os.environ.get("HERMES_HOME", "").strip()
    if not existing:
        return home / ".hermes"
    hh = Path(existing)
    try:
        home_r = home.resolve()
        hh_r = hh.resolve()
    except OSError:
        return home / ".hermes"
    try:
        if hh_r == home_r or hh_r.is_relative_to(home_r):
            return hh
    except (ValueError, AttributeError):
        pass
    scratch = os.environ.get("SCRATCH", "").strip()
    if scratch:
        try:
            scratch_r = Path(scratch).resolve()
            if hh_r == scratch_r or hh_r.is_relative_to(scratch_r):
                return hh
        except (OSError, ValueError, AttributeError):
            pass
    return home / ".hermes"


def repair_dsh_durable_dirs(home: Path, *, dry_run: bool = False) -> list[str]:
    """Restore ``~/.dsh/{sessions,storages}`` as real directories on *home*.

    Older lab builds symlinked these onto ``$SCRATCH``. Scratch dies with the
    session; the next boot then sees a dangling link, dsh never prints a web
    token, and Studio Connect 401s. Undo those links (migrate any surviving
    target contents back onto home) so workspaces stay durable.
    """
    actions: list[str] = []
    for rel in DSH_RUNTIME_DIRS:
        src = home / rel
        if src.is_symlink():
            if dry_run:
                actions.append(f"restore:{rel}")
                continue
            target: Path | None
            try:
                target = src.resolve(strict=False)
            except OSError:
                target = None
            src.unlink(missing_ok=True)
            src.mkdir(parents=True, exist_ok=True)
            if target is not None and target.is_dir():
                for child in target.iterdir():
                    dest = src / child.name
                    if dest.exists():
                        continue
                    try:
                        shutil.move(str(child), str(dest))
                    except OSError:
                        continue
            actions.append(f"restore:{rel}")
            continue
        if not src.exists():
            if dry_run:
                actions.append(f"mkdir:{rel}")
                continue
            src.mkdir(parents=True, exist_ok=True)
            actions.append(f"mkdir:{rel}")
    return actions


def ensure_agent_runtime_on_scratch(
    home: Path | None = None,
    *,
    dry_run: bool = False,
) -> list[str]:
    """Create scratch roots, apply env redirects, and symlink hot agent trees.

    Intended entry point for ``astroai agent install`` / ``agent setup`` /
    ``agent layout`` so CLIs write under ``$SCRATCH`` before they first run.
    Also repairs durable dsh dirs that older builds left pointing at scratch.

    No-ops (empty list) when *home* is not the real user home — unit tests
    pass a temp tree and must not get session-scratch symlinks.
    """
    home = home or Path.home()
    try:
        if home.resolve() != Path.home().resolve():
            return []
    except OSError:
        return []

    from astroai_lab.shell.session_env import resolve_session_env

    actions: list[str] = []
    env = resolve_session_env(ensure=not dry_run)
    exports = env.exports()
    if not dry_run:
        actions.extend(apply_agent_scratch_env(exports))
        actions.extend(
            ensure_omp_xdg_roots(env.xdg_cache_home, env.xdg_data_home, env.xdg_state_home)
        )
        actions.extend(seed_hermes_home(env.xdg_data_home / "hermes-home", home, dry_run=False))
    else:
        actions.extend(seed_hermes_home(env.xdg_data_home / "hermes-home", home, dry_run=True))
    actions.extend(repair_dsh_durable_dirs(home, dry_run=dry_run))
    actions.extend(relocate_agent_runtime(home, env.xdg_data_home, dry_run=dry_run))
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
