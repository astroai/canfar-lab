#!/usr/bin/env bash
# Sync ~/dsh review-bench sources into src/astroai_lab/data/review-bench/.
# Single source of truth remains ~/dsh; the pip package ships the copy.
# CI fails when the copy is stale (run this script, commit the result).
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SRC="${DSH_SRC:-$HOME/dsh}"
DST="$ROOT/src/astroai_lab/data/review-bench"

[ -d "$SRC/presets/review-bench" ] || { echo "missing: $SRC/presets/review-bench" >&2; exit 1; }
[ -d "$SRC/skills/review-panel" ] || { echo "missing: $SRC/skills/review-panel" >&2; exit 1; }

mkdir -p "$DST/presets/review-bench" "$DST/skills" "$DST/bin" "$DST/project-template/.dsh"
cp "$SRC/presets/review-bench/agent.cordis.yml" "$DST/presets/review-bench/agent.cordis.yml"
cp "$SRC/presets/review-bench/preset.yml" "$DST/presets/review-bench/preset.yml"
rm -rf "$DST/skills/review-panel"
cp -r "$SRC/skills/review-panel" "$DST/skills/review-panel"
cp "$SRC/panel.sh" "$SRC/dsh-web.sh" "$SRC/validate.mjs" "$SRC/HOWTO.md" "$SRC/README.md" "$DST/"
cp "$SRC/panel.sh" "$SRC/dsh-web.sh" "$DST/bin/"

# Managed-install overrides (re-applied on every sync; do not edit by hand):
# 1. preset skill root -> ~/.astroai/lab/review-bench/skills (+ ~/dsh fallback)
python3 - "$DST/presets/review-bench/agent.cordis.yml" <<'PY'
import sys
p = sys.argv[1]
text = open(p).read()
old = "      - !!js process.env.HOME + '/dsh/skills'\n"
new = ("      - !!js (process.env.ASTROAI_LAB_DATA || process.env.HOME + '/.astroai/lab')"
       " + '/review-bench/skills'\n      - !!js process.env.HOME + '/dsh/skills'\n")
assert old in text, "upstream preset skill-root line changed — update this script"
open(p, "w").write(text.replace(old, new))
print("patched customSkillDirs -> managed bench + ~/dsh fallback")
PY

# 2. AstroAI Studio Team branding (preset name + flash model pins)
# Keep in sync with canfar-lab Studio Team expansion; do not revert to eight-only.
python3 - "$DST" <<'PY'
from pathlib import Path
import sys

dst = Path(sys.argv[1])
preset = dst / "presets" / "review-bench" / "preset.yml"
preset.write_text(
    "name: AstroAI Studio Team\n"
    "description: >-\n"
    "  AstroAI Studio chaired multi-persona team — science reviewers plus CANFAR\n"
    "  expert, devops, plot master, desloper, innovator, and devil's advocate —\n"
    "  blind round, cross-examination, evidence-gated verdicts. Coding agent is the\n"
    "  default Studio New session; pick this preset for Team review. Also:\n"
    "  `astroai panel run` (headless).\n"
    "order: 10\n",
    encoding="utf-8",
)
print("rewrote preset.yml -> AstroAI Studio Team")

cordis = dst / "presets" / "review-bench" / "agent.cordis.yml"
text = cordis.read_text(encoding="utf-8")
# Flash roles on OpenCode Go: prefer deepseek-v4.1-flash when upstream still has v4-flash.
for role in ("data_scientist", "ml_engineer", "software_engineer"):
    # ponytail: line-local replace after toolName ask_<role> block; ceiling = multi-model rows
    pass
# Replace bare deepseek-v4-flash pins that are not vision-exp (vision keeps v4-flash-vision-exp).
lines = text.splitlines(keepends=True)
out = []
current = None
for line in lines:
    if "toolName: ask_" in line:
        current = line.split("ask_", 1)[1].strip()
    if (
        current in {"data_scientist", "ml_engineer", "software_engineer"}
        and "model: deepseek-v4-flash" in line
        and "vision" not in line
        and "v4.1" not in line
    ):
        line = line.replace("deepseek-v4-flash", "deepseek-v4.1-flash")
        current = None
    out.append(line)
cordis.write_text("".join(out), encoding="utf-8")
print("pinned flash roles -> deepseek-v4.1-flash where applicable")
PY

echo "ok: synced $SRC -> $DST"
if command -v node >/dev/null 2>&1; then
  node "$DST/validate.mjs" --root "$DST" || echo "(validate needs a dsh install: --node-modules <dir>)"
else
  echo "(node unavailable — skipped validate.mjs)"
fi
