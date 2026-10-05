# CLI reference

Power-user reference for **`canfar lab`** (standalone `canfar-lab`). Session create/delete and archive I/O
use **`canfar`** — [opencadc.github.io/canfar](https://opencadc.github.io/canfar/).

`canfar lab` is the in-session CLI: project env (`init` / `save` / `resume`),
Ray cluster and jobs (`cluster` / `run` / `jobs`), agents, kernels, and
session status. `canfar lab status` is this session’s quota. `canfar lab cluster status`
is whether the Ray cluster is up.

Global flags (most commands accept these **before** the subcommand, e.g. `canfar lab --json status`. Several commands also accept the same flags **after** the subcommand name — see examples below):

| Flag | Description |
|------|-------------|
| `--json` | Machine-readable output |
| `--yes` / `-y` | Non-interactive; skip confirmations |
| `--dry-run` | Show actions without executing |
| `--quiet` / `-q` | Minimal output |
| `--version` / `-V` | Show version |

## Top-level commands

### `canfar lab`

Brief status banner when invoked with no subcommand.

### `canfar lab init NAME`

Create a pixi or uv project under the work directory.

```bash
canfar lab init mylab
canfar lab init mylab --uv --no-git
canfar lab init mylab --dir ~/src
```

### `canfar lab clone REPO [REPO…]`

Clone via `gh` into `$WORK` (on CANFAR: `$SCRATCH/src`) and install dependencies.
Bare names prefer **your GitHub user fork**, then `astroai/`. Several repos clone
one after another.

```bash
canfar lab clone myproject
canfar lab clone sfabbro/torchsky
canfar lab clone sfabbro/torchsky --update          # fetch+ff origin tip if dest exists
canfar lab clone sfabbro/torchsky --ref topic   # or a commit SHA
canfar lab clone sfabbro/torchsky --update --ref abc1234
canfar lab clone --from-env ml-base owner/repo
canfar lab clone owner/repo --to $SRCDIR/custom
```

`--update` is how jobs/sessions **securely refresh** to the latest pushed tip on
`origin` (fast-forward only; refuses dirty trees unless `--force`). Prints the
short HEAD SHA after clone/update. Forks get an `upstream` remote when GitHub
reports a parent. `--dir` sets the parent source directory (any number of
repos). `--to` is the exact destination for one repo only.

```bash
canfar lab clone owner/a owner/b
canfar lab clone owner/repo --dir ~/src
canfar lab clone owner/repo --dir /arc/projects/mygroup
```

### `canfar lab run SCRIPT`

Run a Python script on the Ray cluster and wait until it finishes. Discovers
the Running ray-manager automatically (or uses `CANFAR_RAY_JOBS_ADDRESS` /
`--address` if set). Inside the manager, localhost is the default. Do not use
`ray job submit`.

```bash
canfar lab run train.py --cpus 2
canfar lab run train.py --cpus 2 --gpus 1 --memory 8GiB
```

`--cpus` is what makes an autoscaling cluster add a worker.

### `canfar lab cluster`

| Command | What it does |
|---------|----------------|
| `cluster start` | Start (or reuse) the autoscaling cluster. Writes the manager env file, creates the manager if needed; Ray adds `ray-as-*` workers when a job needs CPUs |
| `cluster status` | Up or not, joined workers, Dashboard URL |
| `cluster stop` | Tear down the whole cluster: workers **and** the manager, plus persisted state |
| `cluster dashboard` | Print the Ray Dashboard URL. `proxy` / `iframe` for notebooks |

`start` options: `--min-workers` (kept alive when idle), `--max-workers`
(ceiling), `--cores`, `--ram`, `--gpus`, `--address`, `--timeout`, `--json`.

```bash
canfar lab cluster start
canfar lab cluster start --max-workers 8 --cores 2 --ram 8
canfar lab cluster start --min-workers 1 --gpus 1 --timeout 1800
canfar lab cluster status
canfar lab cluster dashboard
```

`start` is safe to run again (no second manager). If a manager was already
running, the output says so — restart it to pick up new sizing. `--json`
returns `manager_url`, `jobs_address`, `dashboard_url`,
`cluster_phase`, `joined_workers`, `autoscaling`.

### `canfar lab jobs`

`list` / `status` / `logs` / `wait` / `cancel` / `submit`. Same Jobs API as `run`.

```bash
canfar lab jobs list
canfar lab jobs submit --cmd 'python -m mosaic.stack' --wait
canfar lab jobs logs <id> --follow
```

### `canfar lab save [NAME]`

Save lockfiles + manifest to `~/.astroai/lab/saves/`, or list snapshots.

```bash
canfar lab save
canfar lab save mylab --full
canfar lab save mylab --to /arc/projects/team/env-saves/mylab
canfar lab save --list
canfar lab save --list --json
canfar lab save --list --from /arc/projects/team/env-saves
```

### `canfar lab resume NAME`

Restore a saved environment into `$SRCDIR/NAME` (or `--to`) and run install.

```bash
canfar lab resume mylab
canfar lab resume mylab --yes
canfar lab resume mylab --from /arc/projects/team/env-saves
canfar lab resume mylab --to $SRCDIR/mylab --from /arc/projects/team/env-saves/mylab
```

### `canfar lab status`

Session CPU, memory, home disk, the team project you are in, and your
CANFAR sessions.

Default view hides groups, other team projects, and disk quotas you are
not using. Home folder sizes stay. `--all` shows everything. `--json` is
always complete.

Home quota uses Ceph directory xattrs (`ceph.quota.max_bytes` + `ceph.dir.rbytes`) when present. `df` on `/arc/home` is the shared filesystem, not the user quota, so it is not used for the home percentage. Home breakdown never recursively walks `~/.cache` (that hangs on Ceph); it uses `rbytes` or a timed `du`.

Remote probes (GMS, VOSpace, `getfacl`, `canfar`) have short timeouts so a stalled CADC call cannot freeze the command. Default `status` skips GMS/vault/listing every `/arc/projects` dir.

```bash
canfar lab status
canfar lab status --all
canfar lab status --json
canfar lab status -v          # probe timings on stderr
```

**`--json` keys:** `quotas`, `home`, `processes`, `canfar_auth`, `canfar_sessions`, `arc_project`, `arc_projects`, `gms_groups`, `vault`.

Each quota row includes `source` (`ceph-xattr`, `statvfs`, or `vospace`).

Each **`arc_projects[]`** entry includes `access` (`rw`/`ro`), `acl_groups` (from `getfacl`), `gms_member`, optional nested **`vault`** (VOSpace quota/groups), and `quota` (POSIX `df` on `/arc/projects/<name>`).

**`gms_groups`:** `{groups, source}` from `cadc-groups list` when cert/netrc is available, else `null`.

**`vault`:** `{service, source, auth, nodes[]}` from the vos API (`vault:/<name>`). Vault quotas may also appear in `quotas` as `"<name> (vault)"`.

Requires optional tools on PATH: `getfacl`, `cadc-groups` (CADC venv), `vos` — all ship in AstroAI session images.

### `canfar lab clean`

Delete whatever is in ``~/.cache`` on home (and a few known extra cache
dirs). That directory is listed at run time, so new tools are included
without a code change. Scratch-backed ``XDG_CACHE_HOME`` is left alone.

`--yes` deletes those caches only. They come back the next time you install a
package. Saved environments and lab preferences need `--saves` / `--config`,
or a yes at the prompt. Agent logins are `canfar lab agent wipe`.

```bash
canfar lab clean
canfar lab clean --yes
canfar lab clean --yes --saves
canfar lab clean --dry-run
```

### `canfar lab studio`

AstroAI Studio — the browser coding portal built on DeepSeek Harness. Boots an
AstroAI-owned dsh profile (`astroai`), not the shipped `web` profile, so the
deployment adds the Agent Teams layers, routes session state off a
quota-constrained `/arc` home, registers the managed preset root and mounts the
`canfar lab mcp serve` server. Full guide: [studio.md](studio.md).

| Flag | What it does |
|------|----------------|
| `REPO` | Workspace directory (default: cwd) |
| `--port N` | Web port (default 3080) |
| `--profile laptop\|canfar` | Resource profile (default: auto-detect from Skaha env) |
| `--prepare` | Provision everything and exit |
| `--doctor` | Pre-flight dsh, profile, composition, skills, providers, MCP; exit non-zero on a blocking problem |
| `--no-team` | Skip the experimental Agent Teams layers |
| `--no-install` | Never fetch bundles (offline / image-baked setups) |
| `--mcp-bin PATH` | Pin the `canfar-lab` binary the Studio MCP row runs (remembered in `~/.astroai/lab/.env`) |
| `--skills` | Print skills.sh / agentskills onboarding |
| `--force` | Overwrite managed scaffolding |

```bash
canfar lab studio                          # laptop, cwd
canfar lab studio ~/src/astroai/torchsky   # explicit workspace
canfar lab studio --prepare                # provision only
canfar lab --json studio --doctor          # machine-readable pre-flight
canfar lab studio --prepare --profile canfar
```

Studio's `--profile` selects *resources* (`laptop` / `canfar`) and is never
passed to dsh; the harness profile is always `astroai`. See
[docs/studio.md](studio.md) for the storage split, the teams, the provider
seeding, and the CANFAR proxy.

### `canfar lab help`

Print `--help` for the app and every subcommand — the aggregate of all help
output in registration order.

```bash
canfar lab help                     # full dump (pages via less on a terminal)
canfar lab help -c agent            # one command only
canfar lab help --command "agent list"
canfar lab help --json              # command inventory (machine-readable)
canfar lab help -c status --json    # structured help for one command
```

Shell completion offers registered command paths for `-c` (bash/zsh/fish via
`canfar-lab --install-completion <shell>`). With `--json`, `help` prints a
command inventory (path, help, options, subcommands) or structured help for a
single `-c` path.

## Nested commands

### `canfar lab env export`

Session shell infrastructure (applied automatically by `profile.sh` at login).
Fast on purpose: path/cache exports only, plus a Ray Jobs address from env or
a persisted ``connect-url`` — never a live ``canfar ps`` (that blocked every
interactive shell for ~10–15s). Live discovery stays on ``canfar lab run`` /
``cluster`` / jobs.

```bash
eval "$(canfar lab env export)"
canfar lab env export --json        # resolved env as a JSON object
canfar lab --json env export        # same, via the global flag
```

With `--json`, prints the resolved session environment as a JSON object — the
same keys and values as the shell export, without `export KEY=...` syntax (useful
for `jq`, scripts, and tooling). `--no-ensure` skips creating cache/runtime
directories (profile uses this; ``common-init`` still ensures once).

Image builds copy the packaged `profile.sh` / `hooks.sh` at build time —
`canfar-lab` itself stays an in-session tool.

### `canfar lab config show|path`

Optional preferences file.

```bash
canfar lab config show
canfar lab config path
```

### `canfar lab kernel ensure|register|list|unregister`

Jupyter kernels for notebook sessions.

```bash
canfar lab kernel ensure              # scratch-safe default (no pixi project)
canfar lab kernel register [PATH]     # project .pixi/.venv as kernel
canfar lab kernel list
canfar lab kernel unregister NAME
```

### `canfar lab agent list|install|remove|wipe|setup|config|update|verify|plugins`

AI agent MCP, rules, CLI installation, and plugins (skills via ``npx skills``).

**`agent list` is the single installable set.** Every agent is one YAML file
under `data/agent/agents/<id>.yaml` (`id`, `name`, `homepage`, `binary`,
`install`, optional `config`, `verify`). `list` / `install` / `remove` /
`verify` all read that set. CLIs land on `$SCRATCH/.local/bin`
(`CANFAR_LAB_BIN_DIR`); configs stay on `$HOME` (/arc/home). Skills via
``npx skills`` (not AstroAI). Some ids still install via battle-tested
`install.TOOLS` branches (same id appears in the list). CLI utilities such as
`ast-grep` are installed via plugins (`ast-grep-cli`), not listed as agents.
`hyperfine` is image-baked and is not reinstalled.

| Command | What it does |
|---------|----------------|
| `agent list` | Installable agents: installed / logged in / where (scratch, home, image) / version. `--description` for summaries; `--ui` for container endpoints |
| `agent install NAME [NAME…]` | Download CLI binary(ies) onto `$SCRATCH/.local/bin` |
| `agent remove NAME` | Uninstall managed CLI (`--purge` for config dirs; `--clean-home` for leftover `/arc` home CLIs) |
| `agent wipe` | Factory reset: remove every agent settings file, binary, and state; confirmation or `--yes` |
| `agent setup [NAME…]` | First-run scaffold for an agent id or setup name; `--all` / `--project` |
| `agent config ID` | Show/edit an agent's `$HOME` settings file (`--key`, `key=value`, `--unset`) |
| `agent update [ID]` | Refresh agent configs; with ID refreshes one agent |
| `agent verify` | Health check + drift report (legacy `.astroai-managed` skills, stale plugins, dead MCP paths); `--fix` repairs configs **and** reconciles legacy skills/plugins/paths; `--fix ID` for one agent; `--clean` stale state |
| `agent plugins …` | list / install / update / remove MCP, rules, and tools. `plugins list` is Kind / On / Def / Agents; `--description` for summaries |

```bash
canfar lab agent list                 # registered agents
canfar lab agent list --description
canfar lab agent list --ui            # container endpoints
canfar lab --json agent list          # --json is a global flag: BEFORE the subcommand
canfar lab agent setup
npx skills add astroai/canfar-skills   # CANFAR platform skills
canfar lab agent setup hermes         # per-agent scaffold
canfar lab agent setup --all
canfar lab agent setup --project ./repo   # per-repo AGENTS.md + .cursor
canfar lab agent install kilo
canfar lab agent install agy omp pi
canfar lab agent plugins install ray-manager-mcp skore-cli
canfar lab agent remove kilo          # uninstall (--purge removes ~/.<agent> home dirs)
canfar lab agent wipe --dry-run
canfar lab agent wipe --yes
canfar lab agent plugins list
canfar lab agent plugins list --description
canfar lab agent plugins list --kind mcp
canfar lab agent plugins install ray-manager-mcp
canfar lab agent plugins install ray-manager-mcp --agent hermes
canfar lab agent plugins remove ray-manager-mcp
canfar lab agent verify
canfar lab agent verify --fix         # auto-repair, then re-check
canfar lab agent verify --fix hermes  # regenerate/sanitize ONE agent's settings
canfar lab agent verify --fix --all
canfar lab agent verify --clean
canfar lab agent config hermes
canfar lab agent config hermes --key model
canfar lab agent config hermes model=nousresearch/hermes-3-llama-3.1-405b
canfar lab agent config openclaw --unset model
canfar lab agent update               # full refresh after image upgrades
canfar lab agent update hermes
canfar lab agent update openclaw --reinstall
```

**Agent plugins** (`data/agent/plugins/*.yaml`) configure MCP servers, CLI
tools, and Cursor rules across *all* installed agents. Skills (SKILL.md)
install via **`npx skills`** / skills.sh — AstroAI does not manage them.
Each plugin declares a support matrix (`agents:`), a `kind` (`mcp` / `tool` /
`rule`), and how it is applied. MCP plugins use `agents: [mcp-hosts]`.
`plugins install <id>` applies to every *installed* agent in the matrix by
default; `--agent` scopes it. For `kind: mcp` that merge is an `mcpServers`
entry with **dynamic URLs only** (e.g. `$CANFAR_RAY_JOBS_ADDRESS`).

**`ray-manager-mcp`** configures `canfar lab mcp serve` (cluster plus
jobs) with `$CANFAR_RAY_JOBS_ADDRESS` resolved at runtime.

## Not this CLI

Session create/delete and archive I/O belong to **`canfar`**. Notebook
starters ship in the image at `/opt/astroai/notebooks/`.

## Environment variables

`canfar lab` speaks the same storage vocabulary as typical HPC/Slurm clusters:
`SRCDIR`, `SCRATCH`, and `PROJECT` are the session path names. `WORK` is the
same path as `SRCDIR` (kept as a synonym). Session paths are applied in login
shells via `canfar lab env export` (bundled in `/etc/astroai-lab/profile.sh` on
CANFAR images). Skaha sessions provide `TMP_SRC_DIR`/`TMP_SCRATCH_DIR`, which
the profile maps onto `SRCDIR`/`SCRATCH`. `PROJECT` is detected from the
current dir under `/arc/projects` or set explicitly.

### Session paths

| Variable | Purpose |
|----------|---------|
| `SRCDIR` | Source directory for `clone` / `init` / `resume`. Default on CANFAR: `$SCRATCH/src` (survives container OOM; still dies with the session). Set to `/srcdir`, `~/src`, `/arc/projects/<group>`, … |
| `WORK` | Alias of `SRCDIR` (same value) |
| `SCRATCH` | Session scratch; data, caches, runtime installs (Skaha: `/scratch`) |
| `PROJECT` | Team project dir (e.g. `/arc/projects/<group>`); used for team tools |

### Path overrides

| Variable | Purpose |
|----------|---------|
| `SRCDIR` / `WORK` / `SCRATCH` / `PROJECT` | Set explicitly to override detected session paths. `SRCDIR` wins over `WORK` |
| `CANFAR_LAB_WORK_ON_SCRATCH` | Set `0` to keep `SRCDIR` on the container overlay (`/srcdir`) instead of `$SCRATCH/src` |
| `CANFAR_LAB_SAVE_DIR` | Env saves dir (default: `~/.astroai/lab/saves`) |
| `CANFAR_LAB_BIN_DIR` | User CLI install dir (default: `$SCRATCH/.local/bin`) |
| `CANFAR_LAB_RUNTIME_ROOT` | Runtime uv/pixi/mamba roots (default: scratch `.runtime-$USER`) |
| `CANFAR_LAB_NPM_PREFIX` | npm global prefix (default: parent of `CANFAR_LAB_BIN_DIR`) |
| `NPM_CONFIG_PREFIX` | Fallback npm prefix when `CANFAR_LAB_NPM_PREFIX` is unset |
| `CANFAR_LAB_CONFIG_DIR` | Workbench config dir (default: `~/.astroai/lab`) |
| `CANFAR_LAB_PYTHONPATH` | Extra `PYTHONPATH` entries (colon-separated) |
| `PYTHONPATH` | Existing entries are preserved and merged into the export |

### XDG, cache, and runtime dirs

Defaults below apply when scratch is mounted (the CANFAR session case). Without
scratch, caches go under `$WORK/.cache-$USER`, never `$HOME`. If `$WORK` itself
is on home, they go to `/tmp/.cache-$USER`.

| Variable | Purpose |
|----------|---------|
| `XDG_CONFIG_HOME` | XDG config base (default: `~/.config`) |
| `XDG_DATA_HOME` | XDG data base (default: `~/.local/share`) |
| `XDG_CACHE_HOME` | XDG cache base (default: scratch cache root) |
| `UV_CACHE_DIR` | `uv` cache (default: scratch `uv/`) |
| `PIP_CACHE_DIR` | `pip` cache (default: scratch `pip/`) |
| `PIXI_CACHE_DIR` | `pixi` cache (default: scratch `pixi/`) |
| `RATTLER_CACHE_DIR` | rattler/pixi package cache (default: scratch `rattler/`) |
| `NPM_CONFIG_CACHE` | npm cache (default: scratch `npm/`) |
| `HF_HOME` | Hugging Face cache (default: scratch `huggingface/`) |
| `TORCH_HOME` | PyTorch cache (default: scratch `torch/`) |
| `TMPDIR` | Temp dir (default: scratch `.tmp-$USER`) |
| `UV_LINK_MODE` | `uv` link mode (default: `copy`) |

### Runtime roots and conda cache (uv/pixi/mamba)

These are redirected to `CANFAR_LAB_RUNTIME_ROOT` (default: scratch
`.runtime-$USER`) at session time, even though the image sets system-prefix
build-time defaults.

| Variable | Purpose |
|----------|---------|
| `PIXI_HOME` | pixi home (default: runtime `pixi/`) |
| `MAMBA_ROOT_PREFIX` | micromamba root (default: runtime `micromamba/`) |
| `UV_PYTHON_INSTALL_DIR` | uv-managed Pythons (default: runtime `uv/python/`) |
| `UV_TOOL_DIR` | uv tool installs (default: runtime `uv/tools/`) |
| `MAMBA_PKGS_DIRS` / `CONDA_PKGS_DIRS` | conda package cache (default: scratch `conda/pkgs/`) |

### Preferences (also settable in `config.yaml`)

| Variable | Purpose |
|----------|---------|
| `CANFAR_LAB_DEFAULT_PM` | Default package manager: `pixi` or `uv` (default: `pixi`) |
| `CANFAR_LAB_CLONE_FROM_ENV` | Default env preset for `canfar lab clone` |

### AI agent management

| Variable | Purpose |
|----------|---------|
| `CANFAR_LAB_AGENT_BUNDLE` | Override the agent bundle root |
| `CANFAR_LAB_AGENT_GIT_TIMEOUT` | Git-op timeout, seconds (default: `120`) |
| `CANFAR_LAB_AGENT_INSTALL_TIMEOUT` | CLI-install timeout, seconds (default: `1500`; self-bootstrapping installers like hermes need more than 300) |
| `CANFAR_LAB_AGENT_LOCK_TIMEOUT` | Setup-lock timeout, seconds (default: `30`) |
| `ASTROAI_SESSION_KIND` | Session kind label for `agent list --ui` (default: `unknown`) |
| `ASTROAI_AGENT_WIZARD_PORT` | Agent wizard port (default: `4792`) |
| `ASTROAI_OPENWORKER_PORT` | OpenWorker port (default: `5000`) |

### Shell integration

| Variable | Purpose |
|----------|---------|
| `CANFAR_LAB_SHELL_DIR` | Dir holding `profile.sh`/`hooks.sh` (default: `/etc/astroai-lab`) |
| `CANFAR_LAB_PROFILE_LOADED` | Set by `profile.sh` to avoid double-sourcing |
| `JUPYTER_CONFIG_DIR` | Jupyter config dir (default: `~/.jupyter`) |
| `USER` / `HOSTNAME` | Identity labels used by `status` and `agent list --ui` |

`canfar lab env export` also **emits** derived values for downstream tools,
including `CANFAR_LAB_TEAM_BIN` (when a team project is present),
`CANFAR_LAB_PATH_PREFIX` (consumed by the image's `/etc/profile.d/astroai.sh`),
`UV_PYTHON_BIN_DIR`, `UV_TOOL_BIN_DIR` (both pointing at `CANFAR_LAB_BIN_DIR`),
`PYTHONUSERBASE`, `TRANSFORMERS_CACHE`, `HF_DATASETS_CACHE`, and
`MPLCONFIGDIR`.

See [config.md](config.md) for optional YAML preferences.
