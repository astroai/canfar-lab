# Shared home & concurrency

Two AstroAI sessions on CANFAR share `/arc/home` but each has its own
`$SCRATCH`. This page documents what lives where, what is safe to run
concurrently, and the guarantees `astroai` makes.

## What lives where

| State | Location | Why |
|-------|----------|-----|
| Agent configs (MCP servers, settings, skills) | `$HOME` (`~/.cursor`, `~/.claude.json`, …) | Durable, small, read-mostly; shared so every session is configured identically |
| Agent CLIs | `$HOME` (`~/.local/bin`, `~/.opencode/bin`, …) | Match upstream installers; update without AstroAI |
| Auth (`canfar`, `gh`, tokens) | `$HOME` | Must survive sessions |
| Env saves / lab state (`~/.astroai/lab`) | `$HOME` | Explicit snapshots + stamps |
| Ray cluster state (`~/.astroai/ray`) | `$HOME` | Written by the manager and CLI control plane |
| **Agent runtimes** (transcripts, session DBs, telemetry — e.g. `~/.claude/projects`) | **Symlink → scratch** | Two sessions writing one SQLite store over NFS corrupts it; NFS locking is unreliable |
| Caches, package envs | Scratch/work | Already per-session |

`astroai agent setup` (and `verify --fix`) relocates known agent runtime
directories onto the current session's scratch via symlinks and reports what
it moved. Directories larger than 200 MB are left in place and reported for
manual migration.

## Guarantees

1. **Atomic writes.** Every config file `astroai` writes goes through a
   temp-file + `rename` path, so a crash or concurrent reader never sees a
   torn JSON/YAML/env file.
2. **One writer at a time.** Mutations of shared home config take an
   `O_EXCL` lock file with stale-PID recovery:
   - agent domain (`setup`, `plugins install/remove`, `verify --fix`,
     `verify --clean`, `wipe`): `~/.astroai/lab/agent-setup.lock`
   - cluster domain (`cluster start`, `cluster stop`, hub *Start batch
     compute*): `~/.astroai/ray/control.lock`
   A lock whose recorded PID is dead is broken automatically after the
   timeout (30 s agent / 120 s cluster).
3. **Reads are always lock-free** — `status`, `cluster status`,
   `env export`, dashboard URL resolution never block.

## Practical rules

- Run `astroai agent verify --fix` in one session at a time; if another
  holds the lock you get a clear message instead of corruption.
- Chat/session history of relocated agents dies with the scratch disk.
  Configs, skills, and auth persist. Copy anything you need out of
  `$SCRATCH` before the session ends.
- The Ray cluster is controlled by whoever holds `control.lock`; two
  simultaneous `cluster start` calls serialize rather than race.
