---
name: canfar-lab-workflow
description: >-
  AstroAI/CANFAR session workflow: mounts, quotas, resources, headless, ports,
  pixi under $WORK, never ~/.local. Use when on a CANFAR session or asking how
  to work here.
---
# AstroAI session on CANFAR

**Names:** `canfar` = platform sessions/auth; `canfar lab` (standalone
`canfar-lab`) = in-session CLI (env, Ray jobs, agents). AstroAI = product;
CANFAR = host platform.

Detect CANFAR: `$HOME` is under `/arc/home/`, or `/scratch` + `/arc` mounts exist.
When in doubt, run `canfar lab status --json` / `df -h` / `canfar info`.

```bash
canfar lab agent setup              # once per user — MCP + Cursor rules
npx skills add astroai/canfar-skills   # CANFAR platform skills
canfar lab agent install codex      # public GitHub release — no gh login needed
canfar lab agent install kilo       # or: goose, cline, opencode, cursor, …
gh auth login                    # only for GitHub MCP / private repos / git push
```

Discover: `canfar lab agent list` · plugins: `canfar lab agent plugins list`  
Refresh after upgrading lab: `canfar lab agent update`  
Broken configs: `canfar lab agent verify` · `canfar lab agent verify --fix`

## CANFAR ecosystem (agents must know)

Deeper skills (install via `npx skills add astroai/canfar-skills`): `canfar-storage`,
`canfar-quotas`, `canfar-limits`, `canfar-sessions`, `canfar-batch`, `canfar-platform`.

### Mounts (CADC paths — verify live)

| Mount / var | Lifetime | Shared? | Use for |
|-------------|----------|---------|---------|
| `/scratch` | **Session only** (wiped on end) | No | Large temp I/O, caches |
| `${SCRATCH}` / `$SCRATCH` | Session-oriented | No | AstroAI data/runtime roots |
| `${WORK}` (often `$SCRATCH/src`) | Ephemeral code+envs | No | **Repos + `.pixi`** — not `$HOME` |
| `/arc/home/<user>` (`$HOME`) | Persistent, **small quota** | Your sessions | Dotfiles, gh auth, MCP only |
| `/arc/projects/<group>` | Project allocation | Yes (group) | Shared data/results/scripts |
| VOSpace (`arc:`, `vault:`, …) | Service policy | ACLs | Long-term / publish |

```bash
df -h /scratch /arc/home/$USER /arc/projects/* 2>/dev/null
du -sh /arc/home/$USER/* 2>/dev/null | sort -h | tail
canfar lab status --json          # quotas, projects, auth
```

Scratch full ≠ home quota ≠ project quota — diagnose with `df`/`du` before guessing.

### Resources (this Session vs platform)

| Layer | What it is | Inspect |
|-------|------------|---------|
| Requested CPU/RAM/GPU | What you asked at create | Portal / `canfar info <id>` |
| cgroup limits | What the container may use | `nproc`; `free -h`; cgroup `memory.max` |
| `/scratch` size | Ephemeral disk ceiling | `df -h /scratch` |
| Platform capacity | Cluster-wide, not yours | `canfar stats` |

Chart **defaults** (operators override — never promise): interactive ~4 days;
headless deadline ~14 days; ~5 interactive sessions/user; non-desktop scratch
ceiling often ~200 GiB. Always verify live.

```bash
canfar info <session-id>
nproc; free -h; df -h /scratch
nvidia-smi   # only if GPU allocated
```

Flexible resources = exploration. Fixed (`--cpu`, `--memory`, `--gpu`) = measured jobs
(may queue longer). OOM/exit 137 → raise memory or shrink chunks (`canfar-limits`).

### Headless / batch

No Notebook/Desktop UI. Runs a command and exits. Does **not** count toward the
interactive session cap. Same mounts; persist outputs to `/arc/projects` before end.

**Images:** for AstroAI work **never** `skaha/*` — always
`images.canfar.net/astroai/<image>:<tag>` (`base`, `notebook`, `webterm`,
`ray-manager`, …). See `canfar image ls`.

```bash
canfar create headless images.canfar.net/astroai/base:latest --name reduce --cpu 8 --memory 32 \
  --env PYTHONNOUSERSITE=1 \
  -- python /arc/projects/mygroup/scripts/reduce.py
# replicas: REPLICA_ID / REPLICA_COUNT for splits (client max often 512)
```

Always: `export PYTHONNOUSERSITE=1` and `unset PYTHONPATH` in headless runners.

### Ports

| Case | Port / rule |
|------|-------------|
| Contributed / custom web UI in Skaha | Listen on **5000** (probe contract) |
| Notebook / Desktop / CARTA / Firefly | Platform proxies the app — do not invent host ports |
| User services | Prefer platform patterns; do not bind random high ports expecting Portal routing |

### Env hygiene on CANFAR (blocking)

- Prefer **`pixi run`** / `pixi run python` over bare `python3`.
- **Never** `pip install --user` or install into `$HOME/.local`.
- Project envs live under `${WORK}` / the repo `.pixi` — not `/arc/home`.
- **Code path:** always `${WORK}` → `$SCRATCH/src` → `/scratch/src` on CANFAR
  (ephemeral with the session; survives container OOM).
- **Images:** always `images.canfar.net/astroai/*` — **never** `skaha/*`.

## Getting code onto jobs

`/scratch` is **per-pod**. Same-session `canfar lab run` ships the script cwd via
Ray `working_dir`. Cross-session / headless needs an explicit mode:

| Mode | Moves | Command / convention |
|------|-------|----------------------|
| **1. Git push/pull** | Source | Push fork → `canfar lab clone <name> --update` (`--ref` pin; `--force` hard-reset) |
| **2. save/resume** | Lockfiles / optional full env | `canfar lab save` → `canfar lab resume` (deps, **not** uncommitted source) |
| **3. VOSpace tarball** | Source (+ deps if packed) | `vos:$USER/astroai/*.tgz` → unpack under `$WORK` |
| **4. 2+3** | Env + source | resume locks, unpack source tarball into `$WORK` |

Mode 1 defaults: bare name prefers **your GitHub user**, then `astroai/`.
`--update` is ff-only unless `--force`. Clone/update prints the short SHA —
record it in job logs.

```bash
canfar lab clone torchsky --update
canfar lab clone sfabbro/torchsky --ref topic
canfar lab clone --from-env mylab sfabbro/torchsky
# Prefer org layout (Mac mirror): move flat clones into $WORK/astroai/<name>
mkdir -p "${WORK}/astroai" && mv "${WORK}/torchsky" "${WORK}/astroai/torchsky" 2>/dev/null || true
```

## Session workspace (astroai + sfabbro)

Mirror Mac `~/src/{astroai,sfabbro}` under `${WORK}` (`/scratch/src`) — not `opencadc/`, `clones/`, or `overleaf/`:

```bash
# In-session one-shot bootstrap (wires agents, clones repos, installs pixi envs):
bash "$WORK/sfabbro/agent-home/scripts/bootstrap-canfar-workspace.sh" --tier core

# Sync git repos, check dirty state, check key validity:
workspace-sync status     # or: python3 "$WORK/scripts/workspace-sync.py" status
workspace-sync sync       # fetch & fast-forward/rebase all repos
workspace-sync dirty      # verify no uncommitted work before session teardown
sync-keys check           # audit $HOME keys and storage hygiene
```

## Running headless batch jobs (Laptop or Session)

```bash
# Launch batch job staging code directly into /scratch/src on the worker pod:
canfar lab jobs submit --cmd 'pixi run python train.py' --wait
```

## Autoscaling Ray cluster (Laptop or Session)

```bash
canfar lab cluster start --min-workers 0 --max-workers 8 --cores 2 --ram 8
canfar lab cluster status
canfar lab cluster dashboard
canfar lab cluster stop
```

## Daily workflow (inside a CANFAR session)

```bash
source "$WORK/env.sh"             # sets WORK=/scratch/src, aliases: cdwork, ws-status, ws-sync
cdwork/astroai/torchsky           # /scratch/src/astroai/torchsky on CANFAR
pixi run python analysis.py       # not bare python3

# Before ending session:
ws-dirty                          # ensure all work is committed and pushed
ws-sync                           # sync latest tips
```

Global flags work before or after the subcommand: `canfar lab status --json`.

## Storage (AstroAI layout)

| Path | What |
|------|------|
| `${WORK}` (`$SCRATCH/src`) | Code + project `.pixi`/`.venv` — session-ephemeral |
| `${SCRATCH}` | Data, download caches, runtime installs |
| `/opt/canfar` | Platform CLIs (`canfar`, `cadcget`, `canfar-lab`) — session-writable |
| `/arc/projects/<team>/.local` | Shared team tools (persistent) |
| `/arc` (`$HOME`) | **Small only** — MCP, gh auth, lockfile saves |

```bash
upgrade-cadc-tools.sh list
upgrade-cadc-tools.sh --upgrade astroai-lab
canfar lab status --json
```

Optional: `${WORK}/.astroai-lab/pythonpath` or `ASTROAI_LAB_PYTHONPATH`.

## Search & run

```bash
rg 'pattern' --type py
fd name
sg -p 'class $N' -l py          # canfar lab agent plugins install ast-grep-cli
pixi run pytest -q
peek README.md
```

## Help

```bash
canfar lab help
canfar lab cluster status
canfar lab status --json
canfar lab save --list --json
canfar lab agent list
less /opt/astroai/USAGE.md
```
