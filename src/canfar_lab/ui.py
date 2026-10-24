from __future__ import annotations

import json
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from rich.progress import Progress, SpinnerColumn, TextColumn
from rich.table import Table

from canfar_lab.utils.console import console
from canfar_lab.version import display_version


def print_json(data: Any) -> None:
    print(json.dumps(data, indent=2))


def _format_text(text: str) -> str:
    if not isinstance(text, str):
        return text
    import re

    return re.sub(r"`([^`]+)`", r"[bold #ffaf00]\1[/bold #ffaf00]", text)


def print_error(message: str) -> None:
    formatted = _format_text(message)
    if "\n  " in formatted:
        msg, hint = formatted.split("\n  ", 1)
        console.print(f"[bold red]Error:[/bold red] {msg}")
        console.print(f"[dim]  {hint}[/dim]")
    else:
        console.print(f"[bold red]Error:[/bold red] {formatted}")


def print_ok(message: str) -> None:
    console.print(f"[bold green]✓[/bold green] {_format_text(message)}")


def print_hint(message: str) -> None:
    console.print(f"[dim]{_format_text(message)}[/dim]")


def print_markup(message: str) -> None:
    """Print Rich markup without dimming (status tables with selective bold)."""
    console.print(_format_text(message))


def print_info(message: str) -> None:
    console.print(f"[bold #00d7ff]{_format_text(message)}[/bold #00d7ff]")


def print_warn(message: str) -> None:
    console.print(f"[bold yellow]Warning:[/bold yellow] {_format_text(message)}")


@contextmanager
def progress_task(description: str, *, quiet: bool = False) -> Generator[None, None, None]:
    if quiet:
        yield
        return
    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        console=console,
    ) as progress:
        progress.add_task(description, total=None)
        yield


def env_list_table(rows: list[dict[str, str]]) -> None:
    if not rows:
        print_hint("No saved environments.")
        print_hint("  canfar lab save mylab")
        return
    table = Table(title="Saved environments")
    table.add_column("Name")
    table.add_column("Kind")
    table.add_column("Saved")
    table.add_column("Path")
    for row in rows:
        table.add_row(row["name"], row["kind"], row["saved_at"], row["path"])
    console.print(table)


def status_human(
    quotas: list,
    home_rows: list,
    active_project,
    arc_projects: list,
    processes: list[str],
    canfar_auth: str | None = None,
    canfar_sessions: list[str] | None = None,
    gms_groups=None,
    vault=None,
    resources: dict | None = None,
    *,
    full: bool = False,
) -> None:
    console.print(f"[bold]astroai {display_version()}[/bold]  status\n")
    if resources:
        mem = resources.get("mem_pct")
        cpu = resources.get("cpu_pct")
        cg = resources.get("cgroup_mem_pct")
        home = resources.get("home") or {}
        scratch = resources.get("scratch") or {}
        bits = []
        if cpu is not None:
            bits.append(f"cpu~{cpu}%")
        if mem is not None:
            bits.append(f"ram {mem}%")
        if cg is not None:
            bits.append(f"cgroup-mem {cg}%")
        if home.get("pct") is not None:
            bits.append(f"home {home['pct']}% ({home.get('source', '?')})")
        if scratch.get("pct") is not None:
            bits.append(f"scratch {scratch['pct']}%")
        gpus = resources.get("gpu") or []
        if gpus:
            bits.append("gpu " + ", ".join(f"{g.get('util_pct', '?')}%" for g in gpus[:2]))
        if bits:
            console.print("[bold]Session resources:[/bold] " + " · ".join(bits))
            for note in resources.get("notes") or []:
                console.print(f"[dim]  {note}[/dim]")
            console.print("")
    if canfar_auth:
        console.print(f"[bold]CANFAR Authentication:[/bold] {canfar_auth}\n")
    if full and gms_groups is not None and gms_groups.groups:
        names = ", ".join(gms_groups.groups[:8])
        extra = f" (+{len(gms_groups.groups) - 8} more)" if len(gms_groups.groups) > 8 else ""
        console.print(f"[bold]CADC groups:[/bold] {names}{extra}\n")
    elif full and Path("/arc/projects").is_dir() and gms_groups is None:
        console.print(
            "[dim]CADC groups: unavailable (install cadc-groups / run cadc-get-cert)[/dim]\n"
        )
    if active_project is not None:
        q = active_project.quota
        access = getattr(active_project, "access", "?")
        if q is not None:
            console.print(
                f"[bold]Team project:[/bold] {active_project.path} "
                f"[{access}] — {q.free} free of {q.total} ({q.pct}% used)"
            )
        else:
            console.print(f"[bold]Team project:[/bold] {active_project.path} [{access}]")
        console.print("")
    elif full and arc_projects:
        names = ", ".join(f"{p.name}({getattr(p, 'access', '?')})" for p in arc_projects[:6])
        extra = f" (+{len(arc_projects) - 6} more)" if len(arc_projects) > 6 else ""
        console.print(
            f"[dim]cwd not under /arc/projects — accessible team projects: {names}{extra}[/dim]\n"
        )
    elif full and Path("/arc/projects").is_dir():
        console.print("[dim]No readable team projects under /arc/projects[/dim]\n")
    if full and arc_projects:
        pt = Table(title="Team projects (/arc/projects)")
        pt.add_column("Project")
        pt.add_column("Access")
        pt.add_column("ACL groups")
        pt.add_column("GMS")
        pt.add_column("Vault groups")
        for p in arc_projects:
            groups = getattr(p, "acl_groups", None) or []
            group_cell = ", ".join(f"{g.name}({g.perms})" for g in groups[:4])
            if len(groups) > 4:
                group_cell += f" (+{len(groups) - 4})"
            gms_cell = (
                "yes"
                if getattr(p, "gms_member", None) is True
                else "no"
                if getattr(p, "gms_member", None) is False
                else "—"
            )
            vault_node = getattr(p, "vault", None)
            if vault_node is not None and vault_node.found:
                vault_groups = ", ".join(
                    g
                    for g in (
                        vault_node.read_group and f"ro:{vault_node.read_group}",
                        vault_node.write_group and f"rw:{vault_node.write_group}",
                    )
                    if g
                )
            else:
                vault_groups = "—"
            name = p.name
            if getattr(p, "is_cwd", False):
                name = f"{name} [cyan](cwd)[/cyan]"
            pt.add_row(
                name,
                getattr(p, "access", "?"),
                group_cell or "—",
                gms_cell,
                vault_groups or "—",
            )
        console.print(pt)
    if full and vault is not None and vault.nodes:
        extra = [
            node
            for node in vault.nodes
            if node.found and node.name.casefold() not in {p.name.casefold() for p in arc_projects}
        ]
        if extra:
            vt = Table(title="VOSpace vault (extra containers)")
            vt.add_column("Name")
            vt.add_column("Quota")
            vt.add_column("Read group")
            vt.add_column("Write group")
            vt.add_column("GMS")
            for node in extra:
                q = node.quota_line()
                quota_cell = f"{q.used} / {q.total} ({q.pct}%)" if q is not None else "—"
                gms_cell = (
                    "yes" if node.gms_member is True else "no" if node.gms_member is False else "—"
                )
                vt.add_row(
                    node.name,
                    quota_cell,
                    node.read_group or "—",
                    node.write_group or "—",
                    gms_cell,
                )
            console.print(vt)
    shown_quotas = quotas
    if not full:
        shown_quotas = [
            q for q in quotas if q.label in {"home", "scratch"} or getattr(q, "current", False)
        ]
    if shown_quotas:
        qt = Table(title="Storage quotas")
        qt.add_column("Location")
        qt.add_column("Used")
        qt.add_column("Free")
        qt.add_column("Total")
        qt.add_column("%")
        qt.add_column("Source")
        for q in shown_quotas:
            style = "red" if q.pct >= 95 else "yellow" if q.pct >= 80 else ""
            pct_cell = f"[{style}]{q.pct}%[/{style}]" if style else f"{q.pct}%"
            label = q.label
            if getattr(q, "current", False):
                label = f"{label} [cyan](cwd)[/cyan]"
            qt.add_row(label, q.used, q.free, q.total, pct_cell, getattr(q, "source", "") or "—")
        console.print(qt)
    if home_rows:
        ht = Table(title="Home breakdown")
        ht.add_column("Dir")
        ht.add_column("Size")
        ht.add_column("Notes")
        for d, s, n in home_rows:
            ht.add_row(d, s, n)
        console.print(ht)
    if processes:
        console.print("\n[bold]Top CPU processes[/bold]")
        for line in processes:
            console.print(f"  {line}")
    if canfar_sessions:
        console.print("\n[bold]CANFAR Active Sessions (canfar ps)[/bold]")
        for line in canfar_sessions:
            console.print(f"  {line}")
    if not full:
        console.print("")
        print_hint("  More: `canfar lab status --all`  ·  free home space: `canfar lab clean`")
