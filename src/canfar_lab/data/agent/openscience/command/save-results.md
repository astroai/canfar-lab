---
description: Copy results worth keeping from session scratch to persistent CANFAR storage
---
Persist this session's results before scratch is deleted. Destination (optional): $ARGUMENTS

1. List candidate outputs created or changed in this session under $SCRATCH, $SRCDIR and the project directory, with sizes. Mark which exist only on scratch.
2. Separate results worth keeping (figures, tables, reduced products, notebooks, scripts, run configs and logs) from regenerable caches and raw archive downloads that can be fetched again; say which is which.
3. Propose a destination: the directory given above, else /arc/projects/<project>/runs/<date>-<name>/ when the user has a project (list /arc/projects to see), else $HOME/results/<date>-<name>/. Check free space first (astroai_session_resources or df -h).
4. Ask for confirmation, then copy with rsync -a (never move or delete the originals), and write a short README.md next to the results: what each file is, the inputs (archive identifiers, queries) and the commands that made them.
5. Verify: compare file counts and sizes between source and destination, and report the final paths.
