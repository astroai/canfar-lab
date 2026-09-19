# AstroAI Studio

A browser coding and analysis portal built on the upstream
[DeepSeek Harness](https://github.com/deepseek-ai/deepseek-harness) (`dsh`).
One command on a laptop, the contributed `astroai/studio` session image on
CANFAR, the same composition either way.

```bash
astroai studio                    # cwd, laptop profile
astroai studio ~/src/astroai/torchsky --port 3080
astroai studio --prepare          # provision everything, launch nothing
astroai studio --doctor           # pre-flight every dependency, exit
astroai studio --skills           # skills.sh / agentskills onboarding
```

The CANFAR image and its Skaha proxy are documented in
[canfar-containers docs/STUDIO.md](https://github.com/astroai/canfar-containers/blob/main/docs/STUDIO.md)
(`images.canfar.net/astroai/studio`).

## What Studio adds over plain `dsh web`

Studio boots **its own dsh profile**, `astroai` (not the shipped `web`), so the
composition is ours to define:

| Layer | What it brings |
|---|---|
| `dsh-base` + `dsh-web-app` | the shipped browser composition: chat, sessions, models, approvals, sandbox, KaTeX markdown |
| `dsh-experimental-agent-team-profile` | the Agent Teams domain: roster, durable mailbox, shared task board *(opt-in upstream; no shipped profile enables it)* |
| `dsh-experimental-agent-team-web-profile` | the roster, task board and teammate navigation in the browser |
| the profile's own `cordis.patch.yml` | AstroAI storage routing, the managed preset root, the bash timeout, the `astroai mcp serve` row |

Everything else comes from upstream and is deliberately not re-implemented:

- **Markdown and LaTeX** — the web client renders GFM plus TeX math with KaTeX,
  including `\( \)` and `\[ \]` delimiters, and parses streaming text as plain
  GFM so incomplete TeX never flashes a KaTeX error.
- **Models and providers** — **Settings → Models** adds a built-in or custom
  provider, discovers its models, and stores the key in
  `$DSH_HOME/.credentials.yaml` while every other knob lives in
  `$DSH_HOME/settings.yaml`. Changes apply on the next request, no restart.
- **Workflows** — dsh already ships the Workflow capability (a model-written
  orchestration script that starts subagents) and a Chat node that renders the
  run. Studio enables nothing extra for it.

## Where things live

`astroai studio --prepare` writes, idempotently:

```
$DSH_HOME/profiles/astroai/package.json        # manifest: dsh.profile.bundles IS the layer order
$DSH_HOME/profiles/astroai/cordis.patch.yml    # the Studio layer (generated)
$DSH_HOME/profiles/astroai/pnpm-workspace.yaml # nodeLinker: hoisted
$DSH_HOME/cordis.patch.yml                     # machine layer: bash-sandbox timeoutMs
~/.astroai/lab/studio/studio-profile.yaml      # the resolved resource profile
~/.astroai/lab/review-bench/                   # managed presets, skills, bin
```

`$DSH_HOME` is `~/.dsh` unless the environment overrides it. All of the above
is **durable configuration** and stays on `$HOME`.

### State, and the CANFAR storage split

Session logs, the full-text index and spill files are append-heavy and
unbounded, so they do not live in the harness home:

| Profile | State root | Lifetime |
|---|---|---|
| `laptop` | `~/.dsh/state` | durable |
| `canfar` | `/scratch/<user>/.studio-<user>` | dies with the session |

That is the same policy `core/home_layout.py` already applies to the Claude Code
runtime directories: config on `$HOME`, runtime on scratch. Without a writable
`/scratch`, Studio falls back to `TMPDIR` and says so — it never fills a
quota-constrained `/arc` home by accident.

On CANFAR, `/scratch` is per-session and invisible to other pods. Anything that
must outlive the session belongs under `/arc`:

```
/arc/home/<you>            # persistent, quota-constrained: config only
/arc/projects/<group>      # persistent, shared: results worth keeping
/scratch/...               # this session only: state, caches, working data
```

Export a session you want to keep before shutting down; `astroai studio --doctor`
reminds you with the resolved state path.

## Teams

Two delegation mechanisms, deliberately layered — pick one per task.

**Specialist consults — the `AstroAI Studio Team` preset.** New session → preset
**AstroAI Studio Team** gives a chaired team of fourteen personas, each reachable
through its own tool (`ask_statistician`, `ask_canfar_expert`, `ask_plot_master`,
`ask_devils_advocate`, …) with its own pinned model and a research-and-run tool
view. Use it for a review, a referee report, or an adversarial pass. The protocol
is the `review-panel` skill.

**Agent Teams — the durable roster.** Ask for a team in the chat and the Lead
creates named teammates that share this working tree, message each other, and
track work on a task board whose `blockedBy` edges form a real DAG. Messages and
task state survive reloads, so a teammate that was offline gets its queued
messages when it resumes. Use it for work that outlives one turn.

The `astroai-team-charter` skill is the shared playbook for both: choosing
between them, the standard role roster, the six-part brief a delegated member
needs, why teammates must never be handed a paraphrase of the artefact, and the
escalation ladder to real compute.

Upstream caveat: the Team layers are published under experimental names with no
stability guarantee, and the stable Web presets still mount the legacy child
controls, so a session can show both surfaces. Teams are one `--no-team` flag
away from being off.

## Skills

dsh discovers skills from these roots, in precedence order:

| Rank | Root |
|---|---|
| 100 | `<repo>/.dsh/skills` (per project) |
| 200 | `<repo>/.agents/skills` |
| 300 | the managed AstroAI pack: `~/.astroai/lab/review-bench/skills` |
| 400 | `$DSH_HOME/skills` |
| 500 | `~/.agents/skills` |
| 600 | bundled |

```bash
npx skills add astroai/canfar-skills    # 23 CANFAR platform skills
```

`astroai studio --doctor` verifies discovery rather than trusting the install.

## Models and providers

`--prepare` seeds the routes declared in
[`support.yaml`](https://github.com/astroai/canfar-lab/blob/main/src/astroai_lab/data/agent/support.yaml)
into `$DSH_HOME/settings.yaml`: the DeepSeek route and the catalog routes
(`openai`, `anthropic`, `google`) as credential references, and any route dsh's
installed catalog does not ship — currently OpenCode Zen — as a full
hand-declared provider with its protocol, endpoint and models, because dsh
refuses an unserviceable route where it is written.

Keys are never written into a patch or a profile; they come from the
environment, `~/.astroai/lab/.env`, or `$DSH_HOME/.credentials.yaml`. Add a
gateway by hand in **Settings → Add a custom provider**, or declare it once in
`support.yaml` so every machine and image gets it.

## CANFAR

```bash
canfar create --name studio contributed images.canfar.net/astroai/studio:26.09
```

Connect URL → the Studio UI. The blue **AstroAI** chip opens the agent hub.

dsh refuses to bind anything but loopback, so the image runs `dsh` on
`127.0.0.1:3080` and a public proxy on `0.0.0.0:5000` that rewrites asset paths,
prepends `/session/contrib/<id>` in an early fetch/WebSocket shim, forwards the
browser `Host`, and splices WebSocket upgrades. Studio passes every authority
that reaches the deployment through `--trusted-host`, because dsh's `/api` fence
refuses a `Host` it does not know:

```bash
export ASTROAI_STUDIO_TRUSTED_HOST=ws-uv.canfar.net   # comma/space separated
```

An image that serves several hostnames can bake them instead, one per line, in
`$DSH_HOME/studio-trusted-hosts`; the pod hostname is always trusted, and
`ASTROAI_STUDIO_DSH` points Studio at a specific `dsh` binary when PATH is not
the answer.

Do **not** put Studio behind the vscode or notebook `/proxy/3080/` path: dsh
builds absolute `/api` URLs, which escape that prefix.

## Jobs and workloads

The `astroai mcp serve` row gives a Studio session the same CANFAR tooling as the
CLI:

| Tool | What it does |
|---|---|
| `cluster_start` / `cluster_status` / `cluster_stop` / `dashboard_url` | the autoscaling Ray cluster |
| `job_submit` / `job_run` | start a command or script, waiting or not |
| `job_status` / `job_logs` / `job_cancel` / `job_list` | per-run lifecycle |
| `session_resources` | this session's CPU / RAM / GPU / scratch / home headroom |
| `jobs_report` | a markdown report over every job, ready to paste into a reply |

So a chat can plan, launch, monitor and report on real batch compute; the task
board's `blockedBy` graph is the visible plan, and the submitted jobs are the
execution. Check `session_resources` before promising throughput — an
interactive session is capped, and heavy or GPU work belongs on the cluster.

The row is written at `--prepare` time with the absolute path of the `astroai`
that ran it, so it survives login nodes without the venv's `bin` on their
`PATH`. That also means upgrading the CLI does not retarget an existing profile:
re-run `astroai studio --prepare`, and confirm with `--doctor`, which handshakes
the *baked* command in a **scrubbed environment** — no `PYTHONPATH`, exactly what
the session gets — and fails the check if any CANFAR tool is missing. A row
pointing at a CLI older than the one you installed shows up as `10 tool(s),
missing session_resources, jobs_report` rather than as silence.

To run the row against a checkout whose CLI is ahead of the installed one
(developing Studio itself), pin it — the pin is remembered, so later
`--prepare` runs keep it:

```bash
astroai studio --prepare --mcp-bin ~/src/astroai/canfar-lab/.pixi/envs/default/bin/astroai
```

That writes `ASTROAI_STUDIO_MCP_BIN` to `~/.astroai/lab/.env`. Clearing the key
restores the default (whatever `astroai` resolves to on `PATH`), which is what a
committed, upgraded CLI wants.

## Troubleshooting

Run `astroai studio --doctor` first; it checks the harness binary, the profile
manifest and layer order, the composed tree (`dsh --profile astroai
--dump-config`), the state root, the port, the MCP handshake and its tool
inventory, skill discovery, the provider routes and the session's resource
envelope, and prints the fix for anything that fails. Exit status is `1` when a
check is fatal, so it doubles as a container smoke test.

| Symptom | Cause |
|---|---|
| `No dsh executable found` | install it: `npm install -g @deepseek-ai/dsh@0.1.5-rc.2`. Never `npx -y @deepseek-ai/dsh …` — npm ≥ 10 swallows the launcher flags Studio depends on |
| `TEAM LAYERS UNAVAILABLE: no pnpm` | `dsh plugin` forwards to pnpm: `corepack enable pnpm`, then re-run `--prepare` |
| Team layers off after a failed install | the manifest is rewritten without them so the profile still boots: fix the cause and re-run `--prepare` |
| `--dump-config` fails | a declared bundle is not installed, or a patch row targets a row this dsh version no longer ships |
| KaTeX error mid-stream | upstream behaviour: streaming parses GFM only and settles into math when the block completes |
| `410`/`404` on `/api` behind a proxy | the deployment's `Host` is not in `--trusted-host` |
