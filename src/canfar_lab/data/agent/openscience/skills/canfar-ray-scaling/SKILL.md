---
name: canfar-ray-scaling
description: Scale an analysis beyond this CANFAR session with the astroai Ray cluster tools — size the work, start an autoscaling cluster, submit many small jobs, watch them and report. Use when a task needs more CPU, RAM, GPU or wall time than the session has, or has to run over many files, targets or parameter sets.
summary: "Size, start and run many small jobs on the CANFAR Ray cluster."
category: astronomy
metadata:
  maintainer: canfar-lab
---

# Scaling out on CANFAR with Ray

## Decide first

1. Call `astroai_session_resources`. If the job fits in the session's cores and RAM with
   headroom, run it here; a cluster costs minutes of start-up and shared hardware.
2. Prototype on a small subset in the session (one file, one target, one chunk) and time
   it. Extrapolate: `total_cpu_hours ≈ per_item_seconds × n_items / 3600`.
3. Prefer many small independent tasks (one per file/target/chunk) over one large job:
   they schedule on any free worker, fail and retry individually, and scale linearly.

## Data must live where workers can read it

- Workers are separate CANFAR sessions: they do **not** see this session's `$SCRATCH`.
- Put inputs, scripts and outputs under `/arc/projects/<project>/…` (`$HOME` only for
  small outputs: it is quota-limited; both are mounted everywhere), or have each task fetch its own input (CADC/VOSpace/URL)
  to its own scratch and write only the result back to `/arc`.
- `inputs` / `outputs` on `astroai_job_run` / `astroai_job_submit` record provenance on the
  job; they do not copy files.
- Each task writes its own output file (write to `name.tmp`, then rename). Never have
  many tasks append to one file or one SQLite database on `/arc` (CephFS).

## Run

1. State the request and get approval: workers × cores × RAM (× GPUs) and expected hours.
2. `astroai_cluster_start` with `max_workers`, `cores`, `ram` (GiB), `gpus`,
   `idle_timeout_minutes`; it reuses a running cluster, so calling it again is safe.
3. Driver script pattern (`/arc/projects/<project>/runs/<run>/driver.py`):

   ```python
   import pathlib, ray

   ray.init()  # address comes from the job environment

   @ray.remote(num_cpus=1, memory=2 * 1024**3, max_retries=2)
   def process(path: str, out_dir: str) -> str:
       out = pathlib.Path(out_dir) / (pathlib.Path(path).stem + ".csv")
       tmp = out.with_suffix(".tmp")
       ...  # analysis; write tmp
       tmp.rename(out)
       return str(out)

   files = sorted(pathlib.Path("/arc/projects/myproj/data").glob("*.fits"))
   outs = ray.get([process.remote(str(f), "/arc/projects/myproj/runs/r1/out") for f in files])
   print(len(outs), "done")
   ```

   Match `num_cpus`/`memory` to what one task really uses. Ray sets `OMP_NUM_THREADS`
   to `num_cpus` for each worker; setting it inside a task has no effect once numpy or
   BLAS is loaded, so override it in the job environment if needed.
4. `astroai_job_submit` (`cmd: "python driver.py"`, `cwd` on `/arc`, a readable `run_id`)
   for long runs, or `astroai_job_run` for short ones that you wait on.
5. Follow with `astroai_job_status` / `astroai_job_logs`; summarise with
   `astroai_jobs_report`.
6. Check outputs: count files against inputs, spot-check a few values against the
   session prototype, list failures with their errors.
7. `astroai_cluster_stop` when finished unless the user wants to keep it; idle workers
   also stop after `idle_timeout_minutes`.

## Pitfalls

- A job that reads a large file from `/arc` in every task can saturate storage; stage one
  copy per worker to its scratch or split the file first.
- Returning big arrays through `ray.get` goes through the driver's memory; write results
  to files and return paths.
- GPU workers are scarce: request them only for GPU code, and check the code actually
  uses the GPU (`torch.cuda.is_available()`) in the prototype.
