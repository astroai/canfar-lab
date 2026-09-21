# AstroAI Panel / Studio Team (`astroai panel` / `astroai review`)

![AstroAI](../src/astroai_lab/data/brand/astroai-logo.png)

Headless-first chaired multi-persona **Team** review (preset **AstroAI Studio
Team** + `review-panel` skill): freeze → blind-parallel → audit, writing
`panel/<date>-<slug>/{00-brief.md,01-findings.json,02-report.md}`.

astroai never presets providers or models — bring your own provider in dsh
Settings → Models. astroai manages keys and Team structure only.

**Browser coding portal:** prefer [`astroai studio`](studio.md) / the
`astroai/studio` CANFAR image (upstream `dsh web`). `astroai panel web` is
soft-deprecated and points at Studio; Team review is a preset inside Studio.
Skaha proxy notes:
[canfar-containers STUDIO.md](https://github.com/astroai/canfar-containers/blob/main/docs/STUDIO.md).

## One install, one command

```bash
uv tool install --force git+https://github.com/astroai/canfar-lab.git@main
astroai agent setup --recommended   # recommended agents + dsh / review-bench
astroai panel doctor                # dsh presence + keys (no model choice)
astroai panel run /scratch/src/zensus "C1: 90% intervals cover 90% on 2024 split; C2: bias slope |.|<0.01" smoke
```

`astroai review` is the same command. `--dry-run` prints the resolved repo,
panel id, keys present, and task prompt without executing.

## Studio (coding UI)

```bash
astroai studio --prepare            # review-bench + dotenv + profile
astroai studio                      # laptop: dsh web on :3080
astroai studio --skills             # agentskills / skills.sh hint
# CANFAR: launch contributed image images.canfar.net/astroai/studio:<tag>
```

## Catalog CLI

| Command | Behavior |
|---------|----------|
| `astroai panel doctor` | dsh presence + keys present + provider refs (read-only pin) |
| `astroai panel routers` | supported router key catalog + key presence |
| `astroai agent routers` | same router catalog |
| `astroai agent list --supported` | filter to recommended agents |
| `astroai agent setup --recommended` | install+setup recommended set (+ panel/dsh) |
| `astroai studio` | coding portal launcher (laptop / prepare for CANFAR image) |

Supported router keys live in
[`src/astroai_lab/data/agent/support.yaml`](../src/astroai_lab/data/agent/support.yaml).
`astroai panel models` is removed — models live in dsh Settings → Models.

## Surfaces

- Terminal everywhere: `astroai panel run` — headless Team one-shot. If OpenCode Go rejects
  headless (`MissingSessionID`/session errors), switch provider in dsh Settings → Models
  (astroai never retargets `agent-default-model`) and re-run.
- Laptop browser coding: `astroai studio` (preferred) or `astroai panel web`
  (deprecated alias). On Skaha use the **`astroai/studio`** contributed image —
  not vscode `/proxy/PORT/`.
- Marimo notebooks: `from astroai_lab.panel import run_panel`.

## Credentials

Shared dotenv `~/.astroai/lab/.env` (0600) + `agent-env.sh`: discovery order
env → shared `.env` → opencode/Codex auth files, for keys listed in
`support.yaml`. `astroai agent setup` / `studio --prepare` ensure only
`llm-pi-ai.providers.<id> = {apiKeyEnv[, api, baseURL]}` refs.
`~/.dsh/settings.yaml` `agent-default-model` is yours and is never written.
Cursor service tokens are not usable via pi-ai (documented gap).

## Skills

```bash
npx skills add astroai/canfar-skills
```

Also loaded: `~/.astroai/lab/review-bench/skills` (`review-panel`, `canfar-session`).
