#!/usr/bin/env bash
# Install canfar-lab skills, rules, MCP, and AGENTS instructions for coding agents.
# Usage: install.sh [all|skills|rules|instructions|mcp|pi] [--force]
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
HOME_DIR="${HOME:?}"
FORCE=0
BUNDLES=()

for arg in "$@"; do
  case "$arg" in
    --force) FORCE=1 ;;
    all|skills|rules|instructions|mcp|pi|omp|work) BUNDLES+=("$arg") ;;
    -h|--help)
      echo "Usage: $0 [all|skills|rules|instructions|mcp|pi|omp|work] [--force]"
      echo "Repo: $REPO_ROOT"
      exit 0
      ;;
    *) echo "unknown arg: $arg" >&2; exit 1 ;;
  esac
done

if [[ ${#BUNDLES[@]} -eq 0 ]]; then
  BUNDLES=(all)
fi

should_run() {
  local name="$1"
  [[ " ${BUNDLES[*]} " == *" all "* || " ${BUNDLES[*]} " == *" $name "* ]]
}

link_skill_dir() {
  local target_root="$1"
  local source_dir="${2:-$REPO_ROOT/skills}"
  [[ -d "$source_dir" ]] || return 0
  mkdir -p "$target_root"
  for skill in "$source_dir"/*/; do
    [[ -f "${skill}SKILL.md" ]] || continue
    local name
    name="$(basename "$skill")"
    local dst="$target_root/$name"
    if [[ -e "$dst" && ! -L "$dst" ]]; then
      if [[ $FORCE -eq 0 ]]; then
        echo "skip $dst (exists, not symlink; use --force)"
        continue
      fi
      rm -rf "$dst"
    fi
    ln -sfn "$skill" "$dst"
    echo "linked $dst -> $skill"
  done
}

copy_or_skip() {
  local src="$1" dst="$2"
  [[ -f "$src" ]] || return 0
  if [[ -f "$dst" && $FORCE -eq 0 ]]; then
    echo "skip $dst (exists)"
    return 0
  fi
  mkdir -p "$(dirname "$dst")"
  cp "$src" "$dst"
  echo "installed $dst"
}

write_agents_global() {
  local path="$1"
  mkdir -p "$(dirname "$path")"
  local global="$REPO_ROOT/instructions/AGENTS-global.md"
  if [[ -f "$path" ]] && grep -q 'agent-home: begin' "$path" 2>/dev/null; then
    python3 - "$path" "$global" <<'PY'
import sys
from pathlib import Path
path, global_path = Path(sys.argv[1]), Path(sys.argv[2])
text = path.read_text()
begin, end = '<!-- agent-home: begin -->', '<!-- agent-home: end -->'
block = f"{begin}\n{global_path.read_text().rstrip()}\n{end}\n"
if begin in text and end in text:
    pre, rest = text.split(begin, 1)
    _, post = rest.split(end, 1)
    path.write_text(pre.rstrip() + '\n\n' + block + post.lstrip('\n'))
else:
    path.write_text(text.rstrip() + '\n\n' + block)
print(f"refreshed agent-home block in {path}")
PY
    return
  fi
  if [[ -f "$path" && -s "$path" ]]; then
    {
      echo ""
      echo "<!-- agent-home: begin -->"
      cat "$global"
      echo "<!-- agent-home: end -->"
    } >>"$path"
    echo "appended agent-home to $path"
    return
  fi
  cp "$global" "$path"
  echo "wrote $path"
}

merge_json_mcp() {
  local src="$1" dst="$2" key="${3:-mcpServers}"
  python3 - "$src" "$dst" "$key" "$FORCE" <<'PY'
import json, re, sys
from pathlib import Path

src, dst, key, force = sys.argv[1:5]
force = force == "1"
home = str(Path.home())
src_text = re.sub(r"\$\{HOME\}|\$HOME\b", home, Path(src).read_text())
overlay = json.loads(src_text)
if not Path(dst).is_file():
    Path(dst).parent.mkdir(parents=True, exist_ok=True)
    Path(dst).write_text(json.dumps(overlay, indent=2) + "\n")
    print(f"created {dst}")
    sys.exit(0)
data = json.loads(Path(dst).read_text())
base = data.get(key, {})
merged = {**base, **overlay.get(key, {})}
data[key] = merged
Path(dst).write_text(json.dumps(data, indent=2) + "\n")
print(f"merged MCP into {dst}")
PY
}

merge_opencode() {
  local src="$REPO_ROOT/agents/opencode/opencode.json"
  local dst="$HOME_DIR/.config/opencode/opencode.json"
  python3 - "$src" "$dst" "$FORCE" <<'PY'
import json, re, sys
from pathlib import Path

def deep_merge(base, overlay):
    out = dict(base)
    for key, val in overlay.items():
        if isinstance(val, dict) and isinstance(out.get(key), dict):
            out[key] = deep_merge(out[key], val)
        else:
            out[key] = val
    return out

def sanitize_lsp(lsp):
    """Normalize lsp config to opencode schema (per-server booleans are invalid)."""
    if not isinstance(lsp, dict):
        return lsp

    cleaned = {}
    enable_all = False
    for name, val in lsp.items():
        if val is True:
            enable_all = True
        elif val is False:
            cleaned[name] = {"disabled": True}
        elif isinstance(val, dict):
            cleaned[name] = val
        else:
            raise SystemExit(f"invalid lsp.{name}: expected object or boolean, got {val!r}")

    if enable_all and not cleaned:
        return True
    return cleaned

src, dst, force = sys.argv[1:4]
home = str(Path.home())
src_text = re.sub(r"\$\{HOME\}|\$HOME\b", home, Path(src).read_text())
overlay = json.loads(src_text)
if "lsp" in overlay:
    overlay["lsp"] = sanitize_lsp(overlay["lsp"])
if not Path(dst).is_file():
    Path(dst).parent.mkdir(parents=True, exist_ok=True)
    Path(dst).write_text(json.dumps(overlay, indent=2) + "\n")
    print(f"created {dst}")
    sys.exit(0)
data = json.loads(Path(dst).read_text())
for k in ("mcp", "lsp"):
    if k in overlay:
        if isinstance(data.get(k), dict) and isinstance(overlay[k], dict):
            data[k] = deep_merge(data[k], overlay[k])
        else:
            data[k] = overlay[k]
if "lsp" in data:
    data["lsp"] = sanitize_lsp(data["lsp"])
Path(dst).write_text(json.dumps(data, indent=2) + "\n")
print(f"merged OpenCode config {dst}")
PY
}

merge_codex_mcp() {
  local fragment="$REPO_ROOT/agents/codex/mcp-servers.toml"
  local dst="$HOME_DIR/.codex/config.toml"
  python3 - "$fragment" "$dst" <<'PY'
import re, sys
from pathlib import Path

fragment, dst = Path(sys.argv[1]), Path(sys.argv[2])
marker = '# agent-home mcp'
home = str(Path.home())
frag_text = re.sub(r"\$\{HOME\}|\$HOME\b", home, fragment.read_text())
body = '\n'.join(
    ln for ln in frag_text.splitlines() if not ln.startswith('#')
).strip()
block = f"{marker}\n\n{body}\n"

if dst.is_file():
    text = dst.read_text()
    idx = text.find(marker)
    if idx != -1:
        text = text[:idx].rstrip() + '\n\n'
    dst.write_text(text + block)
    print(f"merged MCP into {dst}")
else:
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_text(block)
    print(f"created {dst}")
PY
}

merge_pi_settings() {
  local src="$REPO_ROOT/agents/pi/settings.json"
  local dst="$HOME_DIR/.pi/agent/settings.json"
  python3 - "$src" "$dst" "$FORCE" <<'PY'
import json, sys
from pathlib import Path

src, dst, force = sys.argv[1:4]
overlay = json.loads(Path(src).read_text())
merge_lists = ("packages", "skills")

if not Path(dst).is_file():
    Path(dst).parent.mkdir(parents=True, exist_ok=True)
    Path(dst).write_text(json.dumps(overlay, indent=2) + "\n")
    print(f"created {dst}")
    sys.exit(0)

data = json.loads(Path(dst).read_text())
for key in merge_lists:
    items = list(data.get(key, []))
    for item in overlay.get(key, []):
        if item not in items:
            items.append(item)
    if overlay.get(key):
        data[key] = items
for key, val in overlay.items():
    if key not in merge_lists:
        data[key] = val
Path(dst).write_text(json.dumps(data, indent=2) + "\n")
print(f"merged pi settings into {dst}")
PY
}

# Default clone location marker (optional convenience symlink)
mkdir -p "$HOME_DIR/.local/share/agent-home"

if should_run skills || should_run all; then
  echo "== skills =="
  skill_targets=(
    "$HOME_DIR/.cursor/skills"
    "$HOME_DIR/.codex/skills"
    "$HOME_DIR/.config/opencode/skills"
    "$HOME_DIR/.pi/agent/skills"
    "$HOME_DIR/.omp/agent/skills"
    "$HOME_DIR/.omp/profiles/zero-spend/agent/skills"
    "$HOME_DIR/.gemini/config/skills"
    "$HOME_DIR/.agents/skills"
    "$HOME_DIR/.cline/skills"
  )
  # Laptop clones of canfar-skills must not land in every agent prompt.
  # Install that pack on a session: npx skills add astroai/canfar-skills
  on_canfar=0
  if [[ "$HOME_DIR" == /arc/home/* || "${CANFAR_SKILLS:-}" == 1 ]]; then
    on_canfar=1
  fi
  for target in "${skill_targets[@]}"; do
    link_skill_dir "$target" "$REPO_ROOT/skills"
    if [[ $on_canfar -eq 0 ]]; then
      continue
    fi
    for canfar_candidate in \
      "${WORK:-/scratch/src}/astroai/canfar-skills/skills" \
      "/scratch/src/astroai/canfar-skills/skills" \
      "$HOME_DIR/.canfar-skills/skills"; do
      if [[ -d "$canfar_candidate" ]]; then
        link_skill_dir "$target" "$canfar_candidate"
        break
      fi
    done
  done
fi

if should_run rules || should_run all; then
  echo "== rules =="
  copy_or_skip "$REPO_ROOT/rules/cursor/ponytail.mdc" "$HOME_DIR/.cursor/rules/ponytail.mdc"
  copy_or_skip "$REPO_ROOT/rules/cursor/harness.mdc" "$HOME_DIR/.cursor/rules/harness.mdc"
  copy_or_skip "$REPO_ROOT/rules/cursor/env-hygiene.mdc" "$HOME_DIR/.cursor/rules/env-hygiene.mdc"
  copy_or_skip "$REPO_ROOT/rules/cursor/python-pixi.mdc" "$HOME_DIR/.cursor/rules/python-pixi.mdc"
  copy_or_skip "$REPO_ROOT/rules/cursor/shell.mdc" "$HOME_DIR/.cursor/rules/shell.mdc"
  copy_or_skip "$REPO_ROOT/rules/cursor/token-efficient.mdc" "$HOME_DIR/.cursor/rules/token-efficient.mdc"
  copy_or_skip "$REPO_ROOT/rules/cursor/latex.mdc" "$HOME_DIR/.cursor/rules/latex.mdc"
  copy_or_skip "$REPO_ROOT/rules/cursor/writing.mdc" "$HOME_DIR/.cursor/rules/writing.mdc"
  mkdir -p "$HOME_DIR/.agents/rules"
  copy_or_skip "$REPO_ROOT/rules/agents/ponytail.md" "$HOME_DIR/.agents/rules/ponytail.md"
  copy_or_skip "$REPO_ROOT/rules/agents/harness.md" "$HOME_DIR/.agents/rules/harness.md"
  copy_or_skip "$REPO_ROOT/rules/agents/env-hygiene.md" "$HOME_DIR/.agents/rules/env-hygiene.md"
  copy_or_skip "$REPO_ROOT/rules/agents/python-pixi.md" "$HOME_DIR/.agents/rules/python-pixi.md"
  copy_or_skip "$REPO_ROOT/rules/agents/shell.md" "$HOME_DIR/.agents/rules/shell.md"
  copy_or_skip "$REPO_ROOT/rules/agents/token-efficient.md" "$HOME_DIR/.agents/rules/token-efficient.md"
  copy_or_skip "$REPO_ROOT/rules/agents/latex.md" "$HOME_DIR/.agents/rules/latex.md"
  copy_or_skip "$REPO_ROOT/rules/agents/writing.md" "$HOME_DIR/.agents/rules/writing.md"
fi

if should_run instructions || should_run all; then
  echo "== instructions =="
  write_agents_global "$HOME_DIR/.codex/AGENTS.md"
  write_agents_global "$HOME_DIR/.config/manicode/AGENTS.md"
  write_agents_global "$HOME_DIR/.clinerules"
fi

if should_run mcp || should_run all; then
  echo "== mcp =="
  merge_json_mcp "$REPO_ROOT/agents/cursor/mcp.json" "$HOME_DIR/.cursor/mcp.json"
  merge_json_mcp "$REPO_ROOT/agents/claude/mcp.json" "$HOME_DIR/.claude.json"
  merge_json_mcp "$REPO_ROOT/agents/cursor/mcp.json" "$HOME_DIR/.gemini/config/mcp_config.json"
  merge_json_mcp "$REPO_ROOT/agents/cursor/mcp.json" "$HOME_DIR/.omp/agent/mcp.json"
  merge_opencode
  merge_codex_mcp
  CLINE_MCP="$HOME_DIR/Library/Application Support/Code/User/globalStorage/saoudrizwan.claude-dev/settings/cline_mcp_settings.json"
  if [[ -f "$CLINE_MCP" || -d "$(dirname "$CLINE_MCP")" ]]; then
    merge_json_mcp "$REPO_ROOT/agents/cursor/mcp.json" "$CLINE_MCP"
  fi
fi

if should_run pi || should_run all; then
  echo "== pi =="
  merge_pi_settings
fi

if should_run omp || should_run all; then
  echo "== omp =="
  mkdir -p "$HOME_DIR/.omp/agent" "$HOME_DIR/.omp/profiles/zero-spend/agent"
  copy_or_skip "$REPO_ROOT/agents/omp/config.yml" "$HOME_DIR/.omp/agent/config.yml"
  copy_or_skip "$REPO_ROOT/agents/omp/config-zero-spend.yml" "$HOME_DIR/.omp/profiles/zero-spend/agent/config.yml"
  merge_json_mcp "$REPO_ROOT/agents/omp/mcp.json" "$HOME_DIR/.omp/agent/mcp.json"
  merge_json_mcp "$REPO_ROOT/agents/omp/mcp.json" "$HOME_DIR/.omp/profiles/zero-spend/agent/mcp.json"
  link_skill_dir "$HOME_DIR/.omp/agent/skills"
  link_skill_dir "$HOME_DIR/.omp/profiles/zero-spend/agent/skills"
  if [[ -f "$HOME_DIR/.omp/agent/agent.db" && ! -f "$HOME_DIR/.omp/profiles/zero-spend/agent/agent.db" ]]; then
    cp "$HOME_DIR/.omp/agent/agent.db" "$HOME_DIR/.omp/profiles/zero-spend/agent/agent.db"
  fi
  if [[ -f "$HOME_DIR/.omp/agent/models.db" && ! -f "$HOME_DIR/.omp/profiles/zero-spend/agent/models.db" ]]; then
    cp "$HOME_DIR/.omp/agent/models.db" "$HOME_DIR/.omp/profiles/zero-spend/agent/models.db"
  fi
fi

# Work endpoint configuration is opt-in, never part of the personal all bundle.
for bundle in "${BUNDLES[@]}"; do
  if [[ "$bundle" == work ]]; then
    python3 "$REPO_ROOT/scripts/install-work-models.py"
    break
  fi
done

STAMP="$HOME_DIR/.local/share/agent-home/install-stamp"
date -u +"%Y-%m-%dT%H:%M:%SZ bundle=$(cat "$REPO_ROOT/VERSION")" >"$STAMP"
echo ""
echo "Done. Stamp: $STAMP"
echo "Per-repo harness: bash $REPO_ROOT/scripts/init-harness.sh [git-root]"
