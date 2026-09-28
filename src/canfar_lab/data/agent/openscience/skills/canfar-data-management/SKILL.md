---
name: canfar-data-management
description: Organise, persist and share research data on CANFAR — scratch vs /arc home and project storage vs VOSpace, quotas, safe concurrent writes, and handing results to collaborators. Use when saving results, choosing where to download data, sharing with a team, running out of space, or wrapping up a session.
summary: "Where data lives on CANFAR (scratch, /arc, VOSpace) and how to persist and share it."
category: astronomy
metadata:
  maintainer: canfar-lab
---

# Data on CANFAR

## Where to put what

| Data | Location | Why |
| --- | --- | --- |
| Archive downloads, caches, intermediates | `$SCRATCH` | fast, large, deleted at session end |
| Code, notebooks, configs, small results | `$HOME` (`/arc/home/$USER`) | persistent, quota-limited |
| Team data, final products, inputs for Ray jobs | `/arc/projects/<project>` | persistent, group-shared |
| Long-term or published data, external sharing | VOSpace (`vos:`/`arc:`/`vault:`) | remote, ACL-controlled |

Check space before large downloads: `astroai_session_resources`, or
`df -h $SCRATCH $HOME /arc/projects/<project>` and `du -sh <dir>`.

## Layout for a project

```
/arc/projects/<project>/
  data/raw/        # read-only inputs (never modified in place)
  data/derived/    # regenerable products, with the script that made them
  runs/<date>-<name>/  # one directory per analysis run: config, logs, outputs
  results/         # figures and tables that go into papers
```

Record provenance next to outputs: the command or notebook, input identifiers
(CADC publisher DIDs, VizieR table names, query ADQL), software versions
(`python -c "import astropy; print(astropy.__version__)"`) and the date.

## Safe writes on shared storage

- `/arc` is CephFS, shared by all your sessions and your group. Write to a temporary
  name and rename (`out.tmp` → `out.fits`) so readers never see half a file.
- Do not keep SQLite databases or lock-heavy files on `/arc` when more than one session
  may write them; copy to `$SCRATCH`, work there, copy back.
- Group sharing depends on POSIX group permissions: check with `ls -ld` and keep files
  group-readable (`chmod -R g+rX <dir>`); never loosen permissions to world-writable.

## Moving data

- Within the session: `cp`/`rsync -a` between `$SCRATCH` and `/arc`.
- VOSpace: the `astroai_vospace_list` / `astroai_vospace_copy` tools, or
  `vcp`, `vls`, `vmkdir` in the terminal (needs a valid certificate: `canfar login`).
- CADC archive files: `astroai_cadc_download` (defaults to `$SCRATCH`) or `cadcget`.

## Wrapping up

Before the user leaves, list what exists only on `$SCRATCH` and offer to copy what is
worth keeping to `/arc/projects/<project>` or `$HOME` (or `canfar-lab save`). Say
plainly that scratch is deleted when the session ends.

## Never

- Delete or overwrite anything under `/arc` or VOSpace without explicit confirmation.
- Download large archive data into `$HOME` (quota) or into another user's directory.
