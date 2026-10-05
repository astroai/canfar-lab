#!/usr/bin/env bash
# Update all git clones under $SRC/{orgs}.
# Default Mac: ~/src/{sfabbro,opencadc,astroai}.
# CANFAR: SRC=$WORK ORGS="astroai sfabbro" bash update-repos.sh
# Repos with an `upstream` remote (personal forks) sync main from upstream,
# not from origin. Never auto-pushes to astroai.
# Exit non-zero if any repo needs attention; those are listed at the end.
set -euo pipefail

SRC="${SRC:-$HOME/src}"
# Space-separated org directory names under $SRC
ORGS="${ORGS:-sfabbro opencadc astroai}"

# Fast path: delegate to canfar lab sync if available, or canfar-lab sync
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
if command -v canfar >/dev/null 2>&1 && canfar lab sync --help >/dev/null 2>&1 && [[ "$#" -eq 0 ]]; then
    exec canfar lab sync --root "$SRC"
elif command -v canfar-lab >/dev/null 2>&1 && canfar-lab sync --help >/dev/null 2>&1 && [[ "$#" -eq 0 ]]; then
    exec canfar-lab sync --root "$SRC"
elif [[ -f "$SCRIPT_DIR/scripts/workspace-sync.py" && "$#" -eq 0 ]]; then
    exec python3 "$SCRIPT_DIR/scripts/workspace-sync.py" --root "$SRC" sync
fi


LOG=$(mktemp)
trap 'rm -f "$LOG"' EXIT

for org in $ORGS; do
  base="$SRC/$org"
  [[ -d "$base" ]] || continue
  for dir in "$base"/*/; do
    [[ -d "$dir/.git" ]] || continue
    label="$org/$(basename "$dir")"
    printf '… %s\n' "$label"
    (
      set +e
      cd "$dir" || { echo "ATTENTION|$label|cannot cd"; exit 0; }
      branch=$(git rev-parse --abbrev-ref HEAD)
      if [[ "$branch" == "HEAD" ]]; then
        echo "ATTENTION|$label|detached HEAD"
        exit 0
      fi

      dirty=$(git status --porcelain --untracked-files=no | wc -l | tr -d ' ')
      stashed=0
      if [[ "$dirty" != "0" ]]; then
        if git stash push -m "update-repos $(date +%Y%m%dT%H%M%S)" --quiet; then
          stashed=1
        else
          echo "ATTENTION|$label|dirty working tree, stash failed"
          exit 0
        fi
      fi

      # Canonical remote for sync: upstream if present (fork workflow), else origin
      sync_remote=origin
      if git remote get-url upstream >/dev/null 2>&1; then
        sync_remote=upstream
        if ! git fetch --prune origin 2>/dev/null; then
          echo "ATTENTION|$label|origin fetch failed"
          [[ "$stashed" == "1" ]] && git stash pop --quiet
          exit 0
        fi
      fi

      if ! git fetch --prune "$sync_remote" 2>/dev/null; then
        echo "ATTENTION|$label|$sync_remote fetch failed"
        [[ "$stashed" == "1" ]] && git stash pop --quiet
        exit 0
      fi

      if ! git rev-parse --verify --quiet "$sync_remote/$branch" >/dev/null; then
        # Feature branch only on fork — fall back to origin
        if [[ "$sync_remote" == "upstream" ]] && git rev-parse --verify --quiet "origin/$branch" >/dev/null; then
          sync_remote=origin
        else
          echo "ATTENTION|$label|no $sync_remote/$branch"
          [[ "$stashed" == "1" ]] && git stash pop --quiet
          exit 0
        fi
      fi

      # Keep main tracking upstream when forked; leave feature branches on origin
      if [[ "$sync_remote" == "upstream" && "$branch" == "main" ]]; then
        git branch --set-upstream-to="upstream/main" main >/dev/null 2>&1 || true
      elif [[ "$sync_remote" == "origin" ]]; then
        git branch --set-upstream-to="origin/$branch" "$branch" >/dev/null 2>&1 || true
      fi

      ahead=$(git rev-list --count "$sync_remote/$branch..HEAD")
      behind=$(git rev-list --count "HEAD..$sync_remote/$branch")

      if [[ "$behind" == "0" ]]; then
        echo "OK|$label|via=$sync_remote ahead=$ahead"
      elif [[ "$ahead" == "0" ]]; then
        if git merge --ff-only "$sync_remote/$branch" --quiet; then
          echo "FF|$label|via=$sync_remote behind_was=$behind"
        else
          echo "ATTENTION|$label|ff-only from $sync_remote failed"
        fi
      else
        if git rebase "$sync_remote/$branch" --quiet; then
          echo "REBASE|$label|via=$sync_remote ahead=$ahead behind=$behind"
        else
          git rebase --abort 2>/dev/null || true
          echo "ATTENTION|$label|diverged vs $sync_remote (ahead=$ahead behind=$behind)"
        fi
      fi

      if [[ "$stashed" == "1" ]]; then
        if ! git stash pop --quiet 2>/dev/null; then
          echo "ATTENTION|$label|stash pop conflict — resolve, then: git stash drop"
        fi
      fi
    ) | tee -a "$LOG"
  done
done

echo
echo "=== summary ==="
awk -F'|' '
  $1=="OK"{ok++}
  $1=="FF"{ff++; fflist=fflist sprintf("  %s\n",$2)}
  $1=="REBASE"{rb++; rblist=rblist sprintf("  %s\n",$2)}
  $1=="ATTENTION"{att++; attlist=attlist sprintf("  %-40s %s\n",$2,$3)}
  END{
    print "up to date:   " (ok+0)
    print "fast-forward: " (ff+0)
    if (ff>0) printf "%s", fflist
    print "rebased:      " (rb+0)
    if (rb>0) printf "%s", rblist
    print "attention:    " (att+0)
    if (att>0) {
      print ""
      print "=== needs attention ==="
      printf "%s", attlist
    }
  }
' "$LOG"

att_count=$(awk -F'|' '$1=="ATTENTION"{c++} END{print c+0}' "$LOG")
if [[ "$att_count" -gt 0 ]]; then
  exit 1
fi
exit 0
