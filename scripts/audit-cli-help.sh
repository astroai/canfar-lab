#!/usr/bin/bash
# Audit astroai help text vs accepted flags. Exit 1 on mismatches.
set -euo pipefail
cd "$(dirname "$0")/.."
CLI=(pixi run astroai)
FAIL=0

# Under GITHUB_ACTIONS=true, typer/rich colorizes --help output and splits
# option text with ANSI SGR codes (e.g. "-" + ESC + "-json" + ESC), so a
# literal grep of "--json" finds nothing. Strip SGR sequences first.
strip_ansi() {
    sed $'s/\x1b\[[0-9;]*m//g'
}

check_help_ok() {
    local label="$1"
    shift
    if "${CLI[@]}" "$@" --help >/dev/null 2>&1; then
        echo "  ok  help: $label"
    else
        echo "  FAIL help: $label ($*)" >&2
        FAIL=$((FAIL + 1))
    fi
}

check_flag_in_help() {
    local label="$1"
    local flag="$2"
    shift 2
    local out
    out=$("${CLI[@]}" "$@" --help 2>&1 | strip_ansi) || true
    if grep -qF -- "$flag" <<< "$out"; then
        echo "  ok  flag $flag in $label"
    else
        echo "  FAIL missing flag $flag in $label" >&2
        FAIL=$((FAIL + 1))
    fi
}

check_invocation() {
    local label="$1"
    shift
    if "${CLI[@]}" "$@" >/dev/null 2>&1; then
        echo "  ok  run: $label"
    else
        local code=$?
        echo "  FAIL run ($code): $label ($*)" >&2
        FAIL=$((FAIL + 1))
    fi
}

check_help_accepts_flag() {
    local label="$1"
    local flag="$2"
    shift 2
    if "${CLI[@]}" "$@" "$flag" --help >/dev/null 2>&1; then
        echo "  ok  $label accepts $flag (subcommand placement)"
    else
        echo "  FAIL $label rejects $flag after subcommand" >&2
        FAIL=$((FAIL + 1))
    fi
}

echo "=== Top-level ==="
if "${CLI[@]}" --help >/dev/null 2>&1; then
    echo "  ok  help: main"
else
    echo "  FAIL help: main" >&2
    FAIL=$((FAIL + 1))
fi
check_help_ok "help" help
check_help_ok "help -c" help --command agent
# --show-completion is not a --help target; probe the flag itself.
if "${CLI[@]}" --show-completion bash >/dev/null 2>&1 \
    || "${CLI[@]}" --show-completion zsh >/dev/null 2>&1; then
    echo "  ok  help: show-completion"
else
    # Typer still exits 0 on unknown shells with a message; accept exit 0 either way.
    out=$("${CLI[@]}" --show-completion bash 2>&1) || true
    if [[ -n "$out" ]]; then
        echo "  ok  help: show-completion"
    else
        echo "  FAIL help: show-completion bash" >&2
        FAIL=$((FAIL + 1))
    fi
fi
check_help_ok "run" run
check_help_ok "init" init
check_help_ok "clone" clone
check_help_ok "save" save
check_help_ok "resume" resume
check_help_ok "status" status
check_help_ok "clean" clean

echo "=== Nested typers ==="
for grp in env config kernel agent cluster jobs; do
    check_help_ok "$grp" "$grp"
done
check_help_ok "cluster dashboard" cluster dashboard
check_help_ok "cluster start" cluster start
check_help_ok "cluster status" cluster status
check_help_ok "cluster stop" cluster stop
check_help_ok "mcp (hidden)" mcp
check_help_ok "autoscaler (hidden)" autoscaler

echo "=== Global flags in main help ==="
MAIN=$("${CLI[@]}" --help 2>&1 | strip_ansi)
for flag in "--json" "--yes" "--dry-run" "--quiet" "--version"; do
    if grep -qF -- "$flag" <<< "$MAIN"; then
        echo "  ok  global $flag"
    else
        echo "  FAIL global $flag missing from main --help" >&2
        FAIL=$((FAIL + 1))
    fi
done

echo "=== Documented subcommand flags ==="
check_flag_in_help "init" "--uv" init
check_flag_in_help "init" "--dir" init
check_flag_in_help "clone" "--from-env" clone
check_flag_in_help "clone" "--dir" clone
check_flag_in_help "save" "--full" save
check_flag_in_help "save" "--list" save
check_flag_in_help "save" "--to" save
check_flag_in_help "save" "--from" save
check_flag_in_help "resume" "--from" resume
check_flag_in_help "resume" "--to" resume
check_flag_in_help "status" "--json" status
check_flag_in_help "status" "--verbose" status
check_flag_in_help "status" "--all" status
check_flag_in_help "clean" "--yes" clean
check_flag_in_help "clean" "--saves" clean
check_flag_in_help "env export" "--no-ensure" env export
check_flag_in_help "env export" "--json" env export
check_flag_in_help "agent list" "--description" agent list
check_flag_in_help "cluster start" "--max-workers" cluster start
check_flag_in_help "cluster start" "--min-workers" cluster start
check_flag_in_help "agent setup" "--all" agent setup

echo "=== Flag placement (global OR subcommand) ==="
# Bash 3.2 (macOS /bin/bash) has no negative array indices — peel the flag off
# the end explicitly so local CI matches Ubuntu runners.
for spec in \
    "save --list" \
    "status --json" \
    "status --verbose" \
    "status --all" \
    "env export --no-ensure" \
    "env export --json"; do
    read -r -a parts <<< "$spec"
    n=${#parts[@]}
    flag="${parts[$((n - 1))]}"
    cmd=("${parts[@]:0:$((n - 1))}")
    check_help_accepts_flag "$spec" "$flag" "${cmd[@]}"
done

echo "=== Smoke invocations (lab env) ==="
export HOME="/tmp/astroai-lab-audit-$$"
export WORK="$HOME/work"
export SCRATCH="$HOME/scratch"
mkdir -p "$HOME/work" "$HOME/scratch"
trap 'rm -rf "$HOME"' EXIT
eval "$("${CLI[@]}" env export)"

check_invocation "help" help
check_invocation "help -c agent" help -c agent
check_invocation "help -c nested" help -c "agent list"
check_invocation "help json" help --json
check_invocation "help -c json" help -c status --json
check_invocation "status" status
check_invocation "status json sub" status --json
check_invocation "status json global" --json status
check_invocation "clean dry-run" clean --dry-run
check_invocation "save list" save --list
check_invocation "save list json" save --list --json
check_invocation "config show" config show
check_invocation "config path" config path
check_invocation "env export" env export
check_invocation "env export json sub" env export --json
check_invocation "env export json global" --json env export
check_invocation "agent list" agent list
check_invocation "agent list ui" agent list --ui
check_invocation "agent plugins list" agent plugins list
check_invocation "agent plugins list kind" agent plugins list --kind mcp

echo ""
if [[ "$FAIL" -eq 0 ]]; then
    echo "CLI audit passed."
    exit 0
fi
echo "$FAIL audit failure(s)." >&2
exit 1
