# Desk & CANFAR sync — design proposal

Status: draft for review · 2026-10-17
Scope: `astroai/canfar-lab` (CLI, sync engine, Desk cockpit) + `astroai/canfar-containers` (studio image)
Related: [studio.md](studio.md) · [USAGE.md](USAGE.md) · [concurrency.md](concurrency.md) ·
[canfar-containers docs/STUDIO.md](https://github.com/astroai/canfar-containers/blob/main/docs/STUDIO.md) ·
[canfar-containers docs/RAY.md](https://github.com/astroai/canfar-containers/blob/main/docs/RAY.md)

## 1. Summary

The desktop gets a browser cockpit — the same DeepSeek Harness **dsh** web UI,
latest-capable, served locally by `canfar lab desk` — with a **Desk dock** of
buttons. The CANFAR side stays the contributed `astroai/studio` session and gains
a **Desktop sync** dock button plus a background watcher. Sync between them is an
explicit, idempotent transaction over **GitHub push/pull**, with a small
coordination mailbox on `/arc/home/$USER` so a button press on one side triggers
the other within seconds. Data never moves: GPUs and large storage stay on CANFAR.

Three principles drive every decision below:

1. **GitHub is the protocol; the home mailbox is a doorbell.** If the mailbox is
   unavailable (no SSHFS, no VOSpace), the same workflow still works from the
   session side and via origin polling — just less instantly.
2. **One writer per copy; publish then absorb; fast-forward/rebase only; never
   force.** Divergence stops the transaction and asks.
3. **Code on `/scratch` is a fast cache of GitHub, not a store.** The design
   re-bootstraps it, nags about unpublished work, and warns before expiry
   instead of pretending it is durable.

## 2. Goals and non-goals

Goals:

- One press moves code both ways between the desktop workspace and the CANFAR
  session workspace, with per-repo results and safe failure modes.
- "Exact same configuration" made measurable: same image, same dsh/profile
  generation, same repo SHAs, with a parity report on both sides.
- A user-specific configuration surface (code location, sync policy, apps,
  session resources, Ray defaults) that is explicit, layered, and auditable.
- Browser-first cockpit that can still open desktop-native apps (VS Code,
  terminal) and never runs Ray locally.

Non-goals (v1):

- Live bidirectional file sync into a running session (no SSH/exec into sessions;
  `/arc/home` is a shared network filesystem — see `concurrency.md`).
- Executing commands in an existing session from the desk (no platform API).
- Bulk data mirroring. Data stays on `/arc`, VOSpace, or is pulled on demand.
- Syncing dsh chat logs / runtime state (`~/.dsh/sessions`, `~/.dsh/storages`).
- Windows-native desk (WSL behaves like Linux).

## 3. What exists today

| Piece | Anchor | Status |
|---|---|---|
| dsh portal, `laptop` + `canfar` profiles; pinned `dsh 0.2.1-alpha.1` | `src/canfar_lab/studio.py`, `docs/studio.md` | exists |
| Session image: dsh :3080, hub :4792, terminal :4793, JupyterLab :8888, marimo :2718, OpenVSCode :8080, public proxy :5000 | `canfar-containers/scripts/startup-studio.sh`, `scripts/lib/studio-canfar-proxy.py` | exists |
| Dock chips injected into served HTML; WebSocket splicing; token redirect + `dsh-auth-*` cookies | `studio-canfar-proxy.py` | exists |
| Git sync primitives + repo catalog | `src/canfar_lab/cli/sync.py`, `core/git.py`, `templates/canfar-workspace/scripts/workspace-sync.py` | exists |
| Session launch over REST (`create_contributed`, `create_headless`, name-resolution fallback) | `src/canfar_workload/canfar_ops.py` | exists |
| Ray manager + autoscaling workers + MCP tools row | `canfar lab cluster …`, `mcp serve`, `ray-manager` image | exists |
| Agent install/config/keys | `canfar lab agent …`, `src/canfar_lab/data/agent/` | exists |
| Durable config on `$HOME` (`/arc/home`), runtime state on `/scratch/.studio-$USER` | `core/home_layout.py`, `studio.env` state split | exists |
| **Desk cockpit (local proxy + dock + action API)** | — | new |
| **Sync engine v2 (exchange, bus, watchers, config)** | — | new |
| **Session `Desktop sync` chip + watcher** | — | new |

## 4. Architecture

```
        DESKTOP (laptop)                                   CANFAR
┌──────────────────────────────┐                 ┌────────────────────────────────┐
│ browser: dsh (pin, --latest) │   browser tabs  │ astroai/studio session :5000   │
│  + Desk dock (buttons)       │ ───────────────▶│  /       dsh (image pin)       │
│  [marimo] [jupyter]          │                 │  /marimo/ /jupyter/ /vscode/   │
│  [vscode] [terminal]         │   local apps    │  /terminal/ /hub/              │
│  [launch cluster]            │ ───────────────▶│                                │
│  [canfar sync]               │                 │ [desktop sync] chip            │
├──────────────────────────────┤                 ├────────────────────────────────┤
│ ~/src/…   (git clones)       │  ══ GitHub ══▶  │ $WORK (=/scratch/src, configurable) git clones │
│ ~/.dsh, agent keys (local)   │                 │ ~/.dsh  → /arc/home (durable)  │
└──────────────────────────────┘                 ├────────────────────────────────┤
         ▲                                       │ ray-manager + ray-worker (GPU) │
         └──── coordination: arc:/home/$USER ────┴──── /arc/projects, VOSpace ────┘
```

Four channels, four policies:

| Channel | Transport | Policy |
|---|---|---|
| **Code** | GitHub relay (existing fork `origin` / canonical `upstream`) | Button-driven exchange; ff/rebase only; per-repo results |
| **Config deltas** (AGENTS.md, skills list, per-repo `.dsh/` patches) | Same git channel + generated profiles | Parity by *generation*: both sides run their own `canfar lab studio --prepare` from the same CLI |
| **Coordination** (`requests/`, `events/`, `state/`, `ready.json`) | `$HOME/.astroai/lab/sync/` — POSIX in session; SSHFS on desk; `canfar data` fallback | Tiny JSON files, TTLs, idempotent uuids; never secrets |
| **Data** | `/arc/projects`, VOSpace | Never synced wholesale; on-demand `canfar data cp` into `~/canfar-data` |

## 5. Configuration

### 5.1 Layers and precedence

```
built-in defaults
  → ~/.astroai/lab/config.yaml                 # MACHINE: per-laptop prefs (extends LabSettings)
  → ~/.astroai/lab/studio/studio-profile.yaml  # SHARED: must match both sides; mirrored to the home mailbox
  → session resolution in-container             # $WORK, mounts, actual resources — read-only, echoed back
  → CLI flags
```

- `LabSettings` (`src/canfar_lab/config/settings.py`) already reads
  `~/.astroai/lab/config.yaml` with a `CANFAR_LAB_` env prefix; the desk/app/sync
  machine keys join it.
- The shared profile is already written by `canfar lab studio --prepare`
  (`~/.astroai/lab/studio/studio-profile.yaml`); it grows `code`, `sync`,
  `session`, `ray`, `apps`, `desk` sections. The desk publishes it to
  `$HOME/.astroai/lab/studio/studio-profile.yaml` on the mailbox when launching
  a session; both sides report the effective values.
- `canfar lab config show --json` prints effective values with provenance
  (which layer won). `canfar lab desk --doctor` validates roots and mounts.

### 5.2 Where code lives on CANFAR

`code.root.session` resolves in this order, defaulting to the scratch root:

```yaml
code:
  root:
    session: "${WORK:-/scratch/src}"   # fast, per-session, ephemeral — default
    desktop: "~/src"                   # per-machine; sync state is keyed by repo slug, not path
  overrides:                           # per-repo escape hatch
    # torchfits: /arc/projects/astroai/src/torchfits
```

| Session root | Speed | Lifetime | Use for | Validation |
|---|---|---|---|---|
| `/scratch/src` (`$WORK`) | fastest | dies with session | default: day-to-day repos | none |
| `/arc/projects/<group>/src` | medium | durable, group-shared | long-lived team repos | warns on concurrent-writer risk |
| `/arc/home/$USER/src` | slowest | durable, personal | discouraged for repos (quota, git churn) | warns; allowed |
| deployment `$SRCDIR`/`$WORK` mounts | node-local | deployment | honored when the image sets them (`startup-studio.sh` already exports `SRCDIR`/`WORK`) | none |

Because the default root is ephemeral, the design absorbs the failure mode:

1. **Bootstrap on missing** — a sync request carries `bootstrap: auto`; a fresh
   `/scratch` is populated with the repo catalog in the same button press.
2. **Ephemeral nag** — the session watcher (every `sync.ephemeral_nag_seconds`,
   default 300) reports dirty or ahead-of-origin repos to the session dock and
   to the desk. Publishing stays deliberate unless `sync.auto_push_session` is on.
3. **Expiry chip** — the desk reads `canfar info` and shows
   "session ends in ≤ `session.expiry_warn_hours` h — unpublished: <repos>".
4. **Resolved-root echo** — every event carries the actual resolved root
   (including `$WORK` fallbacks); the parity chip shows desk root ↔ session root,
   so drift is visible.
5. **Skill reminder** — `canfar-lab-workflow` gains: "scratch is a cache of
   GitHub, not a store."

### 5.3 Configuration matrix

| Key | Default | Scope | Purpose |
|---|---|---|---|
| `deployment.server` | active `canfar auth show` | machine | multi-server (CADC/SRCNet); drives URL construction |
| `code.root.session` / `code.root.desktop` | `${WORK:-/scratch/src}` / `~/src` | shared / machine | §5.2 |
| `code.overrides` | `{}` | shared | per-repo root |
| `code.repos` | `auto` | shared | catalog resolution: `--repos-file` → `$WORK/workspace.toml` → this key → `DEFAULT_REPOSITORIES` |
| `code.branch_policy` | `main` | shared | `main` exchange now; `session` (`sess/<name>`) deferred to P3 |
| `sync.bus` | `auto` | machine | `auto` (SSHFS → canfar-data → off), or forced |
| `sync.publish_mode` | `commit` | machine (desk) | commit dirty repos with `sync(desk): <ts>` trailer before push; `report` = never commit |
| `sync.absorb_mode` | desk `rebase`, session `ff` | shared | desk is human-attended (rebase local commits); session is unattended (stop on divergence) |
| `sync.exclude` | `.env*`, `*.pem`, `id_*`, caches, `*.zst`, data globs | shared | never staged/committed; refuse-list scan before any auto-commit |
| `sync.ephemeral_nag_seconds` | `300` | shared | session guardrail |
| `sync.auto_push_session` | `false` | shared | opt-in continuous publish from scratch |
| `sync.origin_poll_seconds` | `300` | machine (desk) | origin fetch fallback when the mailbox is quiet |
| `sync.watcher_poll_seconds` | `10` | shared | session-side mailbox poll (POSIX, no inotify) |
| `sync.open_on_pull` | `[]` | machine | repos to open in the editor after an absorb |
| `sync.with_keys` | `false` | machine | opt-in: push model keys into `~/.astroai/lab/.env` on the home (where in-session keys already live) |
| `session.name` | `astroai-studio` | shared | "ensure" reuses one session (interactive cap) |
| `session.image` | `images.canfar.net/astroai/studio:<tag>` | shared | tag or digest; drift shown on the parity chip |
| `session.resources` | `{}` | shared | `{cpu, memory, gpu}` validated against `Context().resources()` |
| `session.expiry_warn_hours` | `12` | machine | guardrail §5.2 |
| `desk.port` | `5080` | machine | local cockpit (dsh stays on 3080) |
| `desk.dsh_version` | `pin` | machine | `latest` allowed; doctor gates the compose (§7.3) |
| `desk.notifications` | `toast,chat` | machine | OS notification and/or dsh chat note |
| `apps.editor` | `vscode` | machine | `vscode`/`cursor`/custom argv |
| `apps.terminal` | `auto` | machine | detect Ghostty → iTerm → Terminal.app; explicit override |
| `ray.autoscale` / `ray.worker_gpu` | platform defaults / `0` | shared | surfaced from `~/.config/canfar/lab/ray-manager.env` |
| `data.desktop_mirror` | `~/canfar-data` | machine | on-demand pulls only |
| `data.project` | `/arc/projects/<group>` | shared | default durable output root |
| `agent.bundles` | shipped defaults | shared | same composition both sides |

The matrix above is the design vocabulary (dotted paths). The keys that
actually ship in `canfar_lab/config/settings.py` are **flat snake_case** —
`code.root.session`/`code.root.desktop` map to the existing `$WORK` /
`/scratch/src` / `~/src` resolution in `canfar_lab/cli/sync.py`, and the rest:

| Implemented key | Default | What it drives |
|---|---|---|
| `sync_mailbox` | unset (auto) | mailbox path override; `CANFAR_LAB_SYNC_MAILBOX` also honored (SSHFS of a shared home) |
| `sync_publish_mode` | `commit` | engine publish: `commit` (auto-commit with secret scan) or `report` (never commit) |
| `sync_absorb_mode` | unset | engine absorb: `ff`, `rebase`, or unset (callers choose; session watcher is `ff`, desk watcher is `rebase`) |
| `sync_exclude` | `[]` | extra exclude globs on top of the engine's built-in list |
| `dsh_version` | `newest` | dsh spec for the desk: `pin\|alpha\|next\|latest\|newest\|<exact>` (§7.3) |
| `desk_port` | `5080` | local cockpit port (dsh itself stays on 3080) |
| `apps_editor` | `vscode` | `vscode`/`cursor`/`windsurf`/custom argv |
| `apps_terminal` | `auto` | macOS: Ghostty → iTerm → Terminal.app; Linux: `$TERMINAL` → known emulators |
| `session_name` | `astroai-studio` | "ensure" reuses one session (interactive cap) |
| `session_image` | unset | `images.canfar.net/astroai/studio:<tag>` override |

### 5.4 Secrets

- The shared profile and mailbox carry **references only** (env var names,
  provider ids). `.credentials.yaml`, `.env`, proxy certs, SSH keys are excluded.
- Model keys use the existing per-machine store (`canfar lab agent keys`);
  `sync.with_keys` is the only path that copies them, and only into the user's
  own home (`sync.exclude` still guards repos).
- Any auto-commit runs a refuse-list scan (`.env*`, `*.pem`, `id_*`, tokens);
  matched files are skipped and reported, never committed.

## 6. Sync transactions

Engine: `canfar_lab/sync/engine.py` — a promoted, tested version of
`templates/canfar-workspace/scripts/workspace-sync.py` (status model, fork
workflow, bootstrap). The template stays as a compatibility script for images
without the new CLI.

### 6.1 `canfar sync` (desk → CANFAR)

What the desk button runs (`canfar lab sync exchange --to canfar`):

1. **Preflight** — effective config; `canfar auth show --json`. On failure the
   chip becomes *Login required* and offers a terminal with `canfar login`.
2. **Publish (desk)** — scope = repos with changes (or the selection): dirty and
   `publish_mode=commit` → secret scan → commit `sync(desk): <ts>` → push to
   fork `origin`. Skipped repos are listed, not stashed.
3. **Ensure session** — `canfar ps --json`; if `session.name` is not `Running`,
   `CanfarOps.create_contributed(name, image, cores, ram, gpu)` (extended with
   `gpu`) using `session.resources`, then wait via `canfar info`; if Pending
   beyond 180 s, surface `canfar events <id>` and `canfar logs <id>`.
4. **Wait for the mailbox before dropping a note** — the session writes
   `$HOME/.astroai/lab/sync/ready.json` at boot (on durable home, not scratch).
   The desk waits up to 90 s for it; without a bus it skips to step 7.
5. **Enqueue request** — `requests/<utc-ts>-<uuid>.json`: action exchange,
   `bootstrap: auto`, per-repo expected SHAs, TTL 30 min.
6. **Session watcher** — claims the request (rename to `processing/`, POSIX
   side), runs the engine: clone missing repos into the resolved root, fetch,
   absorb (`ff` only; dirty repos are reported as skipped), then writes
   `events/<…>.json` and toasts the session dock.
7. **Desk consumes the event** — mailbox (instant) or origin poll (fallback):
   update per-repo state, notify, optionally open the editor. Pressing again is
   always safe; requests are idempotent by uuid and expire.

### 6.2 `desktop sync` (CANFAR → desk)

What the session dock chip runs (`canfar lab sync exchange --to desktop`):

1. **Publish (session)** — user-initiated: commit dirty repos with
   `sync(canfar): <session> <ts>` and push to origin. (Background nag never
   commits; it only reports.)
2. **Event** — write per-repo SHAs, unresolved items, and the resolved root;
   include an expiry warning when `canfar info` says the session is near its end.
3. **Desk absorbs** — fetch; rebase when the desk has local commits
   (`absorb_mode`), else fast-forward; update state; notify; optional
   `open_on_pull`.
4. **Offline desk** — pending events are absorbed on the next `canfar lab desk`
   start (state file keeps the last-seen uuid); stale events are ignored.

### 6.3 Conflict and concurrency rules

- `main` is the exchange branch; session experiments can use `sess/<name>`
  (P3) and are not auto-exchanged.
- Both sides committed since the last exchange → the transaction **halts per
  repo**, reports `ahead/behind`, and offers resolution (editor on the desk,
  browser terminal in session). No merges beyond ff/rebase; no force pushes.
- No silent stashing in the default path. The engine reports dirty trees;
  publish mode commits (with scans), or the user commits manually.
- Sync state is keyed by repo slug + SHA, never by absolute path, so the desk
  root and the session root can differ.
- The session watcher never rebases; unattended rebases are not a thing.

### 6.4 Mailbox files

```
$HOME/.astroai/lab/sync/          # /arc/home in session; SSHFS-or-canfar-data on desk
  ready.json                      # written by the session at boot (id, pid, config digest)
  requests/<ts>-<uuid>.json       # desk → session; TTL 30 min
  processing/<ts>-<uuid>.json     # claimed by the session watcher
  events/<ts>-<uuid>.json         # session → desk results
  state/<side>.json               # last exchange per repo (slug, sha, ts)
```

- Writes are one-file creations; the watcher tolerates malformed/partial files
  (skips, retries next tick, expires). The `canfar data` path writes a `.part`
  name first where the API allows.
- Events are pruned to the most recent 200 per side.
- Bus degradation: `sshfs` (fast both ways) → `canfar-data` (desk writes via
  `canfar data cp` to `arc:/home/$USER/…`, slower, poll only) → `off` (no
  doorbell; session-side button and origin polling still work).

## 7. The Desk cockpit

### 7.1 Command surface

```bash
canfar lab desk                    # prepare profile, start dsh + desk proxy, open browser
canfar lab desk --dsh-version newest   # default; pin|alpha|next|latest|newest|<exact>
canfar lab desk --doctor           # validate profile, bus, apps, session profile
canfar lab desk --no-install-dsh   # never npm-install dsh
canfar lab sync exchange --to canfar|desktop [--dry-run] [--json]
canfar lab sync watch --desk|--session [--once] [--interval S]
```

`canfar lab desk` reuses `studio --prepare` (laptop profile) and
`studio_web_cmd`, then starts a loopback desk proxy on `desk.port`:

- `/` proxies dsh (HTTP + WebSocket splice, loopback only) and injects the Desk
  dock (same dock asset as the session side);
- `POST /__desk/action/<name>` is the action API, requiring a per-run bearer
  token (`X-Desk-Token`) embedded in the served page plus an `Origin` check;
- `GET /__desk/state` returns chip state for the dock.

### 7.2 Dock buttons

| Button | Mechanism |
|---|---|
| marimo / JupyterLab | open the session's `/marimo/` / `/jupyter/lab` in a browser tab (URL from the connect URL) |
| VS Code | local app on the local clone: `open -a "$apps.editor" <repo>` (picker when several repos changed); P3: `vscode://vscode-remote/tunnel+…` into the session via `code tunnel` |
| Terminal | local app at the workspace root: detect Ghostty (`open -a Ghostty --args --working-directory=<dir>`, flag noted for verification), iTerm (AppleScript for cwd), Terminal.app (`open -a Terminal <dir>`); configurable |
| Launch cluster | `canfar lab cluster start` only — never local; chip shows manager state and opens the Ray dashboard URL |
| canfar sync | §6.1 |
| (session) desktop sync | §6.2 — new chip on the session dock |
| Parity chip | image tag/digest · dsh versions · canfar-lab versions · resolved code roots · last exchange |

### 7.3 dsh version policy and parity

- Repo pins (`studio.py`, `agent/review_bench.py`, `studio_paths.py`,
  `data/agent/agents/dsh.yaml`) are bumped together to `0.2.1-rc.2`
  (the image installs that exact version; a unit test keeps the four in
  lockstep). The four pins are the *offline fallback*, not the desk default.
- The desk default is `newest`: `canfar_lab/dsh_release.py` resolves the max
  semver across all npm dist-tags (prereleases included; as of 2026-10 that is
  `next=0.2.1-rc.2`). Specs: `pin|alpha|next|latest|newest|<exact>`.
  Registry failures degrade to the pin with an `offline` note — no hard stop.
- `canfar lab desk` installs the resolved release with `npm install -g` when it
  is missing/older (`--no-install-dsh` to opt out). `canfar lab studio
  --dsh-version <spec> --install-dsh` does the same for the raw `studio` path;
  `--doctor`/`desk --doctor` report `{spec, release, installed, needs_install}`
  in JSON so the parity decision is scriptable.
- Parity report contents: image tag/digest; dsh version; canfar-lab version
  (note: the image installs `canfar-lab` from git `main` at build time, so the
  session CLI may be *newer* than the desk's install — shown, not assumed);
  resolved code roots; repo SHAs after exchange; env lock presence.

### 7.4 Local app launching

- `apps.editor` and `apps.terminal` are machine config; macOS detection is
  explicit (bundle name), Linux uses `$TERMINAL`/`x-terminal-emulator`.
- The desk never runs Ray, JupyterLab, or marimo locally; browser buttons point
  at the session.

### 7.5 Progress and failure UX

Chip state machine: `Idle → Publishing → Ensuring → Booting → Queued →
Absorbing → Done | Divergent | LoginRequired | BusOff | Error`, each with a
retry and a details drawer (per-repo table: pushed / fast-forwarded / rebased /
skipped-dirty / divergent). Other usability rules:

- `--dry-run` (and dock hover) show the planned repos/commits before anything runs.
- Pending sessions surface `canfar events`/`canfar logs` links instead of a spinner.
- First run: `canfar lab desk` runs prepare + doctor and explains the two buttons.

## 8. Ray and resources

Unchanged machinery, wired to the dock: `canfar lab cluster start` creates the
`ray-manager` contributed session and writes `~/.config/canfar/lab/ray-manager.env`
on the home; workers are headless `ray-worker` sessions with `RAY_WORKER_*` /
`RAY_WORKER_GPUS`. The desk can edit `ray.*` and publish it. Interactive cap
note: studio + raymgr = 2 of the default 5 interactive slots; headless workers
do not count. Session expiry: chart default ~4 days interactive — the expiry
chip covers it.

## 9. Repo changes

### 9.1 canfar-lab

```
src/canfar_lab/desk/            proxy.py (loopback HTTP+WS, dock injection, token)
                                actions.py (ensure-session, open, cluster, login, sync)
                                apps.py (editor/terminal launch)
                                state.py (chips/parity)
src/canfar_lab/sync/            engine.py, bus.py (atomic writes, TTL, pruning), watch.py
src/canfar_lab/cli/desk.py      `canfar lab desk` (+ --doctor, --dsh-version)
src/canfar_lab/cli/sync.py      `exchange --to …`, `watch`; catalog precedence
src/canfar_lab/cli/open_cmd.py  active-server URLs (drop the hardcoded workloads.canfar.net)
src/canfar_lab/config/settings.py   machine keys (§5.3)
src/canfar_lab/studio.py / studio_profile.py   shared profile sections + generation
src/canfar_lab/data/desk/dock.html   single-source dock asset
scripts/audit-cli-help.sh       grows with the new commands (existing gate)
tests/unit/{test_sync_engine,test_sync_bus,test_desk_proxy,test_desk_apps}.py
tests/cli/{test_desk,test_sync}.py
```

### 9.2 canfar-containers

```
scripts/lib/studio-canfar-proxy.py   GET/POST /api/studio/sync + Desk dock injection
                                     (rev 16): bearer-or-loopback auth, single-flight
                                     exchange, dry_run passthrough, cluster start/stop
scripts/startup-studio.sh            start `canfar-lab sync watch --session --interval 30`
                                     (skip if CLI missing), supervision-loop restart,
                                     boot log; the watcher writes ready.json itself
scripts/lib/astroai-dock.html        vendored dock asset (from canfar-lab), rev 2
scripts/sync-desk-assets.sh + config/desk-assets.lock   vendoring + drift check
Makefile                             `desk-assets-check` added to `lint`
dockerfiles/studio/Dockerfile        COPY + chmod the vendored asset into /opt/astroai/lib
scripts/lib/test_studio_canfar_proxy.py  host tests: injection, state, auth, actions
scripts/test-studio-local.sh         assert dock rev fingerprint, /api/studio/sync
                                     state + dry-run POST, watcher ready.json
```

Deviations from the sketch above, as implemented: the dock is injected the same
way the command dock is (stdlib string splice), and session-mode buttons stay
value-add only (**Desktop sync**, **Launch cluster**) so the Studio command dock
keeps navigation. `/api/studio/sync` authorizes with the per-process bearer
embedded in the injected config (`X-Desk-Token`) or a loopback caller instead of
a dsh cookie, and the exchange runs single-flight with a 300 s timeout.

Both proxies stay **stdlib-only** (the image proxy's zero-dependency invariant
and its Docker-free CI are preserved); only the dock asset is shared, via the
vendoring script with a lock-file drift check.

## 10. Testing and verification

- canfar-lab: `./scripts/ci.sh` (lint, format, typecheck, CLI audit, pytest with
  the 73% coverage gate). Engine tests use local bare-repo fixtures (origin +
  two clones); bus tests cover atomic writes, TTL expiry, malformed files, and
  pruning; desk-proxy tests use the stdlib HTTP test pattern; CLI tests use
  `typer.testing.CliRunner`.
- canfar-containers: `make lint` (incl. `desk-assets-check`, `lock-check`),
  `make test-host`, `make test-studio-local` (sync endpoint with a stubbed
  `canfar-lab` on PATH + dock fingerprint), then on-platform
  `make test-canfar-session`; `make test-canfar-ray` unchanged.
- Manual E2E checklist (documented with the PR): fresh session → one press →
  bootstrap + fast-forward; dirty desk → commit + push + absorb; divergence →
  halt; session push → desk absorb; bus off → fallback path.

## 11. Phasing

| Phase | Content | Exit criteria |
|---|---|---|
| **P0** | Config/profile schema (`code`, `sync`, `session`, `ray`, `apps`, `desk`), `code.root.*` handling, `canfar lab config` scoping, active-server URL fix, this doc | profile round-trips; doctor validates roots; no behavior change for existing users |
| **P1** | Sync engine v2 + mailbox + session watcher + `Desktop sync` chip + vendored dock asset | button-to-button sync works both ways on-platform; unit+local tests green |
| **P2** | Desk cockpit: `canfar lab desk`, local proxy + dock, actions (ensure/open/cluster/login/sync), chips + notifications | one browser tab replaces hand-typed commands; dry-run + retry paths verified |
| **P3** | VS Code Remote-Tunnels target, `sync.with_keys`, `branch_policy=session`, dock settings page, refuse-list scan hardening, deck of analog for non-CADC deployments | each opt-in independently |

P1 and P2 can partly overlap; P1 is independently useful (sync without cockpit).

## 12. Security notes

- Desk proxy binds `127.0.0.1` only; action POSTs require a per-run bearer token
  plus `Origin` allowlist (localhost CSRF/DNS-rebinding guard). No action on GET.
- Session-side endpoints authorize with the per-process bearer embedded in the
  injected dock config (`X-Desk-Token`, constant-time compare) or a loopback
  caller; GET state is read-only JSON, the exchange is single-flight.
- Mailbox files are user-owned and reference-only; `sync.exclude` + refuse-list
  scans guard commits; keys never enter the profile, the mailbox, or git
  (unless `sync.with_keys`, user home only).
- The session sync endpoint takes no client-supplied paths; repos come from the
  catalog; work happens as the session user inside the workspace root.
- The desk never writes dsh runtime dirs (`sessions/`, `storages/`); config
  application is session-side via the watcher.

## 13. Open decisions

| # | Decision | Recommended default |
|---|---|---|
| 1 | Session button publish: commit+push vs nag-only | commit+push on press; background is nag-only |
| 2 | Branch policy | `main` now; `sess/<name>` deferred to P3 |
| 3 | Desk absorb when the desk has local commits | rebase (attended side); session stays ff-only |
| 4 | `data.desktop_mirror` | `~/canfar-data` |
| 5 | `sync.with_keys` default | off |

## Appendix A — review notes (deltas from the chat draft)

1. Replaced silent auto-stash with explicit publish/absorb modes; dirty repos
   are reported, not stashed, in the default path.
2. Asymmetric absorb: desk rebases (human-attended), session is ff-only.
3. Added the watcher-ready handshake + request TTL so a just-created session
   never loses a request; added idempotent uuids and `processing/` claiming.
4. Mailbox writes are single-file creates with malformed-file tolerance;
   events pruned; ready marker moved to durable `$HOME` (not scratch).
5. Dock asset is single-sourced in canfar-lab and vendored into the image with
   a drift check; both proxies stay stdlib-only so the image CI stays
   Docker-free and the image proxy keeps its zero-dependency invariant.
6. Action endpoints: loopback + per-run token + Origin checks on the desk;
   per-process bearer (embedded in the injected dock config) or loopback on the
   session side; no side effects on GET.
7. Unified catalog precedence; engine is a promoted, tested version of
   `workspace-sync.py` (template kept for compatibility).
8. Desk origin-poll fallback relaxed to 300 s (mailbox events are the fast path).
9. Auto-commit gains a refuse-list scan; `.env`/certs/keys excluded by default.
10. `open_cmd.py` hardcoded `workloads.canfar.net` is replaced with active-server
    URLs; `CanfarOps.create_contributed` gains `gpu`; dsh "latest" caveat
    (`0.2.0-rc.2` < pin `0.2.1-alpha.1`) handled by the version policy.
