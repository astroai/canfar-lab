"""AstroAI-supported routers (key catalog) and recommended agents.

Routers map a stable AstroAI id to the env key dsh reads. astroai never
writes ``agent-default-model`` — model *choice* lives in dsh Settings. Hand-
declared routes still need a models *catalog* in settings (dsh refuses
incomplete custom providers with UNKNOWN_MODEL).
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from astroai_lab.agent.bundle_path import bundle_root


@dataclass(frozen=True)
class Router:
    """One supported model route (key catalog entry).

    ``id`` is the AstroAI name; ``dsh_route`` is the provider id written
    into dsh settings, which is not always the same string. A route the
    catalog does not ship is "hand-declared" and must carry ``api``,
    ``base_url``, and a ``models`` catalog, or dsh refuses the configuration
    where it is written / fails chats with UNKNOWN_MODEL.
    """

    id: str
    key: str
    notes: str = ""
    dsh_route: str = ""
    api: str = ""
    base_url: str = ""
    models: tuple[str, ...] = ()

    @property
    def provider_id(self) -> str:
        """Provider id to write into ``settings.yaml`` (defaults to ``id``)."""
        return self.dsh_route or self.id

    @property
    def hand_declared(self) -> bool:
        """True when dsh's installed catalog cannot supply this route."""
        return bool(self.api or self.base_url)

    def serviceable(self) -> bool:
        """Whether dsh will accept the provider entry this router produces.

        Hand-declared routes need protocol + endpoint. A models catalog is
        required at write time (live fetch, else yaml fallback) — an empty
        seed alone does not block prepare from attempting the fetch.
        """
        if not self.hand_declared:
            return True
        return bool(self.api and self.base_url)

    def provider_entry(self, *, model_ids: tuple[str, ...] | None = None) -> dict[str, Any]:
        """The ``llm-pi-ai.providers.<id>`` credential reference.

        Catalog routes need only the credential reference; a hand-declared
        route states protocol + endpoint + models catalog. ``model_ids``
        overrides the yaml seed (used after a live ``/models`` fetch).
        Never sets ``agent-default-model``.
        """
        entry: dict[str, Any] = {"apiKeyEnv": self.key}
        if self.hand_declared:
            entry["api"] = self.api
            entry["baseURL"] = self.base_url
            ids = model_ids if model_ids is not None else self.models
            entry["models"] = [{"id": mid} for mid in ids]
        return entry


@dataclass(frozen=True)
class SupportCatalog:
    routers: tuple[Router, ...]
    recommended_agents: tuple[str, ...]
    panel_agents: tuple[str, ...]

    SUPPORT_SCHEMA_VERSION = 2

    @property
    def dsh_keys(self) -> tuple[str, ...]:
        return tuple(r.key for r in self.routers)

    def key_to_route(self) -> dict[str, str]:
        """``KEY → panel route id`` (no model; astroai does not choose models)."""
        return {r.key: r.id for r in self.routers}

    def key_to_dsh_route(self) -> dict[str, str]:
        """``KEY → dsh provider id`` — the settings-side twin."""
        return {r.key: r.provider_id for r in self.routers}

    def unserviceable_routes(self) -> tuple[Router, ...]:
        """Hand-declared routes missing the fields dsh requires."""
        return tuple(r for r in self.routers if not r.serviceable())

    def router_by_id(self, router_id: str) -> Router | None:
        for router in self.routers:
            if router.id == router_id:
                return router
        return None


def support_yaml_path() -> Path:
    return bundle_root() / "support.yaml"


def _parse_model_ids(raw: Any) -> tuple[str, ...]:
    """Normalize support.yaml ``models`` to bare ids."""
    if not raw:
        return ()
    out: list[str] = []
    for item in raw:
        if isinstance(item, str) and item.strip():
            out.append(item.strip())
        elif isinstance(item, dict) and item.get("id"):
            out.append(str(item["id"]).strip())
    return tuple(out)


def fetch_openai_compat_model_ids(base_url: str, *, timeout: float = 8.0) -> tuple[str, ...]:
    """``GET {base_url}/models`` — used to refresh hand-declared catalogs."""
    import json
    import urllib.error
    import urllib.request

    url = base_url.rstrip("/") + "/models"
    # Some gateways 403 bare urllib; curl/browser UAs are accepted.
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "astroai-lab/studio-prepare (+https://github.com/astroai/canfar-lab)",
            "Accept": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 — fixed https hosts
            payload = json.load(resp)
    except (OSError, urllib.error.URLError, TimeoutError, json.JSONDecodeError, ValueError):
        return ()
    data = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(data, list):
        return ()
    return tuple(
        str(item["id"]).strip()
        for item in data
        if isinstance(item, dict) and item.get("id")
    )


@lru_cache
def load_support() -> SupportCatalog:
    from astroai_lab.errors import LabError

    path = support_yaml_path()
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    routers: list[Router] = []
    for entry in raw.get("routers") or []:
        if not isinstance(entry, dict):
            continue
        if "panel_default" in entry or "panel_models" in entry:
            raise LabError(
                f"Router {entry.get('id')} uses removed panel_default/panel_models "
                "(support schema v2: astroai does not preset models).",
                hint="Remove panel_default/panel_models/panel_roles from support.yaml",
            )
        routers.append(
            Router(
                id=str(entry["id"]),
                key=str(entry["key"]),
                notes=str(entry.get("notes") or ""),
                dsh_route=str(entry.get("dsh_route") or ""),
                api=str(entry.get("api") or ""),
                base_url=str(entry.get("base_url") or ""),
                models=_parse_model_ids(entry.get("models")),
            )
        )
    if "panel_roles" in raw:
        raise LabError(
            "support.yaml uses removed panel_roles (support schema v2).",
            hint="Delete panel_roles: astroai does not preset per-role models",
        )
    agents = raw.get("agents") or {}
    recommended = tuple(str(a) for a in (agents.get("recommended") or []))
    panel = tuple(str(a) for a in (agents.get("panel") or []))
    return SupportCatalog(
        routers=tuple(routers),
        recommended_agents=recommended,
        panel_agents=panel,
    )


def brand_logo_path() -> Path | None:
    """Vendored AstroAI logo, if present."""
    data = Path(__file__).resolve().parent.parent / "data" / "brand" / "astroai-logo.png"
    return data if data.is_file() else None


def routers_status(*, keys_present: dict[str, str] | None = None) -> list[dict[str, Any]]:
    """Rows for ``panel routers`` / ``agent routers`` (key presence only)."""
    cat = load_support()
    present = keys_present if keys_present is not None else {}
    rows: list[dict[str, Any]] = []
    for router in cat.routers:
        rows.append(
            {
                "id": router.id,
                "key": router.key,
                "key_present": router.key in present,
                "notes": router.notes,
                "dsh_route": router.provider_id,
                "hand_declared": router.hand_declared,
                "serviceable": router.serviceable(),
            }
        )
    return rows


def clear_support_cache() -> None:
    load_support.cache_clear()
