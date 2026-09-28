"""Model API keys shared by agent CLIs, marimo, and dsh.

Two stores hold the same secrets:

* ``~/.astroai/lab/.env`` — sourced by every login shell (CLI agents, marimo).
* ``$DSH_HOME/.credentials.yaml`` ``refs`` — hot-reloaded by a running dsh.

``set_key``/``unset_key`` write both; ``sync_keys`` reconciles them at Studio
prepare (newest file wins on conflict). Values never leave this module: every
public return value reports presence only.
"""

from __future__ import annotations

import contextlib
import os
import re
import tempfile
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import yaml

from canfar_lab.agent.setup import _read_dotenv_value, openrouter_dotenv_path
from canfar_lab.agent.support import load_support
from canfar_lab.errors import LabError

KEY_NAME_RE = re.compile(r"^[A-Z][A-Z0-9_]{1,63}$")
# The dotenv is sourced by bash: refuse anything a shell would reinterpret.
KEY_VALUE_RE = re.compile(r"^[A-Za-z0-9._~+/=:@-]{8,512}$")
LOCK_WAIT_SEC = 10.0
STALE_LOCK_SEC = 30.0


def credentials_path(home: Path) -> Path:
    from canfar_lab.studio_profile import dsh_home

    return dsh_home(home) / ".credentials.yaml"


def catalog() -> list[dict[str, Any]]:
    """Every key the UI can manage, with the agents that read it."""
    from canfar_lab.agent.registry import load_registry

    support = load_support()
    used_by: dict[str, list[str]] = {}
    labels: dict[str, str] = {}
    for agent in load_registry():
        config = agent.get("config") or {}
        name = str(agent.get("name") or agent["id"])
        agent_keys = [config.get("provider_key"), *(config.get("provider_keys") or [])]
        for key in dict.fromkeys(str(k) for k in agent_keys if k):
            used_by.setdefault(key, []).append(name)
            labels.setdefault(key, name)

    rows: list[dict[str, Any]] = []
    for shared in support.shared_keys:
        rows.append(
            {
                "key": shared.key,
                "label": shared.label,
                "signup_url": shared.signup_url,
                "notes": shared.notes,
                "dsh_route": None,
                "used_by": sorted(used_by.get(shared.key, [])),
            }
        )
    for router in support.routers:
        rows.append(
            {
                "key": router.key,
                "label": router.label or router.id,
                "signup_url": router.signup_url,
                "notes": router.notes,
                "dsh_route": router.provider_id,
                "used_by": ["AstroAI Assistant", *sorted(used_by.get(router.key, []))],
            }
        )
    known = {row["key"] for row in rows}
    secrets = {k for k in used_by if k.endswith(("_KEY", "_TOKEN"))}
    for key in sorted(secrets - known):
        rows.append(
            {
                "key": key,
                "label": labels[key],
                "signup_url": "",
                "notes": "",
                "dsh_route": None,
                "used_by": sorted(used_by[key]),
            }
        )
    return rows


def _dsh_keys() -> set[str]:
    return set(load_support().dsh_keys)


def _validate(name: str, value: str | None = None) -> None:
    known = {row["key"] for row in catalog()}
    if not KEY_NAME_RE.match(name) or name not in known:
        raise LabError(
            f"Unknown key name {name!r}.",
            hint="See `canfar-lab agent keys list` for the supported names.",
        )
    if value is not None and not KEY_VALUE_RE.match(value):
        raise LabError(
            "That does not look like an API key (8-512 characters, no spaces or quotes).",
            hint="Paste the key exactly as the provider shows it.",
        )


def _write_private(path: Path, text: str) -> None:
    """Atomic replace; the temp file is born 0600 so the secret is never wider."""
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp")
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


@contextlib.contextmanager
def _file_lock(path: Path, *, wait: float = LOCK_WAIT_SEC) -> Iterator[None]:
    """dsh's writer lock: a ``wx``-created ``<file>.lock`` holding our pid."""
    lock = path.with_name(path.name + ".lock")
    lock.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    deadline = time.monotonic() + wait
    delay = 0.02
    while True:
        try:
            fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            with contextlib.suppress(OSError, ValueError):
                age = time.time() - lock.stat().st_mtime
                raw = lock.read_text(encoding="utf-8").strip()
                # /arc/home is shared across pods: a pid we cannot see may be
                # alive elsewhere, so also require the lock to be old.
                if age > STALE_LOCK_SEC and (not raw or not _pid_alive(int(raw))):
                    lock.unlink(missing_ok=True)
                    continue
            if time.monotonic() >= deadline:
                raise LabError(
                    f"Timed out waiting for {lock}.",
                    hint="Another writer holds it; retry, or delete the lock if stale.",
                ) from None
            time.sleep(delay)
            delay = min(delay * 2, 0.5)
            continue
        with os.fdopen(fd, "w") as fh:
            fh.write(f"{os.getpid()}\n")
        break
    try:
        yield
    finally:
        lock.unlink(missing_ok=True)


def _read_credentials(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        doc = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise LabError(
            f"Cannot parse {path}: {exc}",
            hint="Fix or remove the file; dsh refuses to start with it broken too.",
        ) from exc
    if doc is None:
        return {}
    if not isinstance(doc, dict) or doc.get("version", 1) != 1:
        raise LabError(
            f"{path} is not a version-1 dsh credentials document.",
            hint="Upgrade dsh or let it migrate the file first.",
        )
    return doc


def _credential_refs(home: Path) -> dict[str, str]:
    refs = _read_credentials(credentials_path(home)).get("refs") or {}
    return {str(k): str(v) for k, v in refs.items() if v} if isinstance(refs, dict) else {}


def _write_credential_ref(home: Path, name: str, value: str | None) -> bool:
    path = credentials_path(home)
    with _file_lock(path):
        doc = _read_credentials(path)
        refs = doc.get("refs") if isinstance(doc.get("refs"), dict) else {}
        if refs.get(name) == value or (value is None and name not in refs):
            return False
        if value is None:
            refs.pop(name, None)
        else:
            refs[name] = value
        out: dict[str, Any] = {"version": 1}
        if refs:
            out["refs"] = refs
        if doc.get("records"):
            out["records"] = doc["records"]
        _write_private(path, yaml.safe_dump(out, sort_keys=False, default_flow_style=False))
    return True


def _write_dotenv(home: Path, name: str, value: str | None) -> bool:
    path = openrouter_dotenv_path(home)
    lines = path.read_text(encoding="utf-8").splitlines() if path.is_file() else []
    out: list[str] = []
    found = False
    for raw in lines:
        stripped = raw.strip()
        key = stripped.partition("=")[0].strip()
        if not stripped.startswith("#") and "=" in stripped and key == name:
            if value is not None and not found:
                out.append(f"{name}={value}")
            found = True
            continue
        out.append(raw)
    if value is not None and not found:
        if out and out[-1].strip():
            out.append("")
        out.append(f"{name}={value}")
    if out == lines:
        return False
    _write_private(path, "\n".join(out) + "\n" if out else "")
    return True


def ensure_shell_hook(home: Path) -> None:
    """Make interactive shells that read ``.bashrc`` source the shared dotenv too."""
    from canfar_lab.agent.setup import _OPENROUTER_DOTENV_MARKER
    from canfar_lab.utils.json_utils import atomic_write_text

    hook = home / ".astroai" / "lab" / "agent-env.sh"
    text = hook.read_text(encoding="utf-8") if hook.is_file() else ""
    if "_astroai_dotenv" in text:
        return
    block = (
        f"{_OPENROUTER_DOTENV_MARKER}\n"
        '_astroai_dotenv="${HOME}/.astroai/lab/.env"\n'
        'if [[ -f "${_astroai_dotenv}" ]]; then\n'
        "  set -a\n"
        "  # shellcheck disable=SC1090\n"
        '  source "${_astroai_dotenv}"\n'
        "  set +a\n"
        "fi\n"
    )
    atomic_write_text(hook, block + (("\n" + text) if text else ""))


def status(home: Path | None = None) -> list[dict[str, Any]]:
    """Catalog rows plus where each key is set (never the value)."""
    home = home or Path.home()
    dotenv = openrouter_dotenv_path(home)
    try:
        refs = _credential_refs(home)
    except LabError:
        refs = {}
    rows = []
    for row in catalog():
        name = row["key"]
        sources = []
        if _read_dotenv_value(dotenv, name):
            sources.append("studio")
        if name in refs:
            sources.append("assistant")
        if os.environ.get(name, "").strip():
            sources.append("environment")
        rows.append({**row, "present": bool(sources), "sources": sources})
    return rows


def set_key(home: Path | None, name: str, value: str) -> dict[str, Any]:
    home = home or Path.home()
    value = value.strip()
    _validate(name, value)
    changed = _write_dotenv(home, name, value)
    if name in _dsh_keys():
        changed = _write_credential_ref(home, name, value) or changed
    ensure_shell_hook(home)
    return {"key": name, "present": True, "changed": changed}


def unset_key(home: Path | None, name: str) -> dict[str, Any]:
    home = home or Path.home()
    _validate(name)
    changed = _write_dotenv(home, name, None)
    if name in _dsh_keys():
        changed = _write_credential_ref(home, name, None) or changed
    return {"key": name, "present": False, "changed": changed}


def sync_keys(home: Path | None = None) -> list[str]:
    """Reconcile dsh-route keys between the shared dotenv and dsh credentials.

    A key present in one store only is copied to the other; a key set to
    different values in both takes the value from the more recently modified
    file. Returns the key names that changed.
    """
    home = home or Path.home()
    dotenv = openrouter_dotenv_path(home)
    creds = credentials_path(home)
    refs = _credential_refs(home)
    dotenv_newer = dotenv.is_file() and (
        not creds.is_file() or dotenv.stat().st_mtime >= creds.stat().st_mtime
    )
    changed: list[str] = []
    for name in sorted(_dsh_keys()):
        env_val = _read_dotenv_value(dotenv, name)
        ref_val = refs.get(name)
        if env_val == ref_val or not (env_val or ref_val):
            continue
        if env_val and (not ref_val or dotenv_newer):
            if KEY_VALUE_RE.match(env_val):
                _write_credential_ref(home, name, env_val)
                changed.append(name)
        elif ref_val and KEY_VALUE_RE.match(ref_val):
            _write_dotenv(home, name, ref_val)
            changed.append(name)
    return changed
