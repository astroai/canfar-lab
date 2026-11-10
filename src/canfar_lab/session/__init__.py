"""Session-level Python API for the dsh image's Lab tab.

Plain functions over the existing agent, save, and cluster internals, so a
web server can call them in-process instead of shelling out to
``canfar-lab --json``. Every function returns a JSON-safe dict or raises
:class:`canfar_lab.errors.LabError`.

* :mod:`canfar_lab.session.agents` — the six Lab agents: status, install,
  update, remove, setup (MCP + skills), keys (presence only), restore.
* :mod:`canfar_lab.session.snapshots` — save ``$SCRATCH/src`` (and dsh state)
  to ``/arc`` and restore it into a fresh session.
* :mod:`canfar_lab.session.cluster` — Ray cluster start/stop/status and jobs.
"""
