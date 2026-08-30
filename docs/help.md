# Session guide

Cheat sheet for work **inside** an AstroAI session.

| Tool | Use it for |
|------|------------|
| [`canfar`](https://github.com/opencadc/canfar) | Log in, create/list/delete sessions, `canfar data` |
| **`astroai`** | Project env, Ray cluster and jobs, agents, kernels, status |
| CADC clients (`cadcget`, `vcp`, …) | Archive and VOSpace I/O |

Notebook: Science Portal → **notebook** → `/opt/astroai/notebooks/starter.ipynb`
(`astroai kernel ensure` if the kernel is missing).

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
1. astroai resume mylab     # or init / clone
2. cd $WORK/mylab && pixi run …
3. astroai save             # lockfile snapshot to /arc
```

## Ray jobs

```text
1. astroai cluster start
2. astroai run train.py --cpus 2
3. astroai cluster status
```

Same as AstroAI hub **Start batch compute**. `astroai status` is not the cluster.

## Commands

```bash
astroai                       # banner
astroai init mylab
astroai clone owner/repo
astroai save [name]
astroai save --list
astroai resume NAME
astroai status                # this session
astroai status --all
astroai clean                 # home caches; --yes to delete
astroai kernel ensure
astroai cluster start
astroai cluster status
astroai cluster stop
astroai run train.py --cpus 2
astroai jobs list
astroai agent setup
astroai agent install kilo
astroai agent verify
astroai --install-completion bash
```

`astroai help` · `astroai help -c cluster`

## Platform vs project Python

| Layer | Where | Versioned by |
|-------|-------|--------------|
| Platform CLIs | `/opt/astroai/venv/cadc` | Image lock (`astroai --version`) |
| Your project | `$WORK` pixi/uv env | `pixi.lock` / `uv.lock` |

```bash
upgrade-cadc-tools.sh --upgrade astroai-lab
```

## More

- [USAGE.md](USAGE.md) — storage, Ray, agents
- [cli.md](cli.md) — flags
- [config.md](config.md) — optional YAML
- [CANFAR docs](https://opencadc.github.io/canfar/)
