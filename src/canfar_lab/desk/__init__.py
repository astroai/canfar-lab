"""The Desk: browser dsh with a dock of CANFAR actions (docs/desk-sync.md).

Modules:

* :mod:`canfar_lab.desk.proxy` — loopback HTTP+WS proxy, dock injection, token.
* :mod:`canfar_lab.desk.actions` — action handlers behind the token.
* :mod:`canfar_lab.desk.state` — chip state (auth, session, mailbox, dsh parity).
* :mod:`canfar_lab.desk.apps` — desktop editor/terminal/browser launching.
"""

from __future__ import annotations

from canfar_lab.desk import actions, apps, proxy, state

__all__ = ["actions", "apps", "proxy", "state"]
