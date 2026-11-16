#!/usr/bin/env python3
"""Install the opt-in Azure work models without replacing unrelated settings.

Invoked by scripts/install.sh work. Requires PyYAML. Existing files are backed
up beside their originals before modification; credentials stay in the shell.
"""

import json
import shutil
from datetime import UTC, datetime
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
HOME = Path.home()
SOURCE = json.loads((ROOT / "agents/work/models.json").read_text())
PROVIDER = "azure-responses"
AZURE = SOURCE["provider"][PROVIDER]
STAMP = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")


def load(path):
    if not path.exists():
        return {}
    text = path.read_text()
    return (yaml.safe_load(text) if path.suffix in (".yml", ".yaml") else json.loads(text)) or {}


def save(path, data):
    if path.suffix == ".toml":
        text = data
    else:
        text = (
            yaml.safe_dump(data, sort_keys=False)
            if path.suffix in (".yml", ".yaml")
            else json.dumps(data, indent=2) + "\n"
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        shutil.copy2(path, path.with_name(path.name + ".bak-" + STAMP))
    path.write_text(text)
    print(f"configured {path}")


models = []
for model in AZURE["models"].values():
    converted = {
        "id": model["id"],
        "name": model["name"],
        "reasoning": model["reasoning"],
        "input": model["modalities"]["input"],
        "contextWindow": model["limit"]["context"],
        "maxTokens": model["limit"]["output"],
    }
    if "cost" in model:
        converted["cost"] = {
            new: model["cost"][old]
            for old, new in (
                ("input", "input"),
                ("output", "output"),
                ("cache_read", "cacheRead"),
                ("cache_write", "cacheWrite"),
            )
        }
    models.append(converted)

native_provider = {
    "baseUrl": AZURE["options"]["baseURL"],
    "api": "azure-openai-responses",
    "apiKey": "$AZURE_OPENAI_API_KEY",
    "models": models,
}

# Prepare every file before writing, so an unreadable config leaves all intact.
updates = {}
for relative in (".config/opencode/opencode.json", ".config/kilo/kilo.jsonc"):
    path = HOME / relative
    data = load(path)
    data.setdefault("provider", {})[PROVIDER] = AZURE
    # Set defaults only — do not set enabled_providers (that hides Zen/Go/etc.).
    for key in ("model", "small_model"):
        data[key] = SOURCE[key]
    data.pop("enabled_providers", None)
    updates[path] = data

for relative in (".omp/agent/models.yml", ".pi/agent/models.json"):
    path = HOME / relative
    data = load(path)
    data.setdefault("providers", {})[PROVIDER] = dict(native_provider)
    if relative.startswith(".omp/"):
        # OMP supports command-resolved secrets; an absent variable stays absent.
        data["providers"][PROVIDER]["apiKey"] = "!printenv AZURE_OPENAI_API_KEY"
    updates[path] = data

path = HOME / ".omp/agent/config.yml"
data = load(path)
roles = data.setdefault("modelRoles", {})
all_roles = {
    "default",
    "plan",
    "designer",
    "slow",
    "task",
    "advisor",
    "vision",
    "smol",
    "tiny",
    "commit",
}
for role in set(roles) | all_roles:
    roles[role] = SOURCE["small_model" if role in ("smol", "tiny", "commit") else "model"]
# Do not set enabledModels — that would hide Zen/Go/Copilot/etc. in the picker.
data.pop("enabledModels", None)
updates[path] = data

path = HOME / ".pi/agent/settings.json"
data = load(path)
data["defaultProvider"], data["defaultModel"] = SOURCE["model"].split("/", 1)
data.pop("enabledModels", None)
updates[path] = data

# Codex 0.155+ layers <name>.config.toml over its existing user configuration.
# Keep personal defaults untouched; select with codex --profile work[-luna].
for profile, selector in (("work", SOURCE["model"]), ("work-luna", SOURCE["small_model"])):
    deployment = selector.split("/", 1)[1]
    updates[HOME / ".codex" / f"{profile}.config.toml"] = (
        f"model = {json.dumps(deployment)}\n"
        f"model_provider = {json.dumps(PROVIDER)}\n"
        "model_context_window = 1000000\n"
        'model_reasoning_effort = "high"\n\n'
        f"[model_providers.{PROVIDER}]\n"
        'name = "Azure OpenAI Responses (Work)"\n'
        f"base_url = {json.dumps(AZURE['options']['baseURL'])}\n"
        'wire_api = "responses"\n'
        'env_key = "AZURE_OPENAI_API_KEY"\n'
        'env_http_headers = { "api-key" = "AZURE_OPENAI_API_KEY" }\n'
        "requires_openai_auth = false\n"
    )

for path, data in updates.items():
    save(path, data)
