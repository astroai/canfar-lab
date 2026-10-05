# AGENTS.md — CANFAR session workspace (`$WORK`)

Instructions for coding agents on a CANFAR lab session. Workspace root is
`${WORK}` (usually `$SCRATCH/src` → `/scratch/src`). This layout mirrors the
Mac `~/src` org trees for **astroai** and **sfabbro** only (no `opencadc/`,
`clones/`, or `overleaf/` here).

Bootstrap once per session (or after wipe):

```bash
bash "$WORK/sfabbro/agent-home/scripts/bootstrap-canfar-workspace.sh"
# or from a fresh clone:
#   git clone git@github.com:sfabbro/agent-home.git "$WORK/sfabbro/agent-home"
#   bash "$WORK/sfabbro/agent-home/scripts/bootstrap-canfar-workspace.sh"
```

## Layout

| Path | Meaning |
|------|---------|
| `$WORK/astroai/<repo>` | AstroAI product clones (fork workflow) |
| `$WORK/sfabbro/<repo>` | Personal repos (`agent-home`, MSA, …) |
| `$WORK/workspace.toml` | Catalog for this session (astroai + sfabbro only) |
| `$WORK/update-repos.sh` | Sync helper (`SRC=$WORK`, `ORGS="astroai sfabbro"`) |
| `$WORK/scripts/workspace-doctor.py` | Read-only consistency check |

Secrets stay under `$HOME` / `~/.config/keys/` on `/arc` — never under `$WORK`.

## Remotes and PRs

Same as Mac: for AstroAI products prefer `origin` → `sfabbro/<repo>`,
`upstream` → `astroai/<repo>`, push WIP to the fork, PR with
`gh pr create -R astroai/<repo> --head sfabbro:main`.

## Daily workflow

```bash
# Prefer org paths (not flat $WORK/<repo>)
cd "$WORK/astroai/torchsky"   # example
pixi install
pixi run pytest -q

# Sync all session clones
SRC="$WORK" ORGS="astroai sfabbro" bash "$WORK/update-repos.sh"

# Agent stack
bash "$WORK/sfabbro/agent-home/scripts/install.sh" all
bash "$WORK/sfabbro/agent-home/scripts/install.sh" work   # Azure/NRC models
```

Clone into the org tree (wrapper in bootstrap, or manually):

```bash
# Example: torchsky under $WORK/astroai/
mkdir -p "$WORK/astroai"
git clone git@github.com:sfabbro/torchsky.git "$WORK/astroai/torchsky"
cd "$WORK/astroai/torchsky"
git remote add upstream git@github.com:astroai/torchsky.git 2>/dev/null || true
```

If `canfar lab clone` lands a flat `$WORK/<repo>`, move it:

```bash
mkdir -p "$WORK/astroai"
mv "$WORK/torchsky" "$WORK/astroai/torchsky"
```

## Env hygiene

- Pixi under the repo / `$WORK` — never `pip install --user` / `~/.local`
- Images: `images.canfar.net/astroai/*` only
- Headless: `export PYTHONNOUSERSITE=1` and `unset PYTHONPATH`
- Skill: **canfar-lab-workflow**; platform details via `npx skills add astroai/canfar-skills`

## Instruction precedence

User request → repository-local `AGENTS.md` / harness → this workspace file →
agent-home skills/rules → generated agent config.

Per-repo `.cursor/harness/` travels with the clone. Azure/NRC model notes:
`sfabbro/agent-home/docs/openai_with_nrc.md`.
