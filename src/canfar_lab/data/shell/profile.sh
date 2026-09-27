#!/bin/bash
# AstroAI lab session environment — sourced from /etc/astroai-lab/profile.sh
# Image PATH (/opt/astroai/...) is applied in /etc/profile.d/astroai.sh after export.

[[ -n "${BASH_VERSION:-}" ]] || return 0 2>/dev/null || exit 0

if [[ -n "${CANFAR_LAB_PROFILE_LOADED:-}" ]]; then
    return 0 2>/dev/null || true
fi
CANFAR_LAB_PROFILE_LOADED=1

# Stderr → canfar logs (non-interactive only; a terminal user should not see
# boot chatter). Always ~/.astroai/lab/boot.log on shared home.
astroai_boot_log() {
    local ts sid kind line dir
    ts="$(date -u +%Y-%m-%dT%H:%M:%SZ 2>/dev/null || printf '?')"
    sid="${skaha_sessionid:-${SKAHA_SESSIONID:-?}}"
    kind="${ASTROAI_SESSION_KIND:-?}"
    line="${ts} sid=${sid} pid=$$ kind=${kind} $*"
    [[ -t 2 ]] || echo "[astroai-boot] ${line}" >&2 || true
    dir="${CANFAR_LAB_CONFIG_DIR:-${HOME}/.astroai/lab}"
    mkdir -p "${dir}" 2>/dev/null || return 0
    echo "${line}" >> "${dir}/boot.log" 2>/dev/null || true
}

astroai_boot_log "profile:start"

if command -v astroai >/dev/null 2>&1; then
    _canfar_lab_cli="astroai"
elif [[ -x /opt/astroai/venv/cadc/bin/astroai ]]; then
    _canfar_lab_cli="/opt/astroai/venv/cadc/bin/astroai"
fi

if [[ -n "${_canfar_lab_cli:-}" ]]; then
    astroai_boot_log "profile:env export"
    # shellcheck disable=SC1090
    # --no-ensure: dirs already created by common-init / prior shells; skip NFS
    # mkdir storms. Ray address is env/persisted only (no canfar ps here).
    eval "$("${_canfar_lab_cli}" env export --no-ensure)" || {
        echo "astroai env export failed — session paths may be incomplete" >&2
    }
    astroai_boot_log "profile:env export done"
else
    echo "astroai: command not found — session paths may be incomplete" >&2
fi
unset _canfar_lab_cli

# Model API keys saved from Studio / `astroai agent keys set` (0600, NAME=value).
_astroai_dotenv="${HOME}/.astroai/lab/.env"
if [[ -r "${_astroai_dotenv}" ]]; then
    set -a
    # shellcheck disable=SC1090
    source "${_astroai_dotenv}"
    set +a
fi
unset _astroai_dotenv

_CANFAR_LAB_SHELL_DIR="${CANFAR_LAB_SHELL_DIR:-/etc/astroai-lab}"
if [[ -f "${_CANFAR_LAB_SHELL_DIR}/hooks.sh" ]]; then
    # shellcheck disable=SC1091
    source "${_CANFAR_LAB_SHELL_DIR}/hooks.sh"
fi

alias py="python3"
alias ll="ls -alF"
alias la="ls -A"

if [[ -n "${BASH_VERSION:-}" ]]; then
    astroai_boot_log "profile:completions"
    command -v uv >/dev/null 2>&1 && eval "$(uv generate-shell-completion bash)"
    command -v pixi >/dev/null 2>&1 && eval "$(pixi completion --shell bash)"
    command -v gh >/dev/null 2>&1 && eval "$(gh completion -s bash)"
    command -v rg >/dev/null 2>&1 && eval "$(rg --generate complete-bash)"
    command -v fzf >/dev/null 2>&1 && eval "$(fzf --bash)"
fi
astroai_boot_log "profile:done"
