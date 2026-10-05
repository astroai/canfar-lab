#!/usr/bin/env bash
# Full local CI for canfar-lab — mirrors .github/workflows/ci.yml.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${ROOT}"

if ! command -v pixi >/dev/null 2>&1; then
    echo "error: pixi is required (https://pixi.sh)" >&2
    exit 1
fi

pixi install --frozen

echo "==> ruff check"
pixi run lint

echo "==> ruff format"
pixi run format-check

echo "==> ty check"
# CONDA_PREFIX can point ty at a conda site-packages on some dev machines.
env -u CONDA_PREFIX pixi run typecheck

echo "==> CLI audit"
# GITHUB_ACTIONS=true makes typer/rich emit ANSI SGR codes in --help output,
# exactly as on ubuntu-latest runners. This exercises the audit script's
# strip_ansi path locally so the local gate matches the remote CI gate.
GITHUB_ACTIONS=true bash scripts/audit-cli-help.sh

echo "==> pytest"
pixi run test

echo "ok: all local CI checks passed"