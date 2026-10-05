#!/bin/bash
# Session reminders and exit hooks for AstroAI lab (interactive shells only).
# Home quota belongs in `canfar lab status`, not every prompt.

__canfar_lab_state_dir() {
    echo "${CANFAR_LAB_CONFIG_DIR:-${HOME}/.astroai/lab}"
}

__canfar_lab_scratch_dir() {
    echo "${SCRATCH:-/scratch}"
}

__canfar_lab_scratch_reminder() {
    local _state _start_file _reminder_file _interval=7200

    [[ -t 1 ]] || return 0
    _state="$(__canfar_lab_state_dir)"
    _start_file="${_state}/session-started"
    _reminder_file="${_state}/last-reminder"
    [[ -f "${_start_file}" ]] || return 0

    local _start _now _elapsed _last _since_last
    _start="$(cat "${_start_file}" 2>/dev/null)" || return 0
    [[ -n "${_start}" && "${_start}" -gt 0 ]] || return 0

    printf -v _now '%(%s)T' -1
    _elapsed=$(( _now - _start ))
    (( _elapsed >= _interval )) || return 0

    _last=0
    [[ -f "${_reminder_file}" ]] && _last="$(cat "${_reminder_file}" 2>/dev/null)" || true
    _since_last=$(( _now - _last ))
    (( _since_last >= _interval )) || return 0

    local _hours=$(( _elapsed / 3600 )) _mins=$(( (_elapsed % 3600) / 60 )) _summary="" _part

    if [[ -d "$(__canfar_lab_scratch_dir)" ]]; then
        _part="$(df -h "$(__canfar_lab_scratch_dir)" 2>/dev/null | awk 'NR>1 {print $3}')"
        [[ -n "${_part}" ]] && _summary="${_summary}data: ${_part}"
    fi

    if git rev-parse --is-inside-work-tree &>/dev/null; then
        _part="$(git rev-list --count HEAD --since="@${_start}" 2>/dev/null)"
        if [[ -n "${_part}" && "${_part}" -gt 0 ]]; then
            [[ -n "${_summary}" ]] && _summary="${_summary} | "
            _summary="${_summary}commits: ${_part}"
        fi
    fi

    if [[ -n "${_summary}" ]]; then
        printf '\n  \033[1;33m⏳ %dh %dm (%s)\033[0m\n  → git push and canfar lab save (${WORK} is ephemeral)\n\n' \
            "${_hours}" "${_mins}" "${_summary}"
    else
        printf '\n  \033[1;33m⏳ %dh %dm — git push and canfar lab save (${WORK} is ephemeral)\033[0m\n\n' \
            "${_hours}" "${_mins}"
    fi

    mkdir -p "${_state}"
    printf '%s' "${_now}" > "${_reminder_file}"
}

if [[ -t 1 && "${PROMPT_COMMAND:-}" != *"__canfar_lab_scratch_reminder"* ]]; then
    if [[ -z "${PROMPT_COMMAND:-}" ]]; then
        PROMPT_COMMAND="__canfar_lab_scratch_reminder"
    else
        PROMPT_COMMAND="${PROMPT_COMMAND}; __canfar_lab_scratch_reminder"
    fi
fi
