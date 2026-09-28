---
description: Plan and run an analysis on CANFAR's Ray cluster instead of this session
---
Scale this work out to the CANFAR Ray cluster: $ARGUMENTS

Load the canfar-ray-scaling skill and follow it:

1. Call astroai_session_resources and say whether the work actually needs a cluster.
2. Prototype one unit of work (one file, target or chunk) in this session, time it and measure its memory.
3. Show a sizing table: number of tasks, CPU and RAM per task, workers × cores × RAM (× GPUs), expected wall time and total CPU-hours. Put inputs and outputs on /arc (workers cannot see this session's scratch).
4. Wait for the user's approval of the resources, then start the cluster, submit the driver, and monitor it.
5. Check the outputs against the inputs and the prototype, report with astroai_jobs_report, and offer to stop the cluster.
