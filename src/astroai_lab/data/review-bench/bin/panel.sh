#!/usr/bin/env bash
# Terminal panel runner — the review bench without a browser.
#
#   ~/dsh/panel.sh <repo> "C1: <falsifiable claim>; C2: ..." [slug]
#
# Runs phases 0–2 of ~/dsh/skills/review-panel (freeze, blind parallel round,
# audit) as a headless one-shot and writes the same artifacts the web chair
# writes: panel/<YYYY-MM-DD>-<slug>/{00-brief.md,01-findings.json,02-report.md}.
#
# Works anywhere with a shell: AstroAI terminal sessions, vscode and
# notebook terminals, your laptop. Needs a model credential: either
# `~/dsh/use-opencode-go.sh` (opencode Zen, no DeepSeek key) or
# `export DEEPSEEK_API_KEY=sk-...`.
#
# Honest delta vs the web preset: workflow children inherit one generic persona,
# so each lens prompt carries its specialist persona text verbatim (read from
# the preset file) instead of a child-local system prompt, and cross-examination
# is one targeted follow-up round, not continuable dialogue. The brief, probes,
# evidence audit, ledger, labels, and falsification tests are the same.
set -euo pipefail

repo="${1:?usage: panel.sh <repo> \"C1: ...; C2: ...\" [slug]}"
claims="${2:?usage: panel.sh <repo> \"C1: ...; C2: ...\" [slug]}"
slug="${3:-review}"
id="$(date +%F)-$slug"

cd "$repo"
if [ -e "panel/$id/00-brief.md" ]; then
  id="$id-$(date +%H%M)"
fi
mkdir -p "panel/$id"

patch=()
if [ -f .dsh/cordis.patch.yml ]; then
  patch=(--patch .dsh/cordis.patch.yml)
fi

read -r -d '' task <<EOF || true
You are chairing a review panel. Protocol: load ~/dsh/skills/review-panel/SKILL.md
and follow phases 0–2 (freeze, blind parallel round, evidence audit), with the
finding schema in ~/dsh/skills/review-panel/references/rubric.md.

Claims under review:
$claims

Artefact: this repo @ HEAD. Report dir: panel/$id/ (create it if missing).

Phase 0 — freeze and brief. Record: git rev-parse HEAD, git status --porcelain,
pixi.lock (or requirements) hash, torch/CUDA versions, and the exact command
reproducing the headline result (run it once, keep output). Write
panel/$id/00-brief.md using
~/dsh/skills/review-panel/references/brief-template.md. If the claims above are
too vague to be falsifiable (named metric, split, threshold), propose a claim
list first and proceed with your best reading — state the assumption in the brief.

Phase 1 — blind parallel round via the workflow tool. Read
~/dsh/skills/review-panel/references/panel-round1.js and run it with one lens
per claim-group: statistician, mathematician, data scientist, ml_engineer,
physicist, astrophysicist, software_engineer, writing_editor (drop lenses that
are irrelevant to the artefact and state the panel size in the brief).
Headless workflow children share one generic persona, so paste each lens's
specialist persona text VERBATIM from
~/dsh/presets/review-bench/agent.cordis.yml (the ask_<role> rows) into its
workflow task, followed by: open with the strongest alternative explanation
for the headline result, run that persona's mandatory probes, then judgement
checks. One pass each, <=25 tool calls, no delegation, no writes to the repo;
mutation experiments only on copies under /tmp. Persist raw returns to
panel/$id/01-findings.json BEFORE reasoning further.

Phase 2 — audit before belief. Re-run every cited command yourself; a finding
whose evidence does not reproduce is UNVERIFIED. One targeted follow-up round
only for contested findings, then verdict.

Write panel/$id/02-report.md using
~/dsh/skills/review-panel/references/report-template.md: verdict per claim
(established | suggestive | speculative, binding on the prose), fix list,
verbatim dissent, and open falsification tests. Print the verdict table to
stdout at the end.
EOF

# Prefer a real `dsh` binary — `npx -y @deepseek-ai/dsh` swallows launcher flags
# on npm ≥ 10. Image bake puts dsh on PATH; laptop: npm install -g @deepseek-ai/dsh@…
dsh_bin="${ASTROAI_STUDIO_DSH:-}"
if [[ -z "${dsh_bin}" ]]; then
  dsh_bin="$(command -v dsh || true)"
fi
if [[ -z "${dsh_bin}" ]]; then
  for candidate in \
      /opt/astroai/bin/dsh \
      "${ASTROAI_LAB_BIN_DIR:+${ASTROAI_LAB_BIN_DIR}/dsh}" \
      "${SCRATCH:+${SCRATCH}/.local/bin/dsh}" \
      ~/.npm-global/bin/dsh \
      ~/.local/bin/dsh \
      /usr/local/bin/dsh; do
    [[ -n "${candidate}" ]] || continue
    if [[ -x "${candidate/#\~/$HOME}" ]]; then
      dsh_bin="${candidate/#\~/$HOME}"
      break
    fi
  done
fi
if [[ -z "${dsh_bin}" ]]; then
  echo "panel.sh: no dsh executable found (install @deepseek-ai/dsh@0.1.5-rc.2 globally)" >&2
  exit 1
fi

# shellcheck disable=SC2086
exec "${dsh_bin}" --profile headless ${patch[@]+"${patch[@]}"} "$task"
