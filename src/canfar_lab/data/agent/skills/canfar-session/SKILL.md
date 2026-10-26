---
name: canfar-session
description: >-
  CANFAR session layout for AstroAI Studio. Use before launching a headless
  job, writing files, or moving data: /scratch, /arc/home, /arc/projects,
  AstroAI images, and where code must live.
---

# This CANFAR session

The `canfar` skill (installed beside this one) is the authority for `canfar`
commands, headless replicas, and Storage Identifiers. Read it before composing
a `canfar` command. This skill is the authority for paths and images on an
AstroAI session.

## Where files go

| Path | Lifetime | Use |
| --- | --- | --- |
| `/scratch` | This session only | Large temp data, caches |
| `$WORK` (`$SCRATCH/src`, `/scratch/src`) | This session only | Repos and pixi/uv envs |
| `/arc/projects/<group>` | Persistent, shared | Results, scripts, data the next session must see |
| `/arc/home/$USER` (`$HOME`) | Persistent, small | Dotfiles and keys only |
| `arc:` / `vault:` via `canfar data` | VOSpace | Publish or fetch data outside the mounts |

`/scratch` is not shared with another session or a headless worker. A file
left only on `/scratch` or `$HOME` is the wrong place for a job output.

## Rules

- Images for this work are `images.canfar.net/astroai/<name>:<tag>`. Do not
  launch `skaha/*`. The generic `skaha/astroml` example in the `canfar` skill
  is not the image for an AstroAI session.
- Install project packages with `pixi` or `uv` under `$WORK`. Never
  `pip install --user` and never write packages under `$HOME/.local`.
- Headless runners set `PYTHONNOUSERSITE=1` and unset `PYTHONPATH`.
- A headless session runs a command and exits. It does not count toward the
  interactive session cap. Persist outputs to `/arc/projects/<group>` or
  `canfar data` before it ends.
- Code reaches another pod by git (`canfar lab clone <repo> --update`), not
  by a path on this session's `/scratch`. `canfar lab save` stores lockfiles,
  not source.

## Headless

```bash
canfar create headless images.canfar.net/astroai/base:latest \
  --name reduce --cpu 8 --memory 32 --env PYTHONNOUSERSITE=1 -- \
  python /arc/projects/<group>/scripts/reduce.py
```

Read `canfar create --help` and the `canfar` skill's batch reference before
adding flags. Reuse a Running session of the same kind when `canfar ps -o json`
already has one.

## Data

List or copy with `canfar data`. Paths look like `arc:/home/<user>/...` and
`local:/absolute/path`. Read the `canfar` skill's data reference before the
first copy. Confirm the destination with `canfar data ls` after a write.
