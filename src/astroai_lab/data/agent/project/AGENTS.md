# AGENTS.md

AstroAI lab project — guidance for AI coding agents.

**Names:** `canfar` manages platform sessions; `astroai` is the in-session
CLI (project env, Ray cluster/jobs, agents). AstroAI is the product; CANFAR
is the Science Platform.

## Setup (each developer, once)

```bash
astroai agent setup          # on /arc — MCP + skills
astroai agent install kilo   # or goose, cline, opencode, codex, cursor, …
astroai agent install cursor # Cursor Agent CLI onto $SCRATCH
gh auth login
```

Refresh after upgrading lab in-session: `astroai agent update`
Overview / broken configs: `astroai agent list` · `astroai agent verify`
Curated lean/science plugins: `astroai agent plugins list` · `astroai agent plugins install ponytail`

## This repo

```bash
pixi install    # or uv sync — env lives under $WORK, not $HOME
pixi run …      # or uv run …
astroai save         # before session ends — code on $WORK is ephemeral
astroai cluster start
astroai run train.py --cpus 2
```

Pin Python deps in **pixi.toml / uv.lock** here — not in the image platform venv.
Platform CLIs (`canfar`, `cadcget`, `astroai`) live in `/opt/astroai/venv/cadc`; upgrade this session with `upgrade-cadc-tools.sh` if needed.

Search: `rg`, `fd`, `sg` (ast-grep skill). View files: `peek <path>` or `bat`/`less`.
Help: `astroai help`, `astroai cluster status`, `astroai status --json`.

In webterm, prefer `peek` when pointing the user at generated plans, logs, or archives.
