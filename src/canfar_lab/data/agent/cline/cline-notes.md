# Cline on CANFAR

1. Install the CLI onto scratch: `canfar lab agent install cline`
2. Optional OpenRouter key: https://openrouter.ai/keys — `export OPENROUTER_API_KEY=sk-or-v1-...`
3. Authenticate: `cline auth -p openrouter -k "$OPENROUTER_API_KEY" -m qwen/qwen3-coder:free`
   Or run `cline auth` and pick a provider interactively.
4. Config lives on `$HOME` (/arc/home); the binary lives under `$CANFAR_LAB_BIN_DIR` (scratch).
