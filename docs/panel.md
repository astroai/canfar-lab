# AstroAI Panel / Studio Team (`canfar lab panel`)

![AstroAI](../src/canfar_lab/data/brand/astroai-logo.png)

Headless-first chaired multi-persona **Team** review (preset **AstroAI Studio
Team** + `review-panel` skill): freeze → blind-parallel → audit, writing
`panel/<date>-<slug>/{00-brief.md,01-findings.json,02-report.md}`.

`canfar lab` never presets providers or models — bring your own provider in dsh
Settings → Models. It manages keys and Team structure only.

**Browser coding portal:** prefer [`canfar lab studio`](studio.md) / the
`astroai/studio` CANFAR image (upstream `dsh web`). Team review is a preset inside Studio.
Skaha proxy notes:
[canfar-containers STUDIO.md](https://github.com/astroai/canfar-containers/blob/main/docs/STUDIO.md).

## One install, one command

```bash
uv tool install --force git+https://github.com/astroai/canfar-lab.git@main
canfar lab agent setup --recommended   # recommended agents + dsh / review-bench
canfar lab panel doctor                # dsh presence + keys (no model choice)
canfar lab panel run /scratch/src/zensus "C1: 90% intervals cover 90% on 2024 split; C2: bias slope |.|<0.01" smoke
```

`--dry-run` prints the resolved repo,
panel id, keys present, and task prompt without executing.

## Studio (coding UI)

```bash
canfar lab studio --prepare            # review-bench + dotenv + profile
canfar lab studio                      # laptop: dsh web on :3080
canfar lab studio --skills             # agentskills / skills.sh hint
# CANFAR: launch contributed image images.canfar.net/astroai/studio:<tag>
```

## Catalog CLI

| Command | Behavior |
|---------|----------|
| `canfar lab panel doctor` | dsh presence + keys present + provider refs (read-only pin) |
| `canfar lab panel routers` | supported router key catalog + key presence |
| `canfar lab agent routers` | same router catalog |
| `canfar lab agent list --supported` | filter to recommended agents |
| `canfar lab agent setup --recommended` | install+setup recommended set (+ panel/dsh) |
| `canfar lab studio` | coding portal launcher (laptop / prepare for CANFAR image) |

Supported router keys live in
[`src/canfar_lab/data/agent/support.yaml`](../src/canfar_lab/data/agent/support.yaml).
Models live in dsh Settings → Models.

## Surfaces

- Terminal everywhere: `canfar lab panel run` — headless Team one-shot. If OpenCode Go rejects
  headless (`MissingSessionID`/session errors), switch provider in dsh Settings → Models
  (`canfar lab` never retargets `agent-default-model`) and re-run.
- Laptop browser coding: `canfar lab studio`. On Skaha use the **`astroai/studio`** contributed image —
  not vscode `/proxy/PORT/`.
- Marimo notebooks: `from canfar_lab.panel import run_panel`.

## Credentials

Shared dotenv `~/.astroai/lab/.env` (0600) + `agent-env.sh`: discovery order
env → shared `.env` → opencode/Codex auth files, for keys listed in
`support.yaml`. `canfar lab agent setup` / `studio --prepare` ensure only
`llm-pi-ai.providers.<id> = {apiKeyEnv[, api, baseURL]}` refs.
`~/.dsh/settings.yaml` `agent-default-model` is yours and is never written.
Cursor service tokens are not usable via pi-ai (documented gap).

## Skills

```bash
npx skills add astroai/canfar-skills
```

Also loaded: `~/.astroai/lab/review-bench/skills` (`review-panel`, `canfar-session`).
