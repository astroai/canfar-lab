# Env hygiene (always)

## Run with the project env

When `pixi.toml` or `[tool.pixi]` exists:

- Prefer `pixi run …` / `pixi run python …` over bare `python`, `python3`, or PATH interpreters.
- Prefer `pixi run <task>` over inventing one-off shell wrappers.
- Add deps with `pixi add` (lockfile), not ad-hoc `pip install`.

When only `uv.lock` exists (OpenCADC-style): `uv run …` / `uv sync`. Still never user-site installs.

## Never install into home user-site

**Forbidden** (Mac and CANFAR):

- `pip install --user`
- writing packages under `~/.local` / `$HOME/.local`
- polluting shared `$HOME` with project deps

On CANFAR, `$HOME` is `/arc/home/<user>`: small quota, shared across all session containers. Packages there leak into every `sys.path` and break locked pixi/uv ABIs.

**Do instead:** project-local `pixi install` under the repo / `${WORK}` / `${TMP_SRC_DIR}`.

Headless/batch: `export PYTHONNOUSERSITE=1` and `unset PYTHONPATH`.

Agent config/MCP under `~` is fine; **Python/npm project packages are not**.

## CANFAR session images (AstroAI product work)

**Never** launch `skaha/*` images for AstroAI repos or workflows.

**Always** use Harbor AstroAI images: `images.canfar.net/astroai/<image>:<tag>`
(e.g. `notebook`, `webterm`, `vscode`, `marimo`, `ray-manager`, `base`).

```bash
canfar create notebook images.canfar.net/astroai/notebook:latest
canfar create headless images.canfar.net/astroai/base:latest --name reduce -- …
```
