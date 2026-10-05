---
name: repo-router
description: >-
  Route to the right agent-home skill and verifier for sfabbro repos. Use at
  session start, when switching directories, or when unsure which workflow applies.
---

# Repo router

Read repo-root `AGENTS.md` first. Then pick skills and verifiers:

| Repo / family | Env skill | Domain skill | Typical verifier |
|---------------|-----------|--------------|------------------|
| torchsky | pixi-dev | torchsky-dev | `pixi run ci-local` |
| torchfits | pixi-dev | release-api-freeze-review (pre-tag) | `pixi run ci-local` |
| zensus | pixi-dev | — | `pixi run pytest` |
| cosmodist, cfhtcast, xmatch | pixi-dev | — | `pixi run` CI task |
| torchregress | pixi-dev | — | `./scripts/preflight_push.sh`; `pixi run ci` |
| torchregress-harness | pixi-dev | harness-coding | `pixi run preflight-push`; `pixi run ci` |
| torchz | pixi-dev | — | `./scripts/preflight_push.sh`; `pixi run ci` |
| weightmask | pixi-dev | — | `pixi run lint`; `pixi run test` |
| canfar-skills | — | canfar-* (upstream install) | `python3 scripts/validate_skills.py` |
| canfar, canfar-portal | — | — | project `AGENTS.md` |
| canfar-lab (platform) | — | canfar-lab-workflow | `canfar lab status --json` / `canfar-lab doctor` |
| CANFAR lab session | — | canfar-lab-workflow | pixi/uv under `${WORK}` |
| masked-stellar-autoencoder | pixi-dev | — | `pixi run` tests; push `origin` only (not aydan upstream) |
| science-platform/* | harness-coding | — | repo CI |
| agent-home | harness-coding | — | `bash scripts/install.sh` dry run |

## Remotes (AstroAI products under `~/src/astroai/`)

Standardize on `origin` = `sfabbro/<repo>`, `upstream` = `astroai/<repo>`,
`main` tracks `upstream/main`. Prefer working and pushing on fork **`main`**,
then PR with `gh pr create -R astroai/<repo> --head sfabbro:main`. Avoid
extra `wip/*` branches unless necessary. Never force-push `astroai` `main`.

## PR / CI skills

| Task | Skill |
|------|-------|
| Failing GitHub Actions on a PR | gh-fix-ci |
| Review comment triage | gh-address-comments |
| Batch bot PRs (Jules, etc.) | jules-pr-batch-triage |
| Merge + verify branches | pr-merger |

## Always on

- **harness-coding** — implementation loop
- **ponytail** (Pi package) or global ponytail rule — minimal diffs
- **science-correctness** — math / physics / logic checks on numerical work
- **perf-measure** — baseline/treatment before speed claims
- **frontend-design** / **presentation-design** / **matplotlib-data-visualization** — user-facing output quality (`matplotlib-data-visualization` is optional upstream, not bundled)
- **orx-figures** — OpenResearch sessions only, via `orx install-skills`. Do not vendor `alphaXiv/openresearch-cli` `agent-skills/` into this repo.

The workspace catalog at `~/src/workspace.toml` is the path/lifecycle source of
truth. Repository-local `AGENTS.md` and `.cursor/harness/config.json` remain the
source of truth for commands when this routing table differs.

## Writing / LaTeX / Overleaf

| Task | Skill |
|------|-------|
| Paper prose, abstracts, rebuttals | academic-writing → academic-humanizer → unslop |
| IMRAD / reporting / evidence language | scientific-writing |
| Review comments without rewriting | manuscript-writing |
| Non-paper prose that sounds like a chatbot | human-voice (not for manuscripts) |
| Compile / log / layout failures | latex-debug (+ pdf) |
| Pull/push Overleaf Git, MCP projects | overleaf-git |

Workspace of clones: `/Users/fabbros/src/overleaf`. Secrets: `~/.config/overleaf-mcp/`.

## MCP (Cursor / Claude / Codex / OpenCode)

- **context7** — library docs
- **github** — issues/PRs (`GITHUB_TOKEN` / `gh auth login`)
- **fetch** — URL content
- **memory** — cross-session notes at `~/.local/share/agent-home/memory.jsonl`
- **overleaf** — `@mjyoo2/overleaf-mcp` via `OVERLEAF_PROJECTS_CONFIG`

On CANFAR lab, run `canfar lab agent update` and `npx skills add astroai/canfar-skills`.
