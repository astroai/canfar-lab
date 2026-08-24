"""`agent config <id>` — show/edit a registered agent's config file (Phase 2).

Format-aware editing driven by the registry ``config.format`` field:

  json / jsonc / json5  — comment-tolerant parse for validation (JSON5-aware);
                          edits are *textual targeted replaces* so comments and
                          trailing commas survive (kilo.jsonc, openclaw.json).
  yaml                 — parse → merge → safe_dump (comments are lost; the
                          hermes config.yaml round-trips cleanly otherwise).
  toml                 — line-based scalar edits (`key = value` top-level or
                          `[table].key`); complex values raise with a hint.
  markdown             — read-only (e.g. cline notes).

Dotted keys (``model.provider``) walk nested mappings. ``agent config <id>``
shows the file; ``--key a.b`` prints one value; ``key=value`` pairs write a
validated edit; ``--unset key`` removes one.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import yaml

from astroai_lab.errors import LabError
from astroai_lab.utils.json_utils import parse_jsonc


def _agent_and_config(
    agent_id: str, home: Path | None
) -> tuple[dict[str, Any], dict[str, Any], Path]:
    """Resolve (registry agent, config block, config path) for an agent id."""
    from astroai_lab.agent.registry import get_registry_agent

    agent = get_registry_agent(agent_id)
    if agent is None:
        raise LabError(f"Unknown agent: {agent_id}", hint="astroai agent list")
    config = agent.get("config") or {}
    path = config.get("path")
    if not path:
        raise LabError(f"Agent {agent_id} has no declared config file", hint="registry YAML")
    home = home or Path.home()
    return agent, config, _resolve_path(str(path), home)


def _resolve_path(path: str, home: Path) -> Path:
    if path == "~":
        return home
    if path.startswith("~/"):
        return home / path[2:]
    p = Path(path).expanduser()
    return home / p if not p.is_absolute() else p


def agent_config_path(agent_id: str, *, home: Path | None = None) -> Path:
    """Resolved config file path for a registered agent."""
    _, _, path = _agent_and_config(agent_id, home)
    return path


def config_format(agent_id: str, *, home: Path | None = None) -> str:
    """Declared config format for a registered agent (default json)."""
    _, config, _ = _agent_and_config(agent_id, home)
    return str(config.get("format", "json"))


def _parse_config(text: str, fmt: str, agent_id: str, path: Path) -> dict[str, Any]:
    """Parse by format; raise LabError on unparseable files (never write through)."""
    if fmt in ("json", "jsonc", "json5"):
        try:
            data = parse_jsonc(text)
        except (ValueError, TypeError) as exc:
            raise LabError(f"Cannot parse {agent_id} config ({path}): {exc}") from exc
    elif fmt == "yaml":
        try:
            data = yaml.safe_load(text)
        except yaml.YAMLError as exc:
            raise LabError(f"Cannot parse {agent_id} config ({path}): {exc}") from exc
    elif fmt == "toml":
        from astroai_lab.utils.toml_compat import tomllib

        try:
            data = tomllib.loads(text)
        except Exception as exc:  # noqa: BLE001 — decode error types differ
            raise LabError(f"Cannot parse {agent_id} config ({path}): {exc}") from exc
    elif fmt == "markdown":
        raise LabError(f"{agent_id} config is markdown (read-only)")
    else:
        raise LabError(f"Unsupported config format {fmt!r} for {agent_id}")
    if data is None:
        data = {}
    if not isinstance(data, dict):
        raise LabError(f"{agent_id} config root must be a mapping, got {type(data).__name__}")
    return data


def validate_config_text(agent_id: str, text: str, *, home: Path | None = None) -> dict[str, Any]:
    """Parse the agent's config text by declared format; LabError when broken.

    Shared by `agent verify --fix <id>` (syntax check before a reset) and the
    edit validation path in ``edit_agent_config``.
    """
    _, config, path = _agent_and_config(agent_id, home)
    fmt = str(config.get("format", "json"))
    return _parse_config(text, fmt, agent_id, path)


def read_agent_config(agent_id: str, *, home: Path | None = None) -> tuple[Path, dict[str, Any]]:
    """Parse the agent's config file by format; LabError when missing/broken."""
    _, config, path = _agent_and_config(agent_id, home)
    fmt = str(config.get("format", "json"))
    if not path.is_file():
        raise LabError(
            f"{agent_id} config not found: {path}",
            hint=f"astroai agent setup {agent_id}",
        )
    data = _parse_config(path.read_text(encoding="utf-8"), fmt, agent_id, path)
    return path, data


def get_config_value(agent_id: str, key: str, *, home: Path | None = None) -> tuple[Any, bool]:
    """Dotted-path lookup into the parsed config; returns (value, found)."""
    _, data = read_agent_config(agent_id, home=home)
    cur: Any = data
    for part in key.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return None, False
        cur = cur[part]
    return cur, True


def parse_value(raw: str) -> Any:
    """Parse a `key=value` argument: JSON literal when valid, else a string."""
    try:
        return json.loads(raw)
    except (ValueError, TypeError):
        return raw


def fmt_value(value: Any) -> str:
    """Human-readable value for `agent config --key` output."""
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False)


# ---------------------------------------------------------------------------
# Edits
# ---------------------------------------------------------------------------


def edit_agent_config(
    agent_id: str,
    *,
    set_items: dict[str, Any] | None = None,
    unsets: list[str] | None = None,
    home: Path | None = None,
    dry_run: bool = False,
) -> list[dict[str, str]]:
    """Apply ``key=value`` sets and/or ``--unset`` keys, format-aware.

    Returns action rows ``{key, status, detail}`` for JSON. Raises LabError
    when the file is missing, unparseable, or the format is read-only.
    """
    set_items = set_items or {}
    unsets = list(unsets or [])
    if not set_items and not unsets:
        return []
    _, config, path = _agent_and_config(agent_id, home)
    fmt = str(config.get("format", "json"))
    if fmt == "markdown":
        raise LabError(f"{agent_id} config is markdown (read-only)")
    if not path.is_file():
        raise LabError(
            f"{agent_id} config not found: {path}",
            hint=f"astroai agent setup {agent_id}",
        )
    text = path.read_text(encoding="utf-8")
    # Validate first — never write through a broken file.
    validate_config_text(agent_id, text, home=home)

    if dry_run:
        actions = [
            {"key": key, "status": "would_set", "detail": fmt_value(value)}
            for key, value in set_items.items()
        ]
        actions += [{"key": key, "status": "would_unset", "detail": ""} for key in unsets]
        return actions

    new_text = _apply_edits(text, fmt, set_items, unsets, agent_id)
    if new_text != text:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(new_text, encoding="utf-8")
    actions = [
        {"key": key, "status": "set", "detail": fmt_value(value)}
        for key, value in set_items.items()
    ]
    actions += [{"key": key, "status": "unset", "detail": ""} for key in unsets]
    return actions


def _apply_edits(
    text: str,
    fmt: str,
    set_items: dict[str, Any],
    unsets: list[str],
    agent_id: str,
) -> str:
    if fmt in ("json", "jsonc", "json5"):
        return _edit_json_family(text, set_items, unsets)
    if fmt == "yaml":
        return _edit_yaml(text, set_items, unsets, agent_id)
    if fmt == "toml":
        return _edit_toml(text, set_items, unsets)
    raise LabError(f"Unsupported config format {fmt!r} for {agent_id}")


# --- JSON / JSONC / JSON5 ---------------------------------------------------


def _edit_json_family(text: str, set_items: dict[str, Any], unsets: list[str]) -> str:
    out = text
    for key in unsets:
        out = _remove_jsonc_key(out, key)
    for key, value in set_items.items():
        out = _set_jsonc_key(out, key, value)
    return out


def _match_brace(text: str, open_idx: int) -> int | None:
    """Index of the closing brace matching the `{` at open_idx (string-aware)."""
    depth = 0
    in_str = False
    i = open_idx
    n = len(text)
    while i < n:
        c = text[i]
        if in_str:
            if c == "\\":
                i += 2
                continue
            if c == '"':
                in_str = False
            i += 1
            continue
        if c == '"':
            in_str = True
        elif c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return i
        i += 1
    return None


def _value_end(text: str, start: int) -> int:
    """Index just past the JSON value starting at start (string/object/array-aware)."""
    i = start
    n = len(text)
    while i < n and text[i] in " \t\r\n":
        i += 1
    if i >= n:
        return n
    if text[i] == '"':
        i += 1
        while i < n:
            if text[i] == "\\":
                i += 2
                continue
            if text[i] == '"':
                return i + 1
            i += 1
        return n
    depth = 0
    in_str = False
    while i < n:
        c = text[i]
        if in_str:
            if c == "\\":
                i += 2
                continue
            if c == '"':
                in_str = False
            i += 1
            continue
        if c == '"':
            in_str = True
            i += 1
            continue
        if c in "[{":
            depth += 1
            i += 1
            continue
        if c in "]}":
            if depth == 0:
                return i
            depth -= 1
            if depth == 0:
                return i + 1
            i += 1
            continue
        if c == "," and depth == 0:
            return i
        i += 1
    return n


def _root_span(text: str) -> tuple[int | None, int | None]:
    """Span (open `{` idx, close `}` idx) of the root JSON object.

    Skips strings and `//` / `/* */` comments so a `{` inside a comment or
    string is never mistaken for the root."""
    in_str = False
    i = 0
    n = len(text)
    while i < n:
        c = text[i]
        if in_str:
            if c == "\\":
                i += 2
                continue
            if c == '"':
                in_str = False
            i += 1
            continue
        if c == '"':
            in_str = True
            i += 1
            continue
        if c == "/" and i + 1 < n and text[i + 1] == "/":
            j = text.find("\n", i + 2)
            i = n if j == -1 else j + 1
            continue
        if c == "/" and i + 1 < n and text[i + 1] == "*":
            j = text.find("*/", i + 2)
            i = n if j == -1 else j + 2
            continue
        if c == "{":
            close = _match_brace(text, i)
            if close is not None:
                return i, close
        i += 1
    return None, None


def _find_parent_span(text: str, parts: list[str]) -> tuple[int | None, int | None]:
    """Span (open `{` idx, close `}` idx) of the object that should contain
    the leaf of a dotted path.

    A single-part path resolves to the ROOT object span. Multi-part paths
    descend from the root through each parent object; when the first parent
    is missing, (None, None) is returned so callers insert the whole nested
    structure, and when a deeper parent is missing the deepest found span is
    returned (leaf inserted there).
    """
    root_open, root_close = _root_span(text)
    if root_open is None or root_close is None:
        return None, None
    open_idx, close_idx = root_open, root_close
    for part in parts[:-1]:
        inner = text[open_idx + 1 : close_idx]
        m = re.search(r'"' + re.escape(part) + r'"\s*:\s*\{', inner)
        if not m:
            if part == parts[0]:
                return None, None  # root segment missing → whole-nested insert
            return open_idx, close_idx  # deeper parent missing → leaf insert here
        open_idx = open_idx + 1 + m.end() - 1
        close_idx = _match_brace(text, open_idx)
        if close_idx is None:
            return None, None
    return open_idx, close_idx


def _set_jsonc_key(text: str, dotted: str, value: Any) -> str:
    parts = dotted.split(".")
    leaf = parts[-1]
    open_idx, close_idx = _find_parent_span(text, parts)
    entry = json.dumps({leaf: value}, ensure_ascii=False)[1:-1]  # '"leaf": <value>'

    if open_idx is None:
        # Root segment missing — insert the whole dotted structure at top level.
        nested: dict[str, Any] = {}
        _set_path(nested, dotted, value)
        return _insert_before_last_brace(text, json.dumps(nested, ensure_ascii=False)[1:-1])

    inner = text[open_idx + 1 : close_idx]
    leaf_m = re.search(r'"' + re.escape(leaf) + r'"\s*:', inner)
    if leaf_m:
        # Keep the whitespace between `:` and the old value (text[:val_start]
        # includes it) and replace from the value start onward.
        val_start = open_idx + 1 + leaf_m.end()
        while val_start < len(text) and text[val_start] in " \t\r\n":
            val_start += 1
        val_end = _value_end(text, val_start)
        return text[:val_start] + json.dumps(value, ensure_ascii=False) + text[val_end:]

    # Leaf missing — insert before the parent object's closing brace.
    body = inner.strip()
    if body:
        body += "," if not body.endswith(",") else ""
        new_inner = body + "\n  " + entry
    else:
        new_inner = entry
    return text[: open_idx + 1] + "\n  " + new_inner + "\n" + text[close_idx:]


def _insert_before_last_brace(text: str, entry: str) -> str:
    _, close_idx = _root_span(text)
    if close_idx is None:
        raise LabError("config has no object to extend")
    prefix = text[:close_idx]
    body = prefix.rstrip()
    if body.endswith("{"):
        return prefix[: len(body)] + "\n  " + entry + "\n" + text[close_idx:]
    if body.endswith(","):
        return prefix[: len(body)] + "\n  " + entry + "\n" + text[close_idx:]
    return prefix[: len(body)] + ",\n  " + entry + "\n" + text[close_idx:]


def _remove_jsonc_key(text: str, dotted: str) -> str:
    parts = dotted.split(".")
    leaf = parts[-1]
    open_idx, close_idx = _find_parent_span(text, parts)
    if open_idx is None:
        return text
    inner = text[open_idx + 1 : close_idx]
    leaf_m = re.search(r'"' + re.escape(leaf) + r'"\s*:', inner)
    if not leaf_m:
        return text
    key_start = open_idx + 1 + leaf_m.start()
    val_start = open_idx + 1 + leaf_m.end()
    val_end = _value_end(text, val_start)
    # Consume a trailing comma + whitespace when the entry is followed by more.
    j = val_end
    while j < len(text) and text[j] in " \t\r\n":
        j += 1
    if j < len(text) and text[j] == ",":
        return text[:key_start] + text[j + 1 :]
    # Last entry in the object — also drop the previous entry's dangling comma.
    before = text[:key_start]
    stripped = before.rstrip()
    if stripped.endswith(","):
        return stripped[:-1] + text[j:]
    return text[:key_start] + text[j:]


# --- YAML -------------------------------------------------------------------


def _set_path(data: dict[str, Any], dotted: str, value: Any) -> None:
    parts = dotted.split(".")
    cur: dict[str, Any] = data
    for part in parts[:-1]:
        nxt = cur.get(part)
        if not isinstance(nxt, dict):
            nxt = {}
            cur[part] = nxt
        cur = nxt
    cur[parts[-1]] = value


def _delete_path(data: dict[str, Any], dotted: str) -> None:
    parts = dotted.split(".")
    cur: Any = data
    for part in parts[:-1]:
        if not isinstance(cur, dict) or part not in cur:
            return
        cur = cur[part]
    if isinstance(cur, dict):
        cur.pop(parts[-1], None)


def _edit_yaml(
    text: str,
    set_items: dict[str, Any],
    unsets: list[str],
    agent_id: str,
) -> str:
    data = yaml.safe_load(text)
    if data is None:
        data = {}
    if not isinstance(data, dict):
        raise LabError(f"{agent_id} config root must be a mapping")
    for key in unsets:
        _delete_path(data, key)
    for key, value in set_items.items():
        _set_path(data, key, value)
    return yaml.safe_dump(data, sort_keys=False, default_flow_style=False)


# --- TOML -------------------------------------------------------------------


def _toml_scalar(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, str):
        return json.dumps(value)
    raise LabError("TOML edits support scalar values only (str/int/float/bool)")


def _toml_set(lines: list[str], dotted: str, value: Any) -> list[str]:
    parts = dotted.split(".")
    scalar = _toml_scalar(value)
    if len(parts) == 1:
        key = parts[0]
        pattern = re.compile(rf"^\s*{re.escape(key)}\s*=")
        for i, line in enumerate(lines):
            if pattern.match(line):
                lines[i] = f"{key} = {scalar}"
                return lines
        return lines + [f"{key} = {scalar}"]
    table = ".".join(parts[:-1])
    key = parts[-1]
    # tolerate an inline comment on the table header: `[chat] # note`
    table_re = re.compile(rf"^\s*\[{re.escape(table)}\]\s*(#.*)?$")
    key_re = re.compile(rf"^\s*{re.escape(key)}\s*=")
    for i, line in enumerate(lines):
        if not table_re.match(line):
            continue
        j = i + 1
        while j < len(lines) and not re.match(r"^\s*\[", lines[j]):
            if key_re.match(lines[j]):
                lines[j] = f"{key} = {scalar}"
                return lines
            j += 1
        lines.insert(i + 1, f"{key} = {scalar}")
        return lines
    return lines + [f"[{table}]", f"{key} = {scalar}"]


def _toml_remove(lines: list[str], dotted: str) -> list[str]:
    parts = dotted.split(".")
    if len(parts) == 1:
        key = parts[0]
        pattern = re.compile(rf"^\s*{re.escape(key)}\s*=")
        return [line for line in lines if not pattern.match(line)]
    table = ".".join(parts[:-1])
    key = parts[-1]
    # tolerate an inline comment on the table header: `[chat] # note`
    table_re = re.compile(rf"^\s*\[{re.escape(table)}\]\s*(#.*)?$")
    key_re = re.compile(rf"^\s*{re.escape(key)}\s*=")
    out: list[str] = []
    in_table = False
    for line in lines:
        if re.match(r"^\s*\[", line):
            in_table = bool(table_re.match(line))
        if in_table and key_re.match(line):
            continue
        out.append(line)
    return out


def _edit_toml(text: str, set_items: dict[str, Any], unsets: list[str]) -> str:
    lines = text.splitlines()
    out_lines = list(lines)
    for key in unsets:
        out_lines = _toml_remove(out_lines, key)
    for key, value in set_items.items():
        out_lines = _toml_set(out_lines, key, value)
    return "\n".join(out_lines) + ("\n" if out_lines else "")
