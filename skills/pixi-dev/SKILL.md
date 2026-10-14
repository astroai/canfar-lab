---
name: pixi-dev
description: >-
  Pixi Python workflow for owned astroai/sfabbro science repos. Use when
  pixi.toml exists (or [tool.pixi] in pyproject.toml): install, run tasks,
  pytest, and CI gates — never bare python on PATH.
---

# Pixi dev workflow

## Detect

Repo uses Pixi when `pixi.toml` exists at the git root (sometimes nested in `pyproject.toml` via `[tool.pixi]`).

## Commands (in order of preference)

| Goal | Command |
|------|---------|
| Install env | `pixi install` |
| Run task | `pixi run <task>` — list with `pixi task list` |
| Quick lint/compile | `pixi run preflight-push` (when defined) |
| Pre-push CI parity | `pixi run ci-local` (when defined) |
| Targeted test | `pixi run -e test -- pytest tests/test_foo.py -q` |
| Add dep | `pixi add <pkg>` — lock in `pixi.lock` |

**Never** use bare `python`, `python3`, or ad-hoc `pip install` for project work when Pixi is available. Prefer `pixi run <task>` or `pixi run python …`.

**Never** install into `~/.local` / `$HOME/.local` or use `pip install --user` (Mac or CANFAR). See **env-hygiene**.

## Repo-specific gates

| Repo | Pre-push verifier | Notes |
|------|-------------------|-------|
| **torchsky** | `pixi run preflight-push` | Full pytest is GitHub `build-test`; `ci-local` is optional. See `AGENTS.md` |
| **torchfits** | `pixi run ci-local` / `pixi run release-gate` before tag | FITS I/O; sibling of torchsky |
| **zscrape** | `pixi run pytest` or task from `pixi.toml` | Always `pixi run python` / `pixi run zscrape` |
| **cosmapper**, **cfhtcast**, **xmatcher** | `pixi run` tasks from each repo | Check `AGENTS.md` |

## Agent loop

1. Read repo-root `AGENTS.md` and `.cursor/harness/config.json` when present.
2. Use **harness-coding** for plan → execute → verify.
3. Run the smallest verifier that falsifies the change (`preflight-push` before `ci-local`).
4. Do not `git push` until `ci-local` passes when the repo defines it.

## Docs

Use Context7 MCP for pixi/pytorch/polars API lookups — not invented wrapper commands.
