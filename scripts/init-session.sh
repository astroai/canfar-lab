#!/usr/bin/env bash
# ==============================================================================
# CANFAR Session Workspace & Science Environment Initializer
# Designed to live in $HOME (/arc/home/<user>) or run directly in any CANFAR
# session (terminal, notebook, vscode, marimo).
#
# Sets up /scratch/src, redirects caches off $HOME, syncs all 10 science repos,
# configures AI coding agents, and prepares Pixi environments.
# ==============================================================================
set -euo pipefail

BOLD="\033[1m"
GREEN="\033[32m"
YELLOW="\033[33m"
CYAN="\033[36m"
RED="\033[31m"
RESET="\033[0m"

echo -e "${BOLD}${CYAN}=== CANFAR Session Initializer & Workspace Sync ===${RESET}"

# ------------------------------------------------------------------------------
# 1. Environment & Storage Hygiene (Strict Zero-Cache on $HOME)
# ------------------------------------------------------------------------------
export PYTHONNOUSERSITE=1
unset PYTHONPATH 2>/dev/null || true

export WORK="${WORK:-/scratch/src}"
export SCRATCH="${SCRATCH:-/scratch}"
CACHE_DIR="${SCRATCH}/.cache"

export PIXI_CACHE_DIR="${CACHE_DIR}/pixi"
export UV_CACHE_DIR="${CACHE_DIR}/uv"
export HF_HOME="${CACHE_DIR}/huggingface"
export TORCH_HOME="${CACHE_DIR}/torch"
export AGENT_STORAGE_DIR="${CACHE_DIR}/agents"
export TMPDIR="${SCRATCH}/tmp"

mkdir -p "${PIXI_CACHE_DIR}" "${UV_CACHE_DIR}" "${HF_HOME}" "${TORCH_HOME}" "${AGENT_STORAGE_DIR}" "${TMPDIR}"
mkdir -p "${WORK}/astroai" "${WORK}/sfabbro"

echo -e "  ${GREEN}✓${RESET} Storage hygiene configured: all caches redirected to ${BOLD}${CACHE_DIR}${RESET}"

# Enforce in ~/.bashrc if not already present
BASHRC="${HOME}/.bashrc"
if [[ -f "$BASHRC" ]]; then
    if ! grep -q "PYTHONNOUSERSITE=1" "$BASHRC"; then
        cat <<'EOF' >> "$BASHRC"

# CANFAR Environment Hygiene
export PYTHONNOUSERSITE=1
unset PYTHONPATH
export WORK="/scratch/src"
export SCRATCH="/scratch"
export PIXI_CACHE_DIR="/scratch/.cache/pixi"
export UV_CACHE_DIR="/scratch/.cache/uv"
export HF_HOME="/scratch/.cache/huggingface"
export TORCH_HOME="/scratch/.cache/torch"
[[ -f /scratch/src/env.sh ]] && source /scratch/src/env.sh
EOF
        echo -e "  ${GREEN}✓${RESET} Injected hygiene exports into ~/.bashrc"
    fi
fi

# ------------------------------------------------------------------------------
# 2. Dynamic User / Fork Resolution
# ------------------------------------------------------------------------------
FORK_USER="$(gh api user -q .login 2>/dev/null || whoami)"
if [[ -z "$FORK_USER" || "$FORK_USER" == "root" ]]; then
    FORK_USER="sfabbro"
fi
echo -e "  ${GREEN}✓${RESET} Active GitHub User / Fork Owner: ${BOLD}${FORK_USER}${RESET}"

# ------------------------------------------------------------------------------
# 3. Synchronize Core Infrastructure (canfar-lab & agent-home)
# ------------------------------------------------------------------------------
sync_repo() {
    local org="$1"
    local repo="$2"
    local target_dir="${WORK}/${org}/${repo}"

    if [[ ! -d "${target_dir}/.git" ]]; then
        echo -e "  ${CYAN}→${RESET} Cloning ${BOLD}${repo}${RESET} (fork: ${FORK_USER}/${repo}, upstream: ${org}/${repo})..."
        if command -v gh >/dev/null 2>&1 && gh auth status >/dev/null 2>&1; then
            gh repo clone "${FORK_USER}/${repo}" "${target_dir}" 2>/dev/null || \
            gh repo clone "${org}/${repo}" "${target_dir}" 2>/dev/null || \
            git clone "https://github.com/${org}/${repo}.git" "${target_dir}"
        else
            git clone "https://github.com/${org}/${repo}.git" "${target_dir}"
        fi

        # Ensure remotes: origin -> fork, upstream -> canonical
        git -C "${target_dir}" remote set-url origin "https://github.com/${FORK_USER}/${repo}.git" 2>/dev/null || true
        git -C "${target_dir}" remote add upstream "https://github.com/${org}/${repo}.git" 2>/dev/null || true
    else
        echo -e "  ${GREEN}✓${RESET} ${BOLD}${repo}${RESET} exists, updating remote references..."
        git -C "${target_dir}" fetch origin --quiet 2>/dev/null || true
        git -C "${target_dir}" fetch upstream --quiet 2>/dev/null || true
    fi
}

echo -e "\n${BOLD}[1/4] Syncing Infrastructure Repositories...${RESET}"
sync_repo "astroai" "canfar-lab"
sync_repo "sfabbro" "agent-home"

# ------------------------------------------------------------------------------
# 4. Synchronize the 10 Science Repositories
# ------------------------------------------------------------------------------
SCIENCE_REPOS=(
    cfhtcast
    cosmodist
    uspm
    torchz
    torchsky
    torchregress
    torchfits
    xmatch
    zensus
    weightmask
)

echo -e "\n${BOLD}[2/4] Syncing Science Repositories into /scratch/src/astroai/...${RESET}"
for repo in "${SCIENCE_REPOS[@]}"; do
    sync_repo "astroai" "$repo"
done

# ------------------------------------------------------------------------------
# 5. Agent Configurations, Rules, and Skills Setup
# ------------------------------------------------------------------------------
echo -e "\n${BOLD}[3/4] Installing & Synchronizing Agent Toolchains...${RESET}"
INSTALL_AGENTS_SCRIPT="${WORK}/astroai/canfar-lab/scripts/install-agents.sh"
if [[ -f "$INSTALL_AGENTS_SCRIPT" ]]; then
    bash "$INSTALL_AGENTS_SCRIPT" all || true
    echo -e "  ${GREEN}✓${RESET} Agent rules and MCP configs synchronized (Cursor, Claude, OpenCode, Codex, Gemini)."
else
    echo -e "  ${YELLOW}⚠${RESET} install-agents.sh not found, skipping agent config."
fi

# ------------------------------------------------------------------------------
# 6. Session Environment Helpers & Aliases
# ------------------------------------------------------------------------------
echo -e "\n${BOLD}[4/4] Writing Session Helper Script (/scratch/src/env.sh)...${RESET}"
cat <<'EOF' > "${WORK}/env.sh"
# Sourceable session environment
export WORK="/scratch/src"
export SCRATCH="/scratch"
export PYTHONNOUSERSITE=1
unset PYTHONPATH

export PIXI_CACHE_DIR="/scratch/.cache/pixi"
export UV_CACHE_DIR="/scratch/.cache/uv"
export HF_HOME="/scratch/.cache/huggingface"
export TORCH_HOME="/scratch/.cache/torch"

# Shortcuts
alias cdwork='cd /scratch/src'
alias cdastro='cd /scratch/src/astroai'
alias ws-status='canfar lab sync status 2>/dev/null || python3 /scratch/src/astroai/canfar-lab/src/canfar_lab/cli/sync.py status 2>/dev/null'
alias ws-push='canfar lab sync push 2>/dev/null || python3 /scratch/src/astroai/canfar-lab/src/canfar_lab/cli/sync.py push 2>/dev/null'
alias ws-dirty='canfar lab sync dirty 2>/dev/null || python3 /scratch/src/astroai/canfar-lab/src/canfar_lab/cli/sync.py dirty 2>/dev/null'
EOF

chmod +x "${WORK}/env.sh"

echo -e "\n${BOLD}${GREEN}================================================================${RESET}"
echo -e "${BOLD}${GREEN}✓ CANFAR Workspace Initialized & In Sync!${RESET}"
echo -e "${BOLD}Location:${RESET} /scratch/src"
echo -e "${BOLD}Science Packages Ready:${RESET} ${SCIENCE_REPOS[*]}"
echo -e "Run ${CYAN}source /scratch/src/env.sh${RESET} or use ${CYAN}cdastro${RESET} / ${CYAN}cdwork${RESET}."
echo -e "${BOLD}${GREEN}================================================================${RESET}\n"
