# Oh My Pi (omp) on CANFAR

1. Install onto scratch: `astroai agent install omp`
2. Optional OpenRouter key: https://openrouter.ai/keys — `export OPENROUTER_API_KEY=sk-or-v1-...`
3. Run `omp` and follow upstream auth prompts. Config is managed by omp itself (see https://github.com/can1357/oh-my-pi).
4. Binary lives under `$ASTROAI_LAB_BIN_DIR` (scratch).

## Keep runtime off `/arc/home`

`omp` defaults to `~/.omp` (natives ≈360MB, Puppeteer Chrome ≈380MB, SQLite
WAL DBs, session transcripts, composer autosaves, daemon sockets, logs). That
layout is catastrophic on CephFS: every `dlopen` and WAL write page-faults over
the network.

AstroAI redirects this automatically:

- Session env seeds `$XDG_{CACHE,DATA,STATE}_HOME/omp` and sets
  `PUPPETEER_CACHE_DIR` on scratch so *new* writes never touch `/arc`.
- `astroai agent setup` force-relocates existing `~/.omp/{natives,puppeteer,agent,run,logs}`
  onto scratch (symlinks back from the well-known home paths).

Small durable notes stay under `~/.config/omp/`. Do not copy natives or Chrome
back onto `/arc`.
