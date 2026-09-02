# AGENTS.md

AstroAI lab project — guidance for AI coding agents.

**Names:** `canfar` manages platform sessions; `astroai` is the in-session
CLI (project env, Ray cluster/jobs, agents). AstroAI is the product; CANFAR
is the Science Platform.

## Setup (each developer, once)

```bash
astroai agent setup                    # on /arc — MCP + Cursor rules
npx skills add astroai/canfar-skills   # platform + workflow skills
astroai agent install kilo             # or goose, cline, opencode, codex, cursor, …
astroai agent install cursor           # Cursor Agent CLI onto $SCRATCH
gh auth login
```

Refresh after upgrading lab in-session: `astroai agent update`
Overview / broken configs: `astroai agent list` · `astroai agent verify`
Plugins (MCP / tools / rules only): `astroai agent plugins list` · `astroai agent plugins install ray-manager-mcp`
Skills: `npx skills add …` (not managed by AstroAI)

The default agent setup also includes a small evidence-first integrity layer:
neutral question reframing (`ask-dont-tell`), calibrated assessment
(`ground-truth`), and the fourteen-point `scientific-integrity` invariant.
Install the K-Dense scientific and Superpowers coding skills only when the
work needs them. Invoke `test-drive` and `the-quorum` explicitly for evidence
plans and consequential decisions; they are not default workflows.

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

Search: `rg`, `fd`, `sg` (`astroai agent plugins install ast-grep-cli`). View files: `peek <path>` or `bat`/`less`.
Help: `astroai help`, `astroai cluster status`, `astroai status --json`.

In webterm, prefer `peek` when pointing the user at generated plans, logs, or archives.
