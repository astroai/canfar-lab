#!/usr/bin/env bash
# Install canfar-lab skills, rules, and MCP into the local agent homes.
set -euo pipefail
REPO_ROOT="$(cd "$(dirname "$0")/../../.." && pwd)"
exec bash "$REPO_ROOT/scripts/install-agents.sh" "$@"
