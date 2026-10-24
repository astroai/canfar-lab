#!/usr/bin/env bash
# One agent-config installer: `canfar lab agent setup`.
# `work` stays a separate opt-in for scripts/install-work-models.py.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
FORCE=0
WORK=0

for arg in "$@"; do
  case "$arg" in
    --force) FORCE=1 ;;
    work) WORK=1 ;;
    all|skills|rules|instructions|mcp|pi|omp) ;;
    -h|--help)
      echo "Usage: $0 [all|work] [--force]"
      echo "Default runs: canfar lab agent setup"
      echo "work runs: $REPO_ROOT/scripts/install-work-models.py"
      exit 0
      ;;
    *)
      echo "unknown arg: $arg" >&2
      exit 1
      ;;
  esac
done

if [[ "$WORK" -eq 1 ]]; then
  exec python3 "$REPO_ROOT/scripts/install-work-models.py"
fi

setup_args=(agent setup)
if [[ "$FORCE" -eq 1 ]]; then
  setup_args+=(--force)
fi

if command -v canfar-lab >/dev/null 2>&1; then
  exec canfar-lab "${setup_args[@]}"
fi
if command -v canfar >/dev/null 2>&1; then
  exec canfar lab "${setup_args[@]}"
fi
exec python3 -m canfar_lab "${setup_args[@]}"
