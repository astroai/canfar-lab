# Python (pixi / uv)

- **pixi:** `pixi install`, `pixi run …` / `pixi run python …` — never bare `python3` when Pixi exists. See pixi-dev + env-hygiene.
- **uv:** `uv sync`, `uv run …` — see uv-dev skill. When `uv.lock` exists and there is no pixi.
- No bare `python` or ad-hoc `pip install` / `--user` / `~/.local` when a project manager exists.
- Context7 MCP for library docs.
