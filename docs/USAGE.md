# canfar lab usage

**`canfar lab`** (standalone `canfar-lab`) is the in-session CLI on AstroAI images
([CANFAR Science Platform](https://www.opencadc.org/canfar/)).

It does project environments (`init` / `save` / `resume`), the Ray cluster
(`cluster start` / `run`), agents, kernels, and session status.

| Tool | Role |
|------|------|
| [`canfar`](https://github.com/opencadc/canfar) | Auth, session lifecycle, `canfar data` |
| CADC clients (`cadcget`, `vcp`, …) | Archive and VOSpace I/O |
| [Session images](https://github.com/astroai/canfar-containers) | `images.canfar.net/astroai/*` only (`terminal`, `notebook`, `vscode`, `marimo`, Ray) — never `skaha/*` |

| Doc | Scope |
|-----|--------|
| **USAGE.md** (this file) | Where to work, storage, Ray, agents |
| [help.md](help.md) | Cheat sheet |
| [cli.md](cli.md) | Flags and every command |
| [config.md](config.md) | Optional `~/.astroai/lab/config.yaml` |

In a session: `canfar lab help` · `less /opt/astroai/USAGE.md` (image user guide).

Platform: [opencadc.github.io/canfar](https://opencadc.github.io/canfar/)

---

## Where you work

```mermaid
flowchart TB
  subgraph outside [Laptop or browser]
    SP[Science Portal]
    CF["canfar login / create / ps"]
  end
  subgraph session [AstroAI session]
    AL["canfar lab"]
    PM[pixi / uv]
    NB[Jupyter / marimo]
    CADC[vcp / cadcget / …]
    Ray[Ray jobs]
  end
  SP --> session
  CF --> session
  AL --> PM
  AL --> NB
  AL --> CADC
  AL --> Ray
```

| Where | What you do | Tools |
|-------|-------------|--------|
| **Laptop / browser** | Log in, start and stop sessions | Science Portal, or `canfar login` / `create` / `ps` |
| **Inside a session** | Code, notebooks, training, agents | `canfar lab`, Jupyter, pixi/uv, CADC clients |

### Notebook-first

1. [Science Portal](https://www.canfar.net/science-portal) → **notebook** or **marimo**.
2. Jupyter: `/opt/astroai/notebooks/starter.ipynb` (kernel: `canfar lab kernel ensure`).
3. Marimo: `$WORK/notebooks/starter.py`.
4. `canfar lab status` for paths and quotas.
5. Keep results with `canfar data` or `vcp`. There is no `canfar lab` VOSpace wrapper.

---

## Install

Images ship `canfar-lab` on PATH (`/opt/astroai/venv/cadc`).

```bash
uv tool install git+https://github.com/astroai/canfar-lab.git
uv sync --all-extras && uv run canfar-lab --help
./scripts/ci.sh
```

---

## Code propagation (sessions + jobs)

On CANFAR, **local code always lives under `$WORK`**, which defaults to
**`$SCRATCH/src`** (same as `$SRCDIR`). That tree survives container OOM
restarts but **dies with the session**. Never put project code in
`/arc/home` or `$HOME/.local`.

Workers and new pods **do not** see another pod's `/scratch`. Pick an
explicit propagation mode before `canfar lab run` / headless jobs:

| Mode | What moves | When to use | How |
|------|------------|-------------|-----|
| **1. GitHub push → pull** | Source only (deps via pixi/uv on install) | Default for WIP on your fork | Push to `origin` (sfabbro fork). On the job/session: `canfar lab clone <fork/repo> --update` (ff-only to latest origin tip) or `--ref <branch\|sha>` to pin. Print SHA is the receipt. |
| **2. `canfar lab save` / `resume`** | **Lockfiles (+ optional `.pixi`/`.venv` with `--full`)** — **not** your `.py` tree | Warm envs across sessions | `canfar lab save mylab [--full]`; next session `canfar lab resume mylab` then still need mode 1 (or shared `/arc`) for source. |
| **3. VOSpace tarball** | Source **and** usually need deps inside or a matching save | Share a frozen tree without git, or headless cold start | Pack under `vos:$USER/astroai/<name>.tar.zst` (or `arc:`), unpack into `$WORK` on the worker. You own the layout; CLI does not auto-pack yet. |
| **4. Save + VOSpace (2+3)** | Env snapshot + code tarball | Reproducible headless: same env + same source blob | `canfar lab save mylab --full` to home/project; upload code tarball to `vos:$USER/astroai/`; job restores both into `$WORK`. |

**Same-session Ray jobs:** `canfar lab run train.py` packages the script's
directory as Ray `working_dir` (local tree). Still push/save before the
session ends if you need the work later.

**Secure "latest fork" (mode 1):** always `git push origin` from the
interactive session, then on the consumer `canfar lab clone … --update`
(refuses dirty trees unless `--force`). Prefer `--ref <sha>` for pinned
science runs.

```bash
# Interactive session
cd "$WORK/torchsky"          # → /scratch/src/torchsky on CANFAR
git push -u origin HEAD
canfar lab save torchsky        # env only

# New session / headless prelude
canfar lab clone sfabbro/torchsky --update          # latest origin tip + SHA printed
# or: canfar lab clone sfabbro/torchsky --ref abc1234
pixi run python train.py
```

## First project

```bash
canfar lab init mylab
cd "$WORK/mylab"
pixi add numpy
pixi run python -c "import numpy; print(numpy.__version__)"
canfar lab save mylab
```

Clone (needs `gh auth login` once):

```bash
canfar lab clone owner/repo
canfar lab clone owner/a owner/b
canfar lab clone --from-env mylab owner/repo
canfar lab clone owner/repo --update              # refresh existing checkout
canfar lab clone owner/repo --ref topic
canfar lab clone owner/repo --dir ~/src           # persist on /arc/home
canfar lab clone owner/repo --dir /arc/projects/mygroup
```

`save` writes lockfiles to `~/.astroai/lab/saves/` on `/arc/home`. The next
session `resume`s that snapshot — **not** your source tree (see Code
propagation above).

---

## Storage

| Tier | Env / path | Lifetime | Use for |
|------|------------|----------|---------|
| Source | `SRCDIR` (`$SCRATCH/src` on CANFAR; `WORK` is the same path) | Session (survives container OOM) | Code, pixi/uv projects |
| Scratch | `SCRATCH` (`/scratch`) | Session | Datasets, caches |
| Home | `/arc/home/<you>` | Persistent | Config, saves, certs |
| Projects | `/arc/projects/<group>` | Persistent | Shared data and team saves |

```bash
canfar lab status
canfar lab status --all
canfar lab clean --yes          # ~/.cache on home (not scratch caches)
canfar data …
vcp ./local.fits vos:…
```

---

## Ray cluster and jobs

Usual path: one autoscaling manager, then a job with `--cpus`. Same as
AstroAI hub **Start batch compute**.

```bash
canfar lab cluster start                # autoscaling head; Ray adds workers on demand
canfar lab run train.py --cpus 2        # discovers the manager; --cpus spins a worker
canfar lab cluster status
```

Optional: `export CANFAR_RAY_JOBS_ADDRESS=…` overrides discovery (printed by
`cluster start`; unnecessary in other sessions when a manager is Running).
Inside the manager session the default is localhost.
Size the ceiling with `--min-workers` / `--max-workers` / `--cores` / `--ram`
/ `--gpus`. `canfar lab status` is this session's quota, not the cluster.

```bash
canfar lab cluster stop                 # destroys workers AND the manager
canfar lab cluster dashboard            # Ray Dashboard URL
canfar lab jobs list
```

Do not use `ray job submit`. The job command is `canfar lab run`.
Manager memory **≥8 GiB**. Shared data on `/arc`; `/scratch` is per-pod.
More: [containers RAY.md](https://github.com/astroai/canfar-containers/blob/main/docs/RAY.md).

---

## Working with `canfar`

```bash
canfar login
canfar create --name demo terminal
canfar ps
canfar lab open <session-id>
canfar delete <session-id>
cadcget …
vls vos:…
```

`canfar lab status` includes `canfar auth show` and `canfar ps` when `canfar` is on PATH.

---

## Command map

| Goal | Command |
|------|---------|
| Banner | `canfar lab` |
| New project | `canfar lab init NAME` |
| Clone + install | `canfar lab clone REPO` |
| Snapshot env | `canfar lab save [NAME]` |
| Restore env | `canfar lab resume NAME` |
| This session’s quota | `canfar lab status` |
| Free home space | `canfar lab clean` |
| Start Ray cluster | `canfar lab cluster start` |
| Is the cluster up? | `canfar lab cluster status` |
| Run a job | `canfar lab run SCRIPT --cpus N` |
| Jupyter kernel | `canfar lab kernel ensure` |
| Agents | `canfar lab agent setup` / `install` / `verify` |

Flags: [cli.md](cli.md).

Two sessions share `/arc/home` — what is safe to run concurrently and where
agent runtimes live: [concurrency.md](concurrency.md).

---

## Shell completion

```bash
canfar-lab --install-completion bash   # or zsh, fish
canfar lab help -c "agent l"<TAB>
canfar lab agent install <TAB>
```

---

## AI coding agents

Agent configs stay on `/arc` home; CLI binaries install to `$SCRATCH/.local/bin`
(fast local disk — `/arc` NFS is too slow for CLI installs). Skills:
``npx skills add …`` (skills.sh) — not managed by AstroAI. Caches and agent
runtime DBs also use `$SCRATCH`.

```bash
canfar lab agent list
canfar lab agent install kilo
# CLIs land on $SCRATCH/.local/bin (override: CANFAR_LAB_BIN_DIR)
canfar lab agent setup hermes
canfar lab agent setup --all
npx skills add astroai/canfar-skills
canfar lab agent plugins install ray-manager-mcp
canfar lab agent update
canfar lab agent verify --fix
```

Skill packs (SKILL.md) install via **`npx skills`**, not AstroAI plugins. Example:

```bash
npx skills add astroai/canfar-skills
# third-party writing / science skills: npx skills add <owner/repo> …
```

Upgrade lab in a running session (no image rebuild):

```bash
uv pip install --python /opt/astroai/venv/cadc \
  "git+https://github.com/astroai/canfar-lab.git@main"
hash -r
```

---

## Troubleshooting

| Symptom | What to run |
|---------|-------------|
| Paths / caches under `$HOME` | `canfar lab env export` in a login shell (`bash -l`) |
| Env save failed | `canfar lab status` (quota) |
| Cluster not up | `canfar lab cluster status`, then `cluster start` |
| Kernel missing | `canfar lab kernel ensure` |
| `canfar` unknown | You are not on an AstroAI image |
| All help | `canfar lab help` |

---

## See also

- [canfar-containers USAGE](https://github.com/astroai/canfar-containers/blob/main/docs/USAGE.md)
- [CANFAR client docs](https://opencadc.github.io/canfar/)
