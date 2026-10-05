# Pre-installed on AstroAI lab (use these names directly — no custom wrappers):
#   rg  fd  fzf  bat  peek  jq  gh  pixi  uv  hyperfine
#   canfar  cadcget  cadc-tap  vcp  canfar-lab  — /opt/astroai/venv/cadc/bin
#   sg  —  canfar lab agent plugins install ast-grep-cli
#
# pixi project:  pixi install && pixi run python script.py  (versions in pixi.lock)
# uv project:    uv sync && uv run python script.py          (versions in uv.lock)
#
# Platform CLI upgrade (this session):  upgrade-cadc-tools.sh --upgrade astroai-lab
# Agent overview:                       canfar lab agent list
# Plugins (MCP/rules/tools):            canfar lab agent plugins list
# Plugins (e.g. ray MCP):               canfar lab agent plugins install ray-manager-mcp
# Skills (SKILL.md packs):              npx skills add astroai/canfar-skills
# Agent configs refresh:                canfar lab agent update
# Config syntax check / repair:         canfar lab agent verify · agent verify --fix
#
# Default agent setup:  canfar lab agent setup cursor  (default MCP + rules only)
