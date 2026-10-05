# canfar-lab

The **`canfar lab`** commands you run *inside* an AstroAI session on the
[CANFAR Science Platform](https://www.opencadc.org/canfar/). The same tree is
the standalone `canfar-lab` binary.

Start and stop sessions with [`canfar`](https://github.com/opencadc/canfar)
(or the Science Portal). Inside the session, `canfar lab` does three jobs:

1. **Project env** — `init` / `clone` / `save` / `resume` (lockfiles on `/arc`)
2. **Ray cluster** — `cluster start` / `status` / `run` / `jobs`
3. **Agents** — `agent setup` / `install` / `verify`

```mermaid
flowchart LR
  subgraph laptop [Laptop or portal]
    Portal[Science Portal]
    CanfarCLI["canfar login / create"]
  end
  subgraph session [AstroAI session]
    Lab["canfar lab"]
    Tools["pixi / uv / Jupyter / CADC / Ray"]
  end
  Portal --> session
  CanfarCLI --> session
  Lab --> Tools
```

| Name | Meaning |
|------|---------|
| **AstroAI** | Product: GitHub [`astroai`](https://github.com/astroai), Harbor `astroai` |
| **CANFAR** | Host platform: portal, Skaha, `/arc`, auth |
| **`canfar`** | Platform CLI — sessions and `canfar data` |
| **`canfar lab` / `canfar-lab`** | This package, inside a session |
| **`images.canfar.net/astroai/*`** | Session images |

Images: [canfar-containers](https://github.com/astroai/canfar-containers).

## Inside a session

```bash
canfar lab                      # status banner
canfar lab init mylab           # or: clone owner/repo
canfar lab save                    # lockfile snapshot to /arc
canfar lab resume mylab
canfar lab status                  # quotas, sessions (not the Ray cluster)

canfar lab cluster start
canfar lab cluster status
canfar lab run train.py --cpus 2

canfar lab kernel ensure
canfar lab agent setup
```

`canfar lab status` is this session’s CPU/disk/quota. `canfar lab cluster status`
is whether the Ray cluster is up. `canfar lab cluster stop` tears down workers
and the manager.

Help: `canfar lab help` · one command: `canfar lab help -c cluster` · cheat sheet:
[docs/help.md](docs/help.md)

## Install

Session images put `canfar-lab` on PATH (`canfar lab` when the platform CLI is installed).

```bash
pipx install git+https://github.com/astroai/canfar-lab.git
# or: pip install "git+https://github.com/astroai/canfar-lab.git"
pixi install && pixi run canfar-lab --help   # checkout
```

## Docs

| Doc | What it is |
|-----|------------|
| [docs/help.md](docs/help.md) | Cheat sheet |
| [docs/USAGE.md](docs/USAGE.md) | Storage, workflows, Ray, agents |
| [docs/cli.md](docs/cli.md) | Flags and every command |
| [docs/config.md](docs/config.md) | Optional `~/.astroai/lab/config.yaml` |
| [docs/concurrency.md](docs/concurrency.md) | Shared home: locks, atomic writes, agent runtime placement |
| [docs/studio.md](docs/studio.md) | AstroAI Studio: the browser coding portal, its dsh profile, teams, storage split |
| [docs/panel.md](docs/panel.md) | AstroAI Panel / Studio Team review (keys only; models in dsh Settings) |

Data movement is not this CLI. Use **`canfar data`** and `vcp` / `vls`.

## Development

```bash
./scripts/ci.sh
pixi run test
```

[MIT](LICENSE). `canfar` keeps its own license.
