# AstroAI Studio settings for this repo

`canfar lab studio` created this folder. Studio reads it when you open the repo.

- `cordis.patch.yml` raises the shell command timeout to 10 minutes (pixi
  installs and benchmarks need it) and turns on search across past sessions.
- Put repo-specific agent skills in `.dsh/skills/<name>/SKILL.md`; Studio
  picks them up automatically.

Change the model or provider in Studio → Settings. Commit this folder to share
the settings with collaborators, or add `.dsh/` to `.gitignore` to keep them
to yourself.
