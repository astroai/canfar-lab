# Working on CANFAR (AstroAI)

You are running inside a CANFAR Science Platform session (a Kubernetes pod
started by Skaha). Treat these facts as ground truth over general habits.

## Storage: where files live

| Path | Persists? | Use for |
| --- | --- | --- |
| `/arc/home/$USER` (`$HOME`) | yes, quota-limited, shared by all your sessions | code, configs, small results |
| `/arc/projects/<project>` | yes, shared with the project group | team data and final products |
| `$SCRATCH` (`/scratch`) | **no**: deleted when the session ends | downloads, caches, large intermediates |
| `$SRCDIR` (usually `/scratch/src`) | **no** | default working folder in AstroAI sessions |

- Download archive data to `$SCRATCH`, not to `/arc/home` (quota).
- Before the user leaves, copy results worth keeping to `/arc/projects/<project>` or
  `$HOME`, or tell them to run `canfar-lab save`. Say explicitly when something lives
  only on scratch.
- `/arc` is CephFS shared between sessions: avoid SQLite databases and lock-heavy
  writes there; write-then-rename for outputs other sessions read.
- VOSpace (`vos:`, `arc:`, `vault:` URIs) is remote storage reached with `vcp`/`vls`
  or the `vospace_list`/`vospace_copy` tools; it needs `canfar login`.

## Compute

- This session is a single pod with capped CPU/RAM (call `astroai_session_resources`
  first). Prototype here on a subset.
- For heavy or parallel work, use the Ray cluster tools: `astroai_cluster_start`
  (reuses a running cluster), then `astroai_job_submit` / `astroai_job_run`, then
  `astroai_job_status`, `astroai_job_logs`, `astroai_jobs_report`. Stop idle clusters
  with `astroai_cluster_stop`. These launch real sessions on shared hardware: state
  the resources you will request and get the user's approval.
- Set `OMP_NUM_THREADS` to the cores you actually have; never assume a laptop GPU.

## Astronomy data

- Resolve names with `astroai_resolve_target` (CDS Sesame) instead of recalling
  coordinates from memory.
- CADC archive (CFHT, JCMT, Gemini, HST, JWST, VLASS, …): `astroai_cadc_search` for a
  cone search, `astroai_tap_query` with `service: cadc` for ADQL on `ivoa.ObsCore` /
  `caom2.*`, `astroai_cadc_download` to fetch files into `$SCRATCH`.
- Other archives through `astroai_tap_query`: `gaia` (gaiadr3.gaia_source), `simbad`,
  `vizier` (quote table names: `"I/355/gaiadr3"`), `ned`, `mast`.
- Literature: `astroai_arxiv_search`, `astroai_ads_search` (needs `ADS_API_TOKEN`).
- Keep result tables small (TOP / maxrec); large results are saved as CSV and the
  tool returns the path.

## Science correctness

- Carry units explicitly (astropy.units where available) and state frames (ICRS,
  epoch, equinox) for coordinates.
- Sanity-check numbers against known values or limits before reporting them (e.g.
  a parallax in mas vs arcsec, magnitudes vs fluxes, wavelength units in ObsCore
  `em_min`/`em_max` are metres).
- Report what was actually run and what was assumed.

## Keys and tools

- Model API keys are managed in the AstroAI hub (the **AstroAI** chip, `/astroai-agents/`);
  never print key values.
- Commands run with the user's own permissions (full access is the CANFAR default:
  the OS sandbox would block archive network access and `/arc` reads). Never delete
  or overwrite files under `/arc` without asking.
- Platform CLIs on `PATH`: `canfar`, `cadcget`, `cadc-tap`, `vcp`, `vls`, `canfar-lab`.
