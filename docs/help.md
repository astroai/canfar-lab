# Session guide

Cheat sheet for work **inside** an AstroAI session.

| Tool | Use it for |
|------|------------|
| [`canfar`](https://github.com/opencadc/canfar) | Log in, create/list/delete sessions, `canfar data` |
| **`canfar lab`** | Project env, Ray cluster and jobs, agents, kernels, status |
| CADC clients (`cadcget`, `vcp`, …) | Archive and VOSpace I/O |

Notebook: Science Portal → **notebook** → `/opt/astroai/notebooks/starter.ipynb`
(`canfar lab kernel ensure` if the kernel is missing).

Marimo: Science Portal → **marimo** → `$WORK/notebooks/starter.py`.

## Storage

| Tier | Path | Purpose |
|------|------|---------|
| Work | `WORK` → `$SCRATCH/src` on CANFAR | Code (survives container OOM; dies with the session) |
| Scratch | `SCRATCH` → `/scratch` | Data and package caches (this session only) |
| Home | `/arc/home` | Config and env saves |
| Projects | `/arc/projects` | Team storage |

Saves default to **`~/.astroai/lab/saves/`**.

## Project env

```text
1. canfar lab resume mylab     # or init / clone  (code under $WORK = /scratch/src)
2. cd $WORK/mylab && pixi run …
3. canfar lab save             # lockfile snapshot to /arc (deps, not source)
```

Refresh source from your fork: `canfar lab clone mylab --update` (ff-only; prints SHA).
See USAGE.md → Code propagation (sessions + jobs).

## Ray jobs

```text
1. canfar lab cluster start
2. canfar lab run train.py --cpus 2
3. canfar lab cluster status
```

Same as AstroAI hub **Start batch compute**. `canfar lab status` is not the cluster.

## Commands

```bash
canfar lab                    # banner
canfar lab init mylab
canfar lab clone owner/repo
canfar lab save [name]
canfar lab save --list
canfar lab resume NAME
canfar lab status                # this session
canfar lab status --all
canfar lab clean                 # home caches; --yes to delete
canfar lab kernel ensure
canfar lab cluster start
canfar lab cluster status
canfar lab cluster stop
canfar lab run train.py --cpus 2
canfar lab jobs list
canfar lab agent setup
canfar lab agent install kilo
canfar lab agent verify
canfar-lab --install-completion bash
```

`canfar lab help` · `canfar lab help -c cluster`

## Platform vs project Python

| Layer | Where | Versioned by |
|-------|-------|--------------|
| Platform CLIs | `/opt/canfar` | Image lock (`canfar lab --version`) |
| Your project | `$WORK` pixi/uv env | `pixi.lock` / `uv.lock` |

```bash
upgrade-cadc-tools.sh --upgrade astroai-lab
```

## More

- [USAGE.md](USAGE.md) — storage, Ray, agents
- [cli.md](cli.md) — flags
- [config.md](config.md) — optional YAML
- [CANFAR docs](https://opencadc.github.io/canfar/)
