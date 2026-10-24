"""GitHub-relay sync between the desk workspace and CANFAR sessions.

Design: ``docs/desk-sync.md``. Modules:

* :mod:`canfar_lab.sync.catalog` — repo catalog resolution.
* :mod:`canfar_lab.sync.engine` — publish/absorb/bootstrap git operations.
* :mod:`canfar_lab.sync.bus` — the ``$HOME/.astroai/lab/sync`` mailbox.
* :mod:`canfar_lab.sync.watch` — the session and desk watchers.
"""

from __future__ import annotations

from canfar_lab.sync import bus, catalog, clock, engine, watch

__all__ = ["bus", "catalog", "clock", "engine", "watch"]
