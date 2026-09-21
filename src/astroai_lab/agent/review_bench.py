"""Review-bench (dsh panel) provisioning + credential plumbing.

Ships the ``~/dsh`` review bench inside the pip package so CANFAR images and
laptops get it without cloning ``~/dsh``. Single source of truth remains
``~/dsh``; ``data/review-bench/`` is a build-time copy
(``scripts/sync-review-bench.sh``).

Layout after ``astroai agent setup``::

    ~/.astroai/lab/review-bench/      # presets/ + skills/ + bin/ + validate.mjs
    ~/.dsh/profiles/web/cordis.patch.yml   # web preset root (idempotent)
    ~/.dsh/profiles/headless/cordis.yml    # empty list (presets are web-only)
"""

from __future__ import annotations

import contextlib
import json
import os
import shutil
from pathlib import Path
from typing import TypedDict

import yaml

from astroai_lab.agent.bundle_path import review_bench_root as _vendored_root
from astroai_lab.agent.support import load_support
from astroai_lab.errors import LabError

DSH_VERSION = "0.1.5-rc.2"


class PanelRouteHealth(TypedDict):
    pinned: str | None
    keys_present: list[str]
    usable: bool


def __getattr__(name: str):
    """Expose ``DSH_KEYS`` / ``_KEY_TO_ROUTE`` from ``support.yaml``."""
    if name == "DSH_KEYS":
        return load_support().dsh_keys
    if name == "_KEY_TO_ROUTE":
        return load_support().key_to_route()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def vendored_review_bench_root() -> Path:
    """Vendored ``data/review-bench`` tree; LabError when sync is missing."""
    try:
        return _vendored_root()
    except FileNotFoundError as exc:
        raise LabError(
            str(exc),
            hint="Run scripts/sync-review-bench.sh (or reinstall astroai-lab)",
        ) from exc


def managed_bench_dir(home: Path | None = None) -> Path:
    return (home or Path.home()) / ".astroai" / "lab" / "review-bench"


def validate_preset(root: Path | None = None) -> tuple[list[str], list[str]]:
    """Python-side preset check (mirrors ``validate.mjs`` without node).

    astroai never presets models: any ``agentOptions.model`` pin is a
    failure (real schema validation lives in ``validate.mjs``).
    """
    root = root or vendored_review_bench_root()
    checks: list[str] = []
    failures: list[str] = []
    preset = root / "presets" / "review-bench" / "agent.cordis.yml"
    skill = root / "skills" / "review-panel" / "SKILL.md"
    for path, label in (
        (preset, "preset"),
        (skill, "skill"),
        (root / "skills" / "review-panel" / "references" / "rubric.md", "rubric"),
        (
            root / "skills" / "review-panel" / "references" / "panel-round1.js",
            "round1 workflow",
        ),
        (
            root / "skills" / "review-panel" / "references" / "brief-template.md",
            "brief template",
        ),
        (
            root / "skills" / "review-panel" / "references" / "report-template.md",
            "report template",
        ),
    ):
        if path.is_file():
            checks.append(f"{label}: {path.name} present")
        else:
            failures.append(f"{label}: missing {path}")
    if not preset.is_file():
        return checks, failures
    try:
        text = preset.read_text(encoding="utf-8")
    except OSError as exc:
        return checks, [f"preset: unreadable ({exc})"]

    # Tolerate the loader's `!!js <expr>` extension tag like install.sh does.
    class _Loader(yaml.SafeLoader):  # type: ignore[valid-type,misc]
        pass

    _Loader.add_multi_constructor("tag:yaml.org,2002:js", lambda _l, _s, _n: "__js__")
    try:
        entries = yaml.load(text, Loader=_Loader)
    except yaml.YAMLError as exc:
        return checks, [f"preset: invalid YAML ({exc})"]
    if not isinstance(entries, list):
        return checks, ["preset: composition is not a top-level list of rows"]
    checks.append("preset: top-level list parses")

    def _rows(items: list) -> list:
        out = []
        for entry in items:
            if not isinstance(entry, dict):
                continue
            out.append(entry)
            config = entry.get("config")
            if isinstance(config, list):
                out.extend(_rows(config))
        return out

    rows = _rows(entries)
    checks.append(f"preset: {len(rows)} rows")
    ids = {r.get("id") for r in rows if isinstance(r.get("id"), str)}
    for want in ("persona", "skill-filesystem", "tool-skill"):
        if want in ids:
            checks.append(f"preset: row {want} present")
        else:
            failures.append(f"preset: row {want} missing")
    lenses = sorted(i for i in ids if i.startswith(("ask-", "ask_")))
    if len(lenses) >= 14:
        checks.append(f"preset: {len(lenses)} persona lenses")
    else:
        failures.append(f"preset: only {len(lenses)} ask_* lenses (want 14)")
    for row in rows:
        config = row.get("config")
        agent_opts = config.get("agentOptions") if isinstance(config, dict) else None
        model = agent_opts.get("model") if isinstance(agent_opts, dict) else None
        if model is not None:
            failures.append(
                f"preset: row {row.get('id')} presets model ({model!r}) — "
                "astroai does not preset models"
            )
    if not any("astroai" in line for line in text.splitlines()):
        failures.append("preset: customSkillDirs does not mention the managed ~/.astroai path")
    else:
        checks.append("preset: customSkillDirs points at the managed bench")
    return checks, failures


def ensure_review_bench(
    home: Path | None = None, *, dry_run: bool = False, force: bool = False
) -> bool:
    """Install the vendored bench to ``~/.astroai/lab/review-bench`` + dsh layers.

    Returns True when files were (or would be, under ``dry_run``) written.
    The web patch layer keeps user edits: a differing installed layer is left
    alone with a warning unless ``force`` (same semantics as ``~/dsh/install.sh``).
    """
    from astroai_lab.agent.setup import install_tree

    home = home or Path.home()
    src = vendored_review_bench_root()
    dst = managed_bench_dir(home)
    wrote = False
    for sub in ("presets", "skills", "bin"):
        if install_tree(src / sub, dst / sub, force=force, dry_run=dry_run):
            wrote = True
    for name in ("validate.mjs", "cordis.bench.patch.yml", "HOWTO.md", "README.md"):
        s, d = src / name, dst / name
        if not s.is_file():
            continue
        if d.is_file() and not force:
            continue
        wrote = True
        if not dry_run:
            d.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(s, d)

    # Legacy shim: ~/dsh remains usable when it matches the vendored copy.
    legacy = home / "dsh" / "presets" / "review-bench"
    link = dst / "presets" / "review-bench"
    if legacy.is_dir() and not dry_run:
        try:
            with contextlib.suppress(OSError):
                identical = _trees_identical(legacy, src / "presets" / "review-bench")
                if identical and (link.is_symlink() or not link.exists()):
                    if link.is_symlink():
                        link.unlink()
                    elif link.is_dir():
                        shutil.rmtree(link)
                    link.symlink_to(legacy, target_is_directory=True)
                    wrote = True
        except OSError:
            pass

    # Web preset root: managed bench. Idempotent; never clobber user edits.
    patch_src = src / "cordis.bench.patch.yml"
    target = home / ".dsh" / "profiles" / "web" / "cordis.patch.yml"
    if patch_src.is_file():
        if target.is_file() and _files_equal(target, patch_src):
            pass
        elif target.is_file() and not force:
            import warnings

            warnings.warn(
                f"Refusing to overwrite differing {target} "
                "(re-run with --force to replace; "
                "legacy ~/dsh/install.sh --force keeps a .bak)"
            )
        else:
            wrote = True
            if not dry_run:
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(patch_src, target)

    headless = home / ".dsh" / "profiles" / "headless" / "cordis.yml"
    if not headless.is_file():
        wrote = True
        if not dry_run:
            headless.parent.mkdir(parents=True, exist_ok=True)
            headless.write_text("[]\n", encoding="utf-8")
    return wrote


def _files_equal(a: Path, b: Path) -> bool:
    try:
        return a.read_bytes() == b.read_bytes()
    except OSError:
        return False


def _trees_identical(a: Path, b: Path) -> bool:
    files_a = sorted(p.relative_to(a) for p in a.rglob("*") if p.is_file())
    files_b = sorted(p.relative_to(b) for p in b.rglob("*") if p.is_file())
    if files_a != files_b:
        return False
    return all(_files_equal(a / rel, b / rel) for rel in files_a)


def _read_dotenv_value(path: Path, name: str) -> str | None:
    if not path.is_file():
        return None
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        if key.strip() == name:
            return val.strip().strip("'\"") or None
    return None


def _auth_json_key(path: Path) -> str | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if isinstance(data, dict):
        node = data.get("opencode-go")
        if isinstance(node, dict) and node.get("key"):
            return str(node["key"]).strip() or None
    return None


def discover_dsh_keys(home: Path | None = None) -> dict[str, str]:
    """Find dsh provider keys: env → shared .env → opencode/Codex auth files."""
    home = home or Path.home()
    from astroai_lab.agent.setup import openrouter_dotenv_path

    dotenv = openrouter_dotenv_path(home)
    xdg = Path(os.environ.get("XDG_DATA_HOME", "") or home / ".local" / "share")
    found: dict[str, str] = {}
    for name in load_support().dsh_keys:
        candidates = [
            os.environ.get(name),
            _read_dotenv_value(dotenv, name),
        ]
        if name == "OPENCODE_API_KEY":
            candidates.append(_auth_json_key(xdg / "opencode" / "auth.json"))
            candidates.append(_auth_json_key(home / ".local" / "share" / "opencode" / "auth.json"))
        for candidate in candidates:
            if candidate and candidate.strip():
                found[name] = candidate.strip()
                break
    return found


def ensure_dsh_dotenv(home: Path | None = None, *, dry_run: bool = False) -> dict[str, str]:
    """Persist discovered dsh keys into the shared ``~/.astroai/lab/.env``."""
    from astroai_lab.agent.setup import _write_dotenv_value, openrouter_dotenv_path

    home = home or Path.home()
    keys = discover_dsh_keys(home)
    if not keys:
        return {}
    dotenv = openrouter_dotenv_path(home)
    for name, value in keys.items():
        if _read_dotenv_value(dotenv, name) != value:
            _write_dotenv_value(dotenv, name, value, dry_run=dry_run)
        if not dry_run:
            os.environ[name] = value
    if not dry_run:
        hook = home / ".astroai" / "lab" / "agent-env.sh"
        text = hook.read_text(encoding="utf-8") if hook.is_file() else ""
        if "_astroai_dotenv" not in text:
            hook.parent.mkdir(parents=True, exist_ok=True)
            hook.write_text(
                "# astroai dsh dotenv\n"
                '_astroai_dotenv="${HOME}/.astroai/lab/.env"\n'
                'if [[ -f "${_astroai_dotenv}" ]]; then\n  set -a\n'
                "  # shellcheck disable=SC1090\n"
                '  source "${_astroai_dotenv}"\n  set +a\nfi\n' + (("\n" + text) if text else ""),
                encoding="utf-8",
            )
    return keys


def read_dsh_pinned_provider(home: Path | None = None) -> str | None:
    """Return ``agent-default-model.provider`` from ``~/.dsh/settings.yaml``, if any."""
    home = home or Path.home()
    settings = home / ".dsh" / "settings.yaml"
    if not settings.is_file():
        return None
    try:
        loaded = yaml.safe_load(settings.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError):
        return None
    if not isinstance(loaded, dict):
        return None
    current = loaded.get("agent-default-model")
    if isinstance(current, dict) and current.get("provider"):
        return str(current["provider"])
    return None


def resolve_panel_route(
    home: Path | None = None,
    *,
    keys: dict[str, str] | None = None,
) -> PanelRouteHealth:
    """Key health for doctor/routers (read-only; never chooses a model).

    ``pinned`` is the user's ``agent-default-model.provider`` if set, reported
    as-is. ``usable`` means at least one provider key is present.
    """
    home = home or Path.home()
    present = keys if keys is not None else discover_dsh_keys(home)
    pinned = read_dsh_pinned_provider(home)
    return PanelRouteHealth(
        pinned=pinned,
        keys_present=sorted(present),
        usable=bool(present),
    )


def unserviceable_keys() -> set[str]:
    """Keys whose router dsh cannot accept as written.

    A hand-declared route (one the installed provider catalog does not ship)
    needs ``api`` and ``base_url``; dsh rejects an incomplete one where it is
    written, which would take the whole ``settings.yaml`` with it.
    """
    return {router.key for router in load_support().unserviceable_routes()}


def ensure_provider_entry(
    home: Path | None = None,
    *,
    route_id: str,
    dry_run: bool = False,
) -> str | None:
    """Ensure one provider credential reference exists; never touches agent-default-model."""
    home = home or Path.home()
    keys = discover_dsh_keys(home)
    catalog = load_support()
    router = catalog.router_by_id(route_id)
    if router is None or router.key not in keys or not router.serviceable():
        return None
    if dry_run:
        return router.provider_id
    settings = home / ".dsh" / "settings.yaml"
    doc: dict = {}
    if settings.is_file():
        try:
            loaded = yaml.safe_load(settings.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                doc = loaded
        except (OSError, yaml.YAMLError):
            doc = {}
    providers = doc.setdefault("llm-pi-ai", {}).setdefault("providers", {})
    if not isinstance(providers, dict):
        providers = doc["llm-pi-ai"]["providers"] = {}
    wanted = router.provider_entry()
    if providers.get(router.provider_id) != wanted:
        providers[router.provider_id] = wanted
        settings.parent.mkdir(parents=True, exist_ok=True)
        backup = settings.with_suffix(settings.suffix + ".pre-astroai.bak")
        if settings.is_file():
            with contextlib.suppress(OSError):
                shutil.copy2(settings, backup)
        settings.write_text(yaml.safe_dump(doc, sort_keys=True), encoding="utf-8")
    return router.provider_id


def ensure_dsh_settings(
    home: Path | None = None,
    *,
    dry_run: bool = False,
) -> list[str]:
    """Ensure provider credential references for every present key.

    Writes ``llm-pi-ai.providers.<id> = {apiKeyEnv[, api, baseURL, models]}``.
    Never reads or writes ``agent-default-model`` — model/provider choice is
    the user's in dsh Settings. Hand-declared routes include a models catalog
    (dsh requires it). Returns the ensured provider ids.
    """
    home = home or Path.home()
    keys = discover_dsh_keys(home)
    if not keys:
        return []
    catalog = load_support()
    ensured: list[str] = []
    for router in catalog.routers:
        if router.key not in keys or not router.serviceable():
            continue
        if dry_run:
            ensured.append(router.provider_id)
            continue
        if ensure_provider_entry(home, route_id=router.id, dry_run=False):
            ensured.append(router.provider_id)
    return ensured


def next_fallback_provider(
    failed: str | None,
    keys: dict[str, str],
) -> str | None:
    """First key-present route that is not ``opencode-go`` (headless-safe).

    ``failed`` is informational: when the first attempt used the user's dsh
    default and hit an opencode-go session error, fall back to the first
    usable non-Go route. Never returns ``opencode-go``.
    """
    catalog = load_support()
    key_to_route = catalog.key_to_route()
    if failed is not None and failed != "opencode-go":
        seen_failed = False
        for key in catalog.dsh_keys:
            if key not in keys:
                continue
            route_id = key_to_route[key]
            if not seen_failed:
                if route_id == failed:
                    seen_failed = True
                continue
            if route_id == "opencode-go":
                continue
            return route_id
        return None
    for key in catalog.dsh_keys:
        if key not in keys:
            continue
        route_id = key_to_route[key]
        if route_id == "opencode-go":
            continue
        return route_id
    return None


def is_opencode_go_headless_error(message: str) -> bool:
    low = message.lower().replace("_", "").replace("-", "")
    return (
        "missingsessionid" in low
        or "xopencodesession" in low
        or ("console go" in low and ("400" in low or "401" in low or "403" in low))
        or ("opencode" in low and "session" in low and ("required" in low or "missing" in low))
        or ("zen" in low and "session" in low)
    )


def panel_role_pins(router_id: str) -> dict[str, str]:
    """Deprecated: astroai no longer presets per-role models (returns {})."""
    return {}


def extract_preset_role_models(root: Path | None = None) -> dict[str, str]:
    """Deprecated: presets must not pin models (returns {})."""
    return {}
