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

# 2. AstroAI Studio Team branding (preset name only; never model pins)
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
# astroai never presets models: upstream must not reintroduce agentOptions.model.
bad = [l for l in text.splitlines() if l.strip().startswith("model:")]
assert not bad, f"upstream preset pins models — strip agentOptions.model first: {bad[:3]}"
print("checked agent.cordis.yml: no model pins")

# validate.mjs: require no model pin (upstream may still require one).
validate = dst / "validate.mjs"
vtext = validate.read_text(encoding="utf-8")
old = "  if (!agentOptions?.model) fail(row.id, 'panel row needs a pinned model')\n"
new = (
    "  // astroai never presets models — children inherit the session route.\n"
    "  if (agentOptions?.model) {\n"
    "    fail(row.id, 'panel row must not pin model (choose models in dsh Settings)')\n"
    "  }\n"
)
if old in vtext:
    validate.write_text(vtext.replace(old, new), encoding="utf-8")
    print("patched validate.mjs: reject model pins")
elif "must not pin model" in vtext:
    print("validate.mjs already rejects model pins")
else:
    raise SystemExit("validate.mjs model-pin check changed — update sync-review-bench.sh")
PY

echo "ok: synced $SRC -> $DST"
if command -v node >/dev/null 2>&1; then
  node "$DST/validate.mjs" --root "$DST" || echo "(validate needs a dsh install: --node-modules <dir>)"
else
  echo "(node unavailable — skipped validate.mjs)"
fi
