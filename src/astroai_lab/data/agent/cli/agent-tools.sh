# Pre-installed on AstroAI lab (use these names directly — no custom wrappers):
#   rg  fd  fzf  bat  peek  jq  gh  pixi  uv  hyperfine
#   canfar  cadcget  cadc-tap  vcp  astroai  — /opt/astroai/venv/cadc/bin
#   sg  —  astroai agent plugins install ast-grep-cli
#
# pixi project:  pixi install && pixi run python script.py  (versions in pixi.lock)
# uv project:    uv sync && uv run python script.py          (versions in uv.lock)
#
# Platform CLI upgrade (this session):  upgrade-cadc-tools.sh --upgrade astroai-lab
# Agent overview:                       astroai agent list
# Plugins (skills/MCP/rules/tools):     astroai agent plugins list
# Plugins (e.g. ponytail):              astroai agent plugins install ponytail
# Agent configs refresh:                astroai agent update
# Config syntax check / repair:         astroai agent verify · agent verify --fix
#
# Default agent setup:  astroai agent setup cursor  (default plugins only)
# Opt-in skills:        astroai agent plugins install polars
# Bulk via pixi:        pixi global install pixi-skills && pixi-skills manage --backend cursor
