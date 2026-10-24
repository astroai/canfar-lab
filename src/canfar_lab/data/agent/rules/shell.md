# Shell

- `#!/bin/bash` with `set -euo pipefail` (or `-e` only when `-u`/`-o pipefail` break intentional patterns).
- Quote variables; use `[[ ]]` in bash; prefer `$()` over backticks.
- Run `shellcheck` on non-trivial scripts before claiming done.
- Idempotent installs: skip if target exists unless `--force`; merge configs instead of blind overwrite.
