# Optional configuration

Paths resolve from session environment variables (`SRCDIR`, `SCRATCH`,
`PROJECT`) and standard CANFAR mount points. `WORK` is an alias of `SRCDIR`.
Optional preferences live at **`~/.astroai/lab/config.yaml`**. Every key is optional.

```yaml
# example
default_pm: pixi          # pixi | uv
clone_from_env: ml-base   # default --from-env name
srcdir: ~/src             # default source dir (else $SRCDIR / $SCRATCH/src)
```

Environment variables override YAML:

| Variable | YAML key |
|----------|----------|
| `SRCDIR` | `srcdir` / `work_dir` |
| `WORK` | `srcdir` / `work_dir` (same path; `SRCDIR` wins if both are set) |
| `SCRATCH` | `scratch_dir` |
| `CANFAR_LAB_SAVE_DIR` | `save_dir` |
| `CANFAR_LAB_DEFAULT_PM` | `default_pm` |
| `CANFAR_LAB_CLONE_FROM_ENV` | `clone_from_env` |

Inspect current settings:

```bash
canfar lab config show
canfar lab config path
canfar lab --json config show
```

Workbench settings stay in `~/.astroai/lab/` so published git repos remain
portable (`pixi.toml` / `pyproject.toml` only).
