# AGENTS.md — canfar-lab

In-session CANFAR Science Platform workbench and workload tooling (`canfar-lab`).
CANFAR is the platform. This package is one command tree: `canfar lab` on the
platform CLI, and the standalone `canfar-lab` binary.

## Remotes (AstroAI fork workflow)

| Remote | Points at | Use |
|--------|-----------|-----|
| `origin` | `<github_user>/canfar-lab` | Push fork `main` |
| `upstream` | `astroai/canfar-lab` | Sync `main`; PR target |

`main` tracks `upstream/main`. Prefer working and pushing on fork `main`.
Never force-push `astroai` `main`.

```bash
git fetch upstream && git rebase upstream/main
# edit on main
git push origin main
gh pr create -R astroai/canfar-lab --head <github_user>:main
```

## Environment

Pixi (`[tool.pixi]` in `pyproject.toml`). Prefer `pixi run` over system Python.

```bash
pixi install
```

## Verification

```bash
./scripts/ci.sh                 # lint, format, typecheck, CLI audit, pytest
canfar lab studio --doctor      # Studio pre-flight (needs dsh on PATH)
canfar lab panel doctor         # headless Team / review-bench
```

Harness verify tasks in `.cursor/harness/config.json` map to the same gate.

## In-session project template

The AGENTS template copied into user projects lives under
`src/canfar_lab/data/agent/project/AGENTS.md`. Edit that file when changing
what lab users see after `canfar lab agent setup`, not only this root file.

Workspace layout and `/arc` rules (`PYTHONNOUSERSITE`, no `$HOME/.local`
installs): parent workspace `AGENTS.md` (`~/src/AGENTS.md`).

## Env hygiene

- Prefer `pixi run` / `pixi run python` over bare `python3` when Pixi exists.
- Never `pip install --user` or install into `~/.local` / `$HOME/.local` (esp. CANFAR `/arc/home`).
- Headless/batch: `export PYTHONNOUSERSITE=1` and `unset PYTHONPATH`.
- On CANFAR: code under `$WORK` → `/scratch/src` (session-ephemeral). Prefer
  `canfar lab sync` / `canfar lab clone <fork> --update` to refresh source; `save`/`resume` are deps only.
  See `docs/USAGE.md` → Getting code onto jobs; skill `canfar-lab-workflow`.
- Session images: always `images.canfar.net/astroai/*` — **never** `skaha/*`.

## Code propagation (jobs + sessions)

On CANFAR, code lives under `$WORK` → **`$SCRATCH/src`** (session-ephemeral).
Workers do not see another pod's `/scratch`. Pick a mode before jobs:

1. **GitHub push → pull** (default): push fork `origin`, then
   `canfar lab sync` or `canfar lab clone <fork/repo> --update` (or `--ref <sha>`). Prints HEAD SHA.
2. **`canfar lab save` / `resume`**: lockfiles / env only — **not** source.
3. **VOSpace tarball** under `vos:$USER/astroai/` (pack deps yourself; no CLI yet).
4. **2 + 3** for reproducible headless (env snapshot + code blob).

Same-session Ray: `canfar lab run` packages local `working_dir`. Details:
`docs/USAGE.md` § Code propagation.
