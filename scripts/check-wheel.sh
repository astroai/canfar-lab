#!/usr/bin/env bash
# Fail if the built wheel omits the non-Python assets the CLI opens by path.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${ROOT}"

tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT

python="${ROOT}/.pixi/envs/default/bin/python"
if [[ ! -x "${python}" ]]; then
    python="$(command -v python3)"
fi

"${python}" -m venv "${tmp}/venv"
"${tmp}/venv/bin/pip" install -q build
"${tmp}/venv/bin/python" -m build --wheel --outdir "${tmp}/dist" "${ROOT}"

wheel=("${tmp}/dist"/*.whl)
if [[ ${#wheel[@]} -ne 1 || ! -f "${wheel[0]}" ]]; then
    echo "expected one wheel in ${tmp}/dist" >&2
    exit 1
fi

"${tmp}/venv/bin/python" - "${wheel[0]}" <<'PY'
import sys
import zipfile

names = set(zipfile.ZipFile(sys.argv[1]).namelist())
need = (
    "canfar_lab/data/desk/dock.html",
    "canfar_lab/data/agent/agents/dsh.yaml",
    "canfar_lab/data/agent/skills/canfar-session/SKILL.md",
)
missing = [name for name in need if name not in names]
if missing:
    print("wheel is missing package data:", file=sys.stderr)
    for name in missing:
        print(f"  {name}", file=sys.stderr)
    raise SystemExit(1)
print(f"wheel data ok: {sys.argv[1].rsplit('/', 1)[-1]}")
PY
