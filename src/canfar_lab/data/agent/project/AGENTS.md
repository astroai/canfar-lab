# AGENTS.md

AstroAI lab project — guidance for AI coding agents.

**Names:** `canfar` manages platform sessions; `canfar lab` (standalone
`canfar-lab`) is the in-session CLI (project env, Ray cluster/jobs, agents).
AstroAI is the product; CANFAR is the Science Platform.

## Setup (each developer, once)

```bash
canfar lab agent setup                    # on /arc — MCP + Cursor rules
npx skills add astroai/canfar-skills   # CANFAR platform skills
canfar lab agent install kilo             # or goose, cline, opencode, codex, cursor, …
canfar lab agent install cursor           # Cursor Agent CLI onto $SCRATCH
gh auth login
```

Refresh after upgrading lab in-session: `canfar lab agent update`
Overview / broken configs: `canfar lab agent list` · `canfar lab agent verify`
Plugins (MCP / tools / rules only): `canfar lab agent plugins list` · `canfar lab agent plugins install ray-manager-mcp`
Skills: `npx skills add …` (not managed by AstroAI)

## This repo

```bash
pixi install    # env under $WORK — never $HOME/.local
pixi run …      # prefer over bare python3
git push -u origin HEAD          # before another session/job needs this tip
canfar lab save                     # env/lockfiles only — not your .py tree
canfar lab clone sfabbro/<repo> --update   # next session: latest fork tip + SHA
canfar lab cluster start
canfar lab run train.py --cpus 2
```

**Code home on CANFAR:** `$WORK` → `/scratch/src` (dies with the session).
Propagation modes: (1) GitHub push/pull via `clone --update` / `--ref`,
(2) `save`/`resume` for envs, (3) `vos:$USER/astroai/` tarball, (4) 2+3.
Same-session Ray packages the local tree; other pods need 1–4.

**Never** `pip install --user` or install into `$HOME/.local` (`/arc/home` is small and shared). Headless: `PYTHONNOUSERSITE=1` and `unset PYTHONPATH`.
**Session images:** always `images.canfar.net/astroai/*` — never `skaha/*`.
Pin Python deps in **pixi.toml / uv.lock** here — not in the image platform venv.
Platform CLIs (`canfar`, `cadcget`, `canfar-lab`) live in `/opt/canfar`; upgrade this session with `upgrade-cadc-tools.sh` if needed.

### CANFAR quick map

| Thing | Where / how |
|-------|-------------|
| Code + pixi env | `${WORK}` (often under `$SCRATCH/src`) |
| Big temp data | `/scratch` (wiped when session ends) |
| Shared project data | `/arc/projects/<group>` |
| Home (tiny) | `/arc/home/$USER` — config only |
| Quotas / projects | `canfar lab status --json`, `df -h` |
| Session CPU/RAM | `nproc`, `free -h`, `canfar info` |
| Headless batch | `canfar create headless …` + `PYTHONNOUSERSITE=1` |
| Contributed web UI port | **5000** |

Search: `rg`, `fd`, `sg` (`canfar lab agent plugins install ast-grep-cli`). View files: `peek <path>` or `bat`/`less`.
Help: `canfar lab help`, `canfar lab cluster status`, `canfar lab status --json`.

In terminal, prefer `peek` when pointing the user at generated plans, logs, or archives.
