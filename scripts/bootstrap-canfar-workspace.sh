#!/usr/bin/env bash
# Bootstrap $WORK/{astroai,<user>} session layout (CANFAR).
# Mirrors Mac ~/src org trees strictly under /scratch/src on CANFAR.
#
# Storage Policy:
#   - Code & Environments: /scratch/src ($WORK) — NEVER $HOME
#   - Caches: /scratch/.cache (PIXI_CACHE_DIR, UV_CACHE_DIR, HF_HOME, TORCH_HOME)
#   - Dotfiles & Keys: $HOME (/arc/home/<user>)
#
# Usage:
#   bash bootstrap-canfar-workspace.sh [--tier core|all] [--all] [--install-agents] [--install-envs] [--force]
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
TEMPLATE="$REPO_ROOT/templates/canfar-workspace"
FORCE=0
INSTALL_AGENTS=1
INSTALL_ENVS=1
WORK_MODELS=0
TIER="core"
SPECIFIED_REPOS=()

for arg in "$@"; do
  case "$arg" in
    --force) FORCE=1 ;;
    --no-agents) INSTALL_AGENTS=0 ;;
    --install-agents) INSTALL_AGENTS=1 ;;
    --no-envs) INSTALL_ENVS=0 ;;
    --install-envs) INSTALL_ENVS=1 ;;
    --work-models) WORK_MODELS=1 ;;
    --all) TIER="all" ;;
    --tier) shift; TIER="${1:-core}" ;;
    -h|--help)
      cat <<'EOF'
Usage: bootstrap-canfar-workspace.sh [options] [repo1 repo2 ...]

Initializes $WORK/{astroai,<user>} on /scratch/src, configures environment hygiene,
installs agents & skills, clones repositories, and prepares pixi environments.

Options:
  --tier core|all      Clone core working tier (default) or all repositories
  --all                Alias for --tier all
  --install-agents     Wire Cursor, Codex, OpenCode, Claude, Gemini, Pi (default: on)
  --no-agents          Skip agent wiring
  --install-envs       Run `pixi install` for cloned packages (default: on)
  --no-envs            Skip `pixi install`
  --work-models        Install NRC/Azure work agent endpoints
  --force              Overwrite existing spine files
EOF
      exit 0
      ;;
    -*) echo "unknown option: $arg" >&2; exit 1 ;;
    *) SPECIFIED_REPOS+=("$arg") ;;
  esac
done

# 1. Enforce Work & Scratch Storage Paths
if [[ -d /scratch && "$(uname)" != "Darwin" ]]; then
  WORK="/scratch/src"
  SCRATCH="/scratch"
elif [[ -n "${SCRATCH:-}" ]]; then
  WORK="$SCRATCH/src"
else
  WORK="${HOME}/src"
  SCRATCH="${HOME}/.scratch"
fi
export WORK SCRATCH

USER_NAME="$(git config github.user 2>/dev/null || git config user.username 2>/dev/null || whoami)"
mkdir -p "$WORK/astroai" "$WORK/$USER_NAME" "$WORK/scripts" "$SCRATCH/.cache"

# Redirect ~/.cache to $SCRATCH/.cache on CANFAR / scratch
if [[ -d "$SCRATCH" && ! -L "$HOME/.cache" ]]; then
  if [[ -d "$HOME/.cache" ]]; then
    cp -rn "$HOME/.cache/"* "$SCRATCH/.cache/" 2>/dev/null || true
    rm -rf "$HOME/.cache"
  fi
  ln -sfn "$SCRATCH/.cache" "$HOME/.cache"
  echo "Redirected ~/.cache -> $SCRATCH/.cache"
fi

# 2. Enforce CANFAR Environment Hygiene
export PYTHONNOUSERSITE=1
unset PYTHONPATH
export PIXI_CACHE_DIR="$SCRATCH/.cache/pixi"
export UV_CACHE_DIR="$SCRATCH/.cache/uv"
export HF_HOME="$SCRATCH/.cache/huggingface"
export TORCH_HOME="$SCRATCH/.cache/torch"

# Persist hygiene exports to ~/.bashrc if on CANFAR
if [[ -d /scratch && -f "$HOME/.bashrc" ]]; then
  if ! grep -q 'CANFAR Environment Hygiene' "$HOME/.bashrc" 2>/dev/null; then
    cat >>"$HOME/.bashrc" <<'EOF'

# CANFAR Environment Hygiene
export PYTHONNOUSERSITE=1
unset PYTHONPATH
export WORK="${TMP_SRC_DIR:-/scratch/src}"
export SCRATCH="${TMP_SCRATCH_DIR:-/scratch}"
export PIXI_CACHE_DIR="${SCRATCH}/.cache/pixi"
export UV_CACHE_DIR="${SCRATCH}/.cache/uv"
export HF_HOME="${SCRATCH}/.cache/huggingface"
export TORCH_HOME="${SCRATCH}/.cache/torch"
EOF
    echo "Configured environment hygiene in ~/.bashrc"
  fi
fi

# 3. Ensure canfar-lab is present under $WORK/astroai/canfar-lab
if [[ "$REPO_ROOT" != "$WORK/astroai/canfar-lab" ]]; then
  if [[ ! -e "$WORK/astroai/canfar-lab" || $FORCE -eq 1 ]]; then
    echo "linking $WORK/astroai/canfar-lab -> $REPO_ROOT"
    ln -sfn "$REPO_ROOT" "$WORK/astroai/canfar-lab"
  fi
fi

# 4. Install Workspace Spine Files
install_file() {
  local src="$1" dst="$2"
  if [[ -e "$dst" && $FORCE -eq 0 ]]; then
    return 0
  fi
  cp "$src" "$dst"
  echo "installed $dst"
}

if [[ -d "$TEMPLATE" ]]; then
  install_file "$TEMPLATE/AGENTS.md" "$WORK/AGENTS.md"
  install_file "$TEMPLATE/CLAUDE.md" "$WORK/CLAUDE.md"
  install_file "$TEMPLATE/workspace.toml" "$WORK/workspace.toml"
  install_file "$TEMPLATE/scripts/workspace-doctor.py" "$WORK/scripts/workspace-doctor.py"
  if [[ -f "$TEMPLATE/scripts/workspace-sync.py" ]]; then
    install_file "$TEMPLATE/scripts/workspace-sync.py" "$WORK/scripts/workspace-sync.py"
  fi
  if [[ -f "$TEMPLATE/scripts/sync-keys.py" ]]; then
    install_file "$TEMPLATE/scripts/sync-keys.py" "$WORK/scripts/sync-keys.py"
  fi
  if [[ -f "$TEMPLATE/update-repos.sh" ]]; then
    install_file "$TEMPLATE/update-repos.sh" "$WORK/update-repos.sh"
  fi
fi

chmod +x "$WORK/scripts/"*.py 2>/dev/null || true
chmod +x "$WORK/update-repos.sh" 2>/dev/null || true

# 5. Clone Repositories via canfar lab sync or workspace-sync.py
if command -v canfar >/dev/null 2>&1; then
  echo "== Bootstrapping repositories via canfar lab sync =="
  canfar lab sync bootstrap --root "$WORK"
elif [[ -f "$WORK/scripts/workspace-sync.py" ]]; then
  if [[ ${#SPECIFIED_REPOS[@]} -gt 0 ]]; then
    python3 "$WORK/scripts/workspace-sync.py" --root "$WORK" bootstrap "${SPECIFIED_REPOS[@]}"
  elif [[ -n "${CLONE_REPOS:-}" ]]; then
    read -ra repos_arr <<< "$CLONE_REPOS"
    python3 "$WORK/scripts/workspace-sync.py" --root "$WORK" bootstrap "${repos_arr[@]}"
  else
    python3 "$WORK/scripts/workspace-sync.py" --root "$WORK" bootstrap --tier "$TIER"
  fi
fi

# 6. Wire Agents, Rules, Skills & MCP
if [[ $INSTALL_AGENTS -eq 1 ]]; then
  echo "== Installing Agent Rules, Skills, and MCP =="
  if [[ $FORCE -eq 1 ]]; then
    bash "$REPO_ROOT/scripts/install-agents.sh" all --force
  else
    bash "$REPO_ROOT/scripts/install-agents.sh" all
  fi
fi

if [[ $WORK_MODELS -eq 1 ]]; then
  bash "$REPO_ROOT/scripts/install-agents.sh" work --force
fi

# 7. Install Pixi Environments for Cloned Packages
if [[ $INSTALL_ENVS -eq 1 ]] && command -v pixi >/dev/null 2>&1; then
  echo "== Preparing Pixi Environments =="
  for repo_dir in "$WORK"/astroai/* "$WORK"/"$USER_NAME"/*; do
    [[ -d "$repo_dir" ]] || continue
    if [[ -f "$repo_dir/pixi.toml" || -f "$repo_dir/pyproject.toml" && -f "$repo_dir/pixi.lock" ]]; then
      label="$(basename "$(dirname "$repo_dir")")/$(basename "$repo_dir")"
      echo "… pixi install in $label"
      (cd "$repo_dir" && pixi install --quiet || true)
    fi
  done
  # Register Jupyter kernels if canfar CLI is available
  if command -v canfar >/dev/null 2>&1; then
    canfar lab kernel ensure >/dev/null 2>&1 || true
  fi
fi

# 8. Create Environment Helper & Aliases
cat >"$WORK/env.sh" <<'EOF'
# Source this file for convenient session helpers: source /scratch/src/env.sh
export WORK="${TMP_SRC_DIR:-/scratch/src}"
export SCRATCH="${TMP_SCRATCH_DIR:-/scratch}"
export PYTHONNOUSERSITE=1
unset PYTHONPATH
alias cdwork='cd "$WORK"'
alias ws-status='canfar lab sync status'
alias ws-sync='canfar lab sync'
alias ws-dirty='canfar lab sync dirty'
EOF

cat <<EOF

======================================================================
  CANFAR Workspace Initialized under: $WORK
======================================================================
  Repos:       $WORK/astroai/  $WORK/$USER_NAME/
  Storage:     All code on /scratch (ephemeral) — $HOME is protected
  Spine:       AGENTS.md  workspace.toml  workspace-sync.py
  Environment: PYTHONNOUSERSITE=1  PIXI_CACHE_DIR=$PIXI_CACHE_DIR

  Status:      canfar lab sync status
  Sync:        canfar lab sync
  Quick Env:   source "$WORK/env.sh"
======================================================================
EOF
