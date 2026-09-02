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
# Plugins (MCP/rules/tools):            astroai agent plugins list
# Plugins (e.g. ray MCP):               astroai agent plugins install ray-manager-mcp
# Skills (SKILL.md packs):              npx skills add astroai/canfar-skills
# Agent configs refresh:                astroai agent update
# Config syntax check / repair:         astroai agent verify · agent verify --fix
#
# Default agent setup:  astroai agent setup cursor  (default MCP + rules only)
