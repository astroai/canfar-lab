# Harness (always on)

Use a **coding harness** for implementation work: workflow loop + file memory + verification.

1. If `.cursor/harness/playbook.md` exists at the **git root**, read it before non-trivial coding.
2. Follow `harness-coding`: plan → execute → **verify** → improve.
3. Persist lessons under `.cursor/harness/` (trajectories, failures) — not long chat scrollback.
4. Bootstrap once per repo: `bash ~/src/astroai/canfar-lab/scripts/init-harness.sh`

Optional: `harness-reflect` / `harness-improve` (manual). Pair with `continual-learning` for `AGENTS.md` preference memory.
