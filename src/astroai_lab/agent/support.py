"""AstroAI-supported routers, panel models, and recommended agents."""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from astroai_lab.agent.bundle_path import bundle_root


@dataclass(frozen=True)
class Router:
    """One supported model route.

    ``id`` is the AstroAI/panel name (what ``panel_roles`` is keyed by);
    ``dsh_route`` is the *provider id written into dsh settings*, which is not
    always the same string — dsh resolves provider routes from its installed
    catalog (``openai``, ``anthropic``, ``google``), so ``openai-official`` has
    to be written as ``openai``. A route the catalog does not ship is
    "hand-declared" and must carry ``api``, ``base_url`` and a non-empty
    ``panel_models`` list, or dsh refuses the configuration where it is written.
    """

    id: str
    key: str
    panel_default: str
    panel_models: tuple[str, ...]
    notes: str = ""
    dsh_route: str = ""
    api: str = ""
    base_url: str = ""

    @property
    def provider_id(self) -> str:
        """Provider id to write into ``settings.yaml`` (defaults to ``id``)."""
        return self.dsh_route or self.id

    @property
    def hand_declared(self) -> bool:
        """True when dsh's installed catalog cannot supply this route."""
        return bool(self.api or self.base_url)

    def serviceable(self) -> bool:
        """Whether dsh will accept the provider entry this router produces."""
        if not self.hand_declared:
            return True
        return bool(self.api and self.base_url and self.panel_models)

    def provider_entry(self) -> dict[str, Any]:
        """The ``llm-pi-ai.providers.<id>`` entry for this router.

        A catalog route needs only its credential reference; a hand-declared
        route states the protocol, endpoint and model list its catalog entry
        would otherwise supply.
        """
        entry: dict[str, Any] = {"apiKeyEnv": self.key}
        if self.hand_declared:
            entry["api"] = self.api
            entry["baseURL"] = self.base_url
            entry["models"] = [{"id": model} for model in self.panel_models]
        return entry


@dataclass(frozen=True)
class SupportCatalog:
    routers: tuple[Router, ...]
    panel_roles: dict[str, dict[str, str]]
    recommended_agents: tuple[str, ...]
    panel_agents: tuple[str, ...]

    @property
    def dsh_keys(self) -> tuple[str, ...]:
        return tuple(r.key for r in self.routers)

    def key_to_route(self) -> dict[str, tuple[str, str]]:
        """``KEY → (panel route id, default model)``."""
        return {r.key: (r.id, r.panel_default) for r in self.routers}

    def key_to_dsh_route(self) -> dict[str, tuple[str, str]]:
        """``KEY → (dsh provider id, default model)`` — the settings-side twin."""
        return {r.key: (r.provider_id, r.panel_default) for r in self.routers}

    def unserviceable_routes(self) -> tuple[Router, ...]:
        """Hand-declared routes missing the fields dsh requires."""
        return tuple(r for r in self.routers if not r.serviceable())

    def router_by_id(self, router_id: str) -> Router | None:
        for router in self.routers:
            if router.id == router_id:
                return router
        return None

    def role_model(self, role: str, router_id: str) -> str | None:
        row = self.panel_roles.get(role) or {}
        if router_id in row:
            return row[router_id]
        router = self.router_by_id(router_id)
        return router.panel_default if router else None


def support_yaml_path() -> Path:
    return bundle_root() / "support.yaml"


@lru_cache
def load_support() -> SupportCatalog:
    path = support_yaml_path()
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    routers: list[Router] = []
    for entry in raw.get("routers") or []:
        if not isinstance(entry, dict):
            continue
        routers.append(
            Router(
                id=str(entry["id"]),
                key=str(entry["key"]),
                panel_default=str(entry["panel_default"]),
                panel_models=tuple(str(m) for m in (entry.get("panel_models") or [])),
                notes=str(entry.get("notes") or ""),
                dsh_route=str(entry.get("dsh_route") or ""),
                api=str(entry.get("api") or ""),
                base_url=str(entry.get("base_url") or ""),
            )
        )
    roles_raw = raw.get("panel_roles") or {}
    panel_roles: dict[str, dict[str, str]] = {}
    if isinstance(roles_raw, dict):
        for role, mapping in roles_raw.items():
            if isinstance(mapping, dict):
                panel_roles[str(role)] = {str(k): str(v) for k, v in mapping.items()}
    agents = raw.get("agents") or {}
    recommended = tuple(str(a) for a in (agents.get("recommended") or []))
    panel = tuple(str(a) for a in (agents.get("panel") or []))
    return SupportCatalog(
        routers=tuple(routers),
        panel_roles=panel_roles,
        recommended_agents=recommended,
        panel_agents=panel,
    )


def brand_logo_path() -> Path | None:
    """Vendored AstroAI logo, if present."""
    data = Path(__file__).resolve().parent.parent / "data" / "brand" / "astroai-logo.png"
    return data if data.is_file() else None


def routers_status(*, keys_present: dict[str, str] | None = None) -> list[dict[str, Any]]:
    """Rows for ``panel routers`` / ``agent routers``."""
    cat = load_support()
    present = keys_present if keys_present is not None else {}
    rows: list[dict[str, Any]] = []
    for router in cat.routers:
        rows.append(
            {
                "id": router.id,
                "key": router.key,
                "key_present": router.key in present,
                "panel_default": router.panel_default,
                "panel_models": list(router.panel_models),
                "notes": router.notes,
                "dsh_route": router.provider_id,
                "hand_declared": router.hand_declared,
                "serviceable": router.serviceable(),
            }
        )
    return rows


def clear_support_cache() -> None:
    load_support.cache_clear()
