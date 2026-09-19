# AGENTS.md

AstroAI lab project — guidance for AI coding agents.

**Names:** `canfar` manages platform sessions; `astroai` is the in-session
CLI (project env, Ray cluster/jobs, agents). AstroAI is the product; CANFAR
is the Science Platform.

## Setup (each developer, once)

```bash
astroai agent setup                    # on /arc — MCP + Cursor rules
npx skills add astroai/canfar-skills   # CANFAR platform skills
astroai agent install kilo             # or goose, cline, opencode, codex, cursor, …
astroai agent install cursor           # Cursor Agent CLI onto $SCRATCH
gh auth login
```

Refresh after upgrading lab in-session: `astroai agent update`
Overview / broken configs: `astroai agent list` · `astroai agent verify`
Plugins (MCP / tools / rules only): `astroai agent plugins list` · `astroai agent plugins install ray-manager-mcp`
Skills: `npx skills add …` (not managed by AstroAI)

## This repo

```bash
pixi install    # env under $WORK — never $HOME/.local
pixi run …      # prefer over bare python3
git push -u origin HEAD          # before another session/job needs this tip
astroai save                     # env/lockfiles only — not your .py tree
astroai clone sfabbro/<repo> --update   # next session: latest fork tip + SHA
astroai cluster start
astroai run train.py --cpus 2
```

**Code home on CANFAR:** `$WORK` → `/scratch/src` (dies with the session).
Propagation modes: (1) GitHub push/pull via `clone --update` / `--ref`,
(2) `save`/`resume` for envs, (3) `vos:$USER/astroai/` tarball, (4) 2+3.
Same-session Ray packages the local tree; other pods need 1–4.

**Never** `pip install --user` or install into `$HOME/.local` (`/arc/home` is small and shared). Headless: `PYTHONNOUSERSITE=1` and `unset PYTHONPATH`.
**Session images:** always `images.canfar.net/astroai/*` — never `skaha/*`.
Pin Python deps in **pixi.toml / uv.lock** here — not in the image platform venv.
Platform CLIs (`canfar`, `cadcget`, `astroai`) live in `/opt/astroai/venv/cadc`; upgrade this session with `upgrade-cadc-tools.sh` if needed.

### CANFAR quick map

| Thing | Where / how |
|-------|-------------|
| Code + pixi env | `${WORK}` (often under `$SCRATCH/src`) |
| Big temp data | `/scratch` (wiped when session ends) |
| Shared project data | `/arc/projects/<group>` |
| Home (tiny) | `/arc/home/$USER` — config only |
| Quotas / projects | `astroai status --json`, `df -h` |
| Session CPU/RAM | `nproc`, `free -h`, `canfar info` |
| Headless batch | `canfar create headless …` + `PYTHONNOUSERSITE=1` |
| Contributed web UI port | **5000** |

Search: `rg`, `fd`, `sg` (`astroai agent plugins install ast-grep-cli`). View files: `peek <path>` or `bat`/`less`.
Help: `astroai help`, `astroai cluster status`, `astroai status --json`.

In terminal, prefer `peek` when pointing the user at generated plans, logs, or archives.
