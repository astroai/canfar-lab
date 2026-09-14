---
name: canfar-session
description: >-
  CANFAR / AstroAI session resource limits, storage tiers, and when to offload
  work to headless ray-manager batch compute. Use when planning heavy jobs,
  GPU work, or diagnosing OOM / quota issues in Studio or webterm.
---

# CANFAR session awareness

## Storage

| Tier | Path | Lifetime | Shared? |
|------|------|----------|---------|
| Source | `$SRCDIR` / `$WORK` → `/scratch/src` | Session (survives container OOM) | No |
| Scratch | `/scratch` | Session | **No** — other pods cannot see it |
| Home | `/arc/home/<you>` | Persistent | Yes |
| Projects | `/arc/projects/<group>` | Persistent | Yes (ACLs) |

Never `pip install --user` or write large caches under `$HOME/.local` on `/arc/home`.

## Interactive Studio session

- CPU/RAM are whatever the portal request asked for (often modest).
- Contributed session quota is small (~3). Do not burn slots on long GPU training.
- Prefer **AstroAI hub → Start batch compute** (ray-manager) for heavy/GPU work.
- Profile file: `~/.dsh/studio-profile.yaml` (`profile: canfar|laptop`).

## Laptop Studio

- No Skaha quota; local CPU/RAM/GPU.
- Same skills and `~/.astroai/lab/.env` keys as CANFAR.

## Checks

```bash
astroai status
echo "SCRATCH=$SCRATCH WORK=$WORK SRCDIR=$SRCDIR"
test -n "${skaha_sessionid:-}" && echo "on Skaha session $skaha_sessionid"
```

When a claim needs more resources than the interactive session: freeze the
artefact, push code, start batch compute, and re-run there — do not pretend the
interactive pod has unlimited RAM/GPU.
