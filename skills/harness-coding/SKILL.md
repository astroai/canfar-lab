---
name: harness-coding
description: >-
  Coding-agent harness loop for plan, execute, verify, and improve. Uses
  `.cursor/harness/playbook.md` as durable ACE-style memory, logs trajectories to
  disk, delegates parallel work to subagents, and runs the smallest verifier
  before done. Use for implementation, debugging, refactors, and multi-step coding
  tasks in any repository.
---

# Harness Coding

Runtime loop inspired by harness engineering (workflow + file memory + verify). Context stays small; durable state lives on disk.

## Before coding

1. Find the git root. Look for `.cursor/harness/playbook.md` there (walk up if needed).
2. Read the playbook bullets and `AGENTS.md` at the repo root when present.
3. Read `.cursor/harness/config.json` when present for verify tiers (`verify_fast`, `verify`, `verify_full`).
4. State a one-line plan and the verifier tier you will run before claiming done.

## Execute loop

Repeat until the task is done or blocked:

1. **Plan** — smallest next step only.
2. **Execute** — minimal diff; match repo conventions.
3. **Observe** — read tool output; do not assume success.
4. **Verify** — run the smallest check that falsifies the change (see Verify).
5. **Improve** — on failure, write a short note under `.cursor/harness/failures/` then fix root cause.

## File memory (required for non-trivial work)

Do not carry long logs in chat. Persist artifacts:

| Path | Purpose |
|------|---------|
| `.cursor/harness/playbook.md` | Durable bullets (id + one-line desc) |
| `.cursor/harness/trajectories/` | Session summaries (append-only) |
| `.cursor/harness/failures/` | Verifier-grounded failure records |
| `.cursor/harness/proposals/` | Bounded harness edit candidates |

Trajectory stub (append one file per substantial session):

```markdown
# trajectory YYYY-MM-DD-<short-topic>
status: completed|blocked|failed
verifier: <command run>
outcome: <pass|fail + one line>
lesson: <optional, one line>
```

## Verify (three tiers)

Read `.cursor/harness/config.json` keys in order:

| Tier | Key | When |
|------|-----|------|
| Fast | `verify_fast` | Agent claims done on a small edit (`preflight-push`) |
| Pre-push | `verify` | Before push or Jules PR fix complete (`ci-local`) |
| Full | `verify_full` | Human opt-in only — never auto-run |

Standard entrypoints (pixi packages): `pixi run preflight-push`, `pixi run ci-local`.
UV trees: `uv run pytest` / the repo’s documented CI script.

If config is missing a tier, fall back: narrowest pytest/ruff on touched files.
Never auto-run `torchregress-harness` or benchmark suites.
Never claim done on non-trivial logic without a fresh `verify_fast` run.

## Subagents

Use parallel subagents when hypotheses are independent (search + test, multi-file exploration). Require subagents to write results to files under `.cursor/harness/trajectories/` or task-specific paths — not only chat summaries.

## Playbook hygiene

When you learn something reusable (recurring mistake, repo-specific verifier, API quirk):

- Propose a new bullet `{id, desc}` — do not rewrite the whole playbook.
- Prefer updating an existing bullet by `id`.
- Run `harness-reflect` or ask the user before large playbook edits.

## Bootstrap

If `.cursor/harness/` is missing in the git root, run:

```bash
bash ~/src/astroai/canfar-lab/scripts/init-harness.sh
```

Optional pre-push git hook (runs `verify` tier only):

```bash
bash ~/src/astroai/canfar-lab/skills/harness-coding/scripts/install_pre_push_hook.sh
```

## Other agents (Codex, OpenCode, Antigravity, Freebuff, Pi)

Install skills and rules from canfar-lab:

```bash
bash ~/src/astroai/canfar-lab/scripts/install-agents.sh
```

Repo harness files (`.cursor/harness/`) are shared across all agents.
