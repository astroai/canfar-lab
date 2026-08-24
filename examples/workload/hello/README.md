# First job

Check that `astroai run` can start a program on a live cluster.

1. AstroAI hub: **Start batch compute** (or portal: `ray-manager`, ≥8 GiB)
2. Then:

```bash
astroai cluster start
export ASTROAI_RAY_JOBS_ADDRESS=…   # printed by start; skip inside the manager
astroai run job.py --cpus 1
```

You should see `hello from ray` in the log. `run` waits until the job
finishes. `--cpus 1` is what makes Ray add a worker.
