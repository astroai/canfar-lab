"""Keep agent logins, keys and settings readable by their owner only.

``/arc/home`` is a shared filesystem where group ACLs are common, and agent
CLIs create their config dirs with whatever umask the session had. This
module strips group/other access from the places that hold secrets:

* ``~/.canfar/lab`` (shared API keys ``.env``, settings, setup state)
* dsh's home (``~/.dsh``) and its ``.credentials.yaml``
* every Lab agent's config dir, config file and login files (``auth_files``)

It only ever *removes* permission bits, never adds them, touches nothing it
does not own, and never recurses: closing a directory is enough to stop
others reaching what is inside. Container dirs like ``~/.config`` itself are
left alone.
"""

from __future__ import annotations

import os
import stat
from pathlib import Path
from typing import Any

#: Shared parents that hold many apps' data; never chmod these themselves.
_SHARED_PARENTS = (".config", ".local", ".local/share", ".local/state", ".cache")


def _candidates(home: Path) -> list[Path]:
    from canfar_lab.agent.registry_probe import config_state_paths
    from canfar_lab.agent.registry_store import get_registry_agent
    from canfar_lab.core.state_home import lab_home
    from canfar_lab.session import agents as lab
    from canfar_lab.studio_profile import dsh_home

    paths: list[Path] = [lab_home(home), dsh_home(home), dsh_home(home) / ".credentials.yaml"]
    for path in lab_home(home).glob("*"):
        if path.name in (".env", "workspace.json", "config.yaml") or path.suffix == ".token":
            paths.append(path)
    for spec in lab.lab_agents():
        reg = get_registry_agent(spec.id)
        if reg is not None:
            paths.extend(config_state_paths(reg, home))
        paths.extend(lab._auth_path(raw, home) for raw in spec.auth_files)
    return paths


def _skippable(path: Path, home: Path) -> bool:
    try:
        resolved = path.resolve()
    except OSError:
        return True
    home_r = home.resolve()
    return resolved == home_r or resolved in {home_r / rel for rel in _SHARED_PARENTS}


def lock_down(home: Path | None = None, *, dry_run: bool = False) -> dict[str, Any]:
    """Remove group/other access from secret-holding paths. Returns what changed."""
    home = home or Path.home()
    uid = os.getuid()
    changed: list[str] = []
    errors: list[str] = []
    seen: set[Path] = set()
    for path in _candidates(home):
        if path in seen or _skippable(path, home):
            continue
        seen.add(path)
        try:
            st = path.stat()
        except OSError:
            continue  # missing: nothing to protect
        if st.st_uid != uid or not (stat.S_ISDIR(st.st_mode) or stat.S_ISREG(st.st_mode)):
            continue
        if not st.st_mode & 0o077:
            continue
        mode = st.st_mode & 0o700
        try:
            if not dry_run:
                os.chmod(path, mode)
            changed.append(f"{path} {oct(st.st_mode & 0o777)}->{oct(mode)}")
        except OSError as exc:
            errors.append(f"{path}: {exc}")
    return {"ok": not errors, "changed": changed, "errors": errors, "dry_run": dry_run}


def exposed(home: Path | None = None) -> list[str]:
    """Secret-holding paths others can currently read (for the Settings page)."""
    return [line.split(" ", 1)[0] for line in lock_down(home, dry_run=True)["changed"]]
