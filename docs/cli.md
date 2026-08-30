# CLI reference

Power-user reference for **`astroai`**. Session create/delete and archive I/O
use **`canfar`** — [opencadc.github.io/canfar](https://opencadc.github.io/canfar/).

`astroai` is the in-session CLI: project env (`init` / `save` / `resume`),
Ray cluster and jobs (`cluster` / `run` / `jobs`), agents, kernels, and
session status. `astroai status` is this session’s quota. `astroai cluster status`
is whether the Ray cluster is up.

Global flags (most commands accept these **before** the subcommand, e.g. `astroai --json status`. Several commands also accept the same flags **after** the subcommand name — see examples below):

| Flag | Description |
|------|-------------|
| `--json` | Machine-readable output |
| `--yes` / `-y` | Non-interactive; skip confirmations |
| `--dry-run` | Show actions without executing |
| `--quiet` / `-q` | Minimal output |
| `--version` / `-V` | Show version |

## Top-level commands

### `astroai`

Brief status banner when invoked with no subcommand.

### `astroai init NAME`

Create a pixi project under the work directory.

```bash
astroai init mylab
astroai init mylab --no-git
astroai init mylab --dir ~/src
```

### `astroai clone REPO [REPO…]`

Clone via `gh` and install dependencies. Several repos clone one after another
into `$SRCDIR/<name>`. `--dir` sets the parent source directory (any number of
repos). `--to` is the exact destination for one repo only. A name without `/`
is tried as `$github-user/name`, then `astroai/name`.

```bash
astroai clone myproject
astroai clone owner/repo
astroai clone owner/a owner/b
astroai clone --from-env ml-base owner/repo
astroai clone owner/repo --to $SRCDIR/custom
astroai clone owner/repo --dir ~/src
astroai clone owner/a owner/b --dir /srcdir
astroai clone owner/repo --dir /arc/projects/mygroup
```

### `astroai run SCRIPT`

Run a Python script on the Ray cluster and wait until it finishes. Needs
`ASTROAI_RAY_JOBS_ADDRESS` (printed by `cluster start`; already set inside
the manager). Do not use `ray job submit`.

```bash
astroai run train.py --cpus 2
astroai run train.py --cpus 2 --gpus 1 --memory 8GiB
```

`--cpus` is what makes an autoscaling cluster add a worker.

### `astroai cluster`

| Command | What it does |
|---------|----------------|
| `cluster start` | Start (or reuse) the autoscaling cluster. Writes the manager env file, creates the manager if needed; Ray adds `ray-as-*` workers when a job needs CPUs |
| `cluster status` | Up or not, joined workers, Dashboard URL |
| `cluster stop` | Tear down the whole cluster: workers **and** the manager, plus persisted state |
| `cluster dashboard` | Print the Ray Dashboard URL. `proxy` / `iframe` for notebooks |

`start` options: `--min-workers` (kept alive when idle), `--max-workers`
(ceiling), `--cores`, `--ram`, `--gpus`, `--address`, `--timeout`, `--json`.

```bash
astroai cluster start
astroai cluster start --max-workers 8 --cores 2 --ram 8
astroai cluster start --min-workers 1 --gpus 1 --timeout 1800
astroai cluster status
astroai cluster dashboard
```

`start` is safe to run again (no second manager). If a manager was already
running, the output says so — restart it to pick up new sizing. `--json`
returns `manager_url`, `jobs_address`, `dashboard_url`,
`cluster_phase`, `joined_workers`, `autoscaling`.

### `astroai jobs`

`list` / `status` / `logs` / `wait` / `cancel` / `submit`. Same Jobs API as `run`.

```bash
astroai jobs list
astroai jobs submit --cmd 'python -m mosaic.stack' --wait
astroai jobs logs <id> --follow
```

### `astroai save [NAME]`

Save lockfiles + manifest to `~/.astroai/lab/saves/`, or list snapshots.

```bash
astroai save
astroai save mylab --full
astroai save mylab --to /arc/projects/team/env-saves/mylab
astroai save --list
astroai save --list --json
astroai save --list --from /arc/projects/team/env-saves
```

### `astroai resume NAME`

Restore a saved environment into `$SRCDIR/NAME` (or `--to`) and run install.

```bash
astroai resume mylab
astroai resume mylab --yes
astroai resume mylab --from /arc/projects/team/env-saves
astroai resume mylab --to $SRCDIR/mylab --from /arc/projects/team/env-saves/mylab
```

### `astroai status`

Session CPU, memory, home disk, the team project you are in, and your
CANFAR sessions.

Default view hides groups, other team projects, and disk quotas you are
not using. Home folder sizes stay. `--all` shows everything. `--json` is
always complete.

Home quota uses Ceph directory xattrs (`ceph.quota.max_bytes` + `ceph.dir.rbytes`) when present. `df` on `/arc/home` is the shared filesystem, not the user quota, so it is not used for the home percentage. Home breakdown never recursively walks `~/.cache` (that hangs on Ceph); it uses `rbytes` or a timed `du`.

Remote probes (GMS, VOSpace, `getfacl`, `canfar`) have short timeouts so a stalled CADC call cannot freeze the command. Default `status` skips GMS/vault/listing every `/arc/projects` dir.

```bash
astroai status
astroai status --all
astroai status --json
astroai status -v          # probe timings on stderr
```

**`--json` keys:** `quotas`, `home`, `processes`, `canfar_auth`, `canfar_sessions`, `arc_project`, `arc_projects`, `gms_groups`, `vault`.

Each quota row includes `source` (`ceph-xattr`, `statvfs`, or `vospace`).

Each **`arc_projects[]`** entry includes `access` (`rw`/`ro`), `acl_groups` (from `getfacl`), `gms_member`, optional nested **`vault`** (VOSpace quota/groups), and `quota` (POSIX `df` on `/arc/projects/<name>`).

**`gms_groups`:** `{groups, source}` from `cadc-groups list` when cert/netrc is available, else `null`.

**`vault`:** `{service, source, auth, nodes[]}` from the vos API (`vault:/<name>`). Vault quotas may also appear in `quotas` as `"<name> (vault)"`.

Requires optional tools on PATH: `getfacl`, `cadc-groups` (CADC venv), `vos` — all ship in AstroAI session images.

### `astroai clean`

Delete whatever is in ``~/.cache`` on home (and a few known extra cache
dirs). That directory is listed at run time, so new tools are included
without a code change. Scratch-backed ``XDG_CACHE_HOME`` is left alone.

`--yes` deletes those caches only. They come back the next time you install a
package. Saved environments and lab preferences need `--saves` / `--config`,
or a yes at the prompt. Agent logins are `astroai agent wipe`.

```bash
astroai clean
astroai clean --yes
astroai clean --yes --saves
astroai clean --dry-run
```

### `astroai help`

Print `--help` for the app and every subcommand — the aggregate of all help
output in registration order.

```bash
astroai help                     # full dump (pages via less on a terminal)
astroai help -c agent            # one command only
astroai help --command "agent list"
astroai help --json              # command inventory (machine-readable)
astroai help -c status --json    # structured help for one command
```

Shell completion offers registered command paths for `-c` (bash/zsh/fish via
`astroai --install-completion <shell>`). With `--json`, `help` prints a
command inventory (path, help, options, subcommands) or structured help for a
single `-c` path.

## Nested commands

### `astroai env export`

Session shell infrastructure (applied automatically by `profile.sh` at login).

```bash
eval "$(astroai env export)"
astroai env export --json        # resolved env as a JSON object
astroai --json env export        # same, via the global flag
```

With `--json`, prints the resolved session environment as a JSON object — the
same keys and values as the shell export, without `export KEY=...` syntax (useful
for `jq`, scripts, and tooling). `--no-ensure` skips creating cache/runtime
directories.

Image builds copy the packaged `profile.sh` / `hooks.sh` at build time —
`astroai` itself stays an in-session tool.

### `astroai config show|path`

Optional preferences file.

```bash
astroai config show
astroai config path
```

### `astroai kernel ensure|register|list|unregister`

Jupyter kernels for notebook sessions.

```bash
astroai kernel ensure              # scratch-safe default (no pixi project)
astroai kernel register [PATH]     # project .pixi/.venv as kernel
astroai kernel list
astroai kernel unregister NAME
```

### `astroai agent list|install|remove|wipe|setup|config|update|verify|plugins`

AI agent MCP, rules, skills, CLI installation, and plugins.

**`agent list` is the single installable set.** Every agent is one YAML file
under `data/agent/agents/<id>.yaml` (`id`, `name`, `homepage`, `binary`,
`install`, optional `config`, `verify`). `list` / `install` / `remove` /
`verify` all read that set. CLIs land on `$SCRATCH` (`$ASTROAI_LAB_BIN_DIR`);
configs stay on `$HOME` (/arc/home). Some ids still install via battle-tested
`install.TOOLS` branches (same id appears in the list). CLI utilities such as
`ast-grep` are installed via plugins (`ast-grep-cli`), not listed as agents.
`hyperfine` is image-baked and is not reinstalled.

| Command | What it does |
|---------|----------------|
| `agent list` | Installable agents: installed / logged in / where (scratch, home, image) / version. `--description` for summaries; `--ui` for container endpoints |
| `agent install NAME [NAME…]` | Download CLI binary(ies) onto scratch |
| `agent remove NAME` | Uninstall managed CLI on scratch (`--clean-home` for `$HOME` CLIs; `--purge` for config dirs) |
| `agent wipe` | Factory reset: remove every agent settings file, binary, and state; confirmation or `--yes` |
| `agent setup [NAME…]` | First-run scaffold for an agent id or setup name; `--all` / `--project` |
| `agent config ID` | Show/edit an agent's `$HOME` settings file (`--key`, `key=value`, `--unset`) |
| `agent update [ID]` | Refresh agent configs; with ID refreshes one agent |
| `agent verify` | Health check + drift report (obsolete managed skills, stale plugins, dead MCP paths); `--fix` repairs configs **and** reconciles skills/plugins/paths with this lab version; `--fix ID` for one agent; `--clean` stale state |
| `agent plugins …` | list / install / update / remove extras (skills, MCP, rules, tools). `plugins list` is Kind / On / Def / Agents; `--description` for summaries |

```bash
astroai agent list                 # registered agents
astroai agent list --description
astroai agent list --ui            # container endpoints
astroai --json agent list          # --json is a global flag: BEFORE the subcommand
astroai agent setup
astroai agent setup hermes         # per-agent scaffold
astroai agent setup --all
astroai agent setup --project ./repo   # per-repo AGENTS.md + .cursor
astroai agent install kilo
astroai agent install agy omp pi
astroai agent plugins install ponytail astroai-ray
astroai agent remove kilo          # uninstall (--purge removes ~/.<agent> home dirs)
astroai agent wipe --dry-run
astroai agent wipe --yes
astroai agent plugins list
astroai agent plugins list --description
astroai agent plugins list --kind mcp
astroai agent plugins install astroai-ray
astroai agent plugins install astroai-ray --agent hermes
astroai agent plugins remove astroai-ray
astroai agent plugins install ray-manager-mcp
astroai agent verify
astroai agent verify --fix         # auto-repair, then re-check
astroai agent verify --fix hermes  # regenerate/sanitize ONE agent's settings
astroai agent verify --fix --all
astroai agent verify --clean
astroai agent config hermes
astroai agent config hermes --key model
astroai agent config hermes model=nousresearch/hermes-3-llama-3.1-405b
astroai agent config openclaw --unset model
astroai agent update               # full refresh after image upgrades
astroai agent update hermes
astroai agent update openclaw --reinstall
```

**Agent plugins** (`data/agent/plugins/*.yaml`) are the uniform surface for
skills / MCP servers / config snippets across *all* installed agents.
Each plugin declares a support matrix (`agents:`), a `kind`, and how it is
applied. Generic skills use `agents: [skill-hosts]` (every agent that loads
SKILL.md); MCP plugins use `agents: [mcp-hosts]`. `plugins install <id>`
applies to every *installed* agent in the matrix by default; `--agent` scopes
it. For `kind: mcp` that merge is an `mcpServers` entry with **dynamic URLs
only** (e.g. `$ASTROAI_RAY_JOBS_ADDRESS`).

**`ray-manager-mcp`** configures `astroai mcp serve` (cluster plus
jobs) with `$ASTROAI_RAY_JOBS_ADDRESS` resolved at runtime.

## Not this CLI

Session create/delete and archive I/O belong to **`canfar`**. Notebook
starters ship in the image at `/opt/astroai/notebooks/`.

## Environment variables

`astroai` speaks the same storage vocabulary as typical HPC/Slurm clusters:
`SRCDIR`, `SCRATCH`, and `PROJECT` are the session path names. `WORK` is the
same path as `SRCDIR` (kept as a synonym). Session paths are applied in login
shells via `astroai env export` (bundled in `/etc/astroai-lab/profile.sh` on
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
| `ASTROAI_LAB_WORK_ON_SCRATCH` | Set `0` to keep `SRCDIR` on the container overlay (`/srcdir`) instead of `$SCRATCH/src` |
| `ASTROAI_LAB_SAVE_DIR` | Env saves dir (default: `~/.astroai/lab/saves`) |
| `ASTROAI_LAB_BIN_DIR` | User CLI install dir (default: scratch `.local/bin`; last resort: work `.runtime-$USER/bin` — never `~/.local`) |
| `ASTROAI_LAB_RUNTIME_ROOT` | Runtime uv/pixi/mamba roots (default: scratch `.runtime-$USER`) |
| `ASTROAI_LAB_NPM_PREFIX` | npm global prefix (default: `.local` under scratch) |
| `NPM_CONFIG_PREFIX` | Fallback npm prefix when `ASTROAI_LAB_NPM_PREFIX` is unset |
| `ASTROAI_LAB_CONFIG_DIR` | Workbench config dir (default: `~/.astroai/lab`) |
| `ASTROAI_LAB_PYTHONPATH` | Extra `PYTHONPATH` entries (colon-separated) |
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

These are redirected to `ASTROAI_LAB_RUNTIME_ROOT` (default: scratch
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
| `ASTROAI_LAB_DEFAULT_PM` | Default package manager: `pixi` or `uv` (default: `pixi`) |
| `ASTROAI_LAB_CLONE_FROM_ENV` | Default env preset for `astroai clone` |

### AI agent management

| Variable | Purpose |
|----------|---------|
| `ASTROAI_LAB_AGENT_BUNDLE` | Override the agent bundle root |
| `ASTROAI_LAB_AGENT_GIT_TIMEOUT` | Git-op timeout, seconds (default: `120`) |
| `ASTROAI_LAB_AGENT_INSTALL_TIMEOUT` | CLI-install timeout, seconds (default: `1500`; self-bootstrapping installers like hermes need more than 300) |
| `ASTROAI_LAB_AGENT_LOCK_TIMEOUT` | Setup-lock timeout, seconds (default: `30`) |
| `ASTROAI_SESSION_KIND` | Session kind label for `agent list --ui` (default: `unknown`) |
| `ASTROAI_AGENT_WIZARD_PORT` | Agent wizard port (default: `4792`) |
| `ASTROAI_OPENWORKER_PORT` | OpenWorker port (default: `5000`) |

### Shell integration

| Variable | Purpose |
|----------|---------|
| `ASTROAI_LAB_SHELL_DIR` | Dir holding `profile.sh`/`hooks.sh` (default: `/etc/astroai-lab`) |
| `ASTROAI_LAB_PROFILE_LOADED` | Set by `profile.sh` to avoid double-sourcing |
| `JUPYTER_CONFIG_DIR` | Jupyter config dir (default: `~/.jupyter`) |
| `USER` / `HOSTNAME` | Identity labels used by `status` and `agent list --ui` |

`astroai env export` also **emits** derived values for downstream tools,
including `ASTROAI_LAB_TEAM_BIN` (when a team project is present),
`ASTROAI_LAB_PATH_PREFIX` (consumed by the image's `/etc/profile.d/astroai.sh`),
`UV_PYTHON_BIN_DIR`, `UV_TOOL_BIN_DIR` (both pointing at `ASTROAI_LAB_BIN_DIR`),
`PYTHONUSERBASE`, `TRANSFORMERS_CACHE`, `HF_DATASETS_CACHE`, and
`MPLCONFIGDIR`.

See [config.md](config.md) for optional YAML preferences.
