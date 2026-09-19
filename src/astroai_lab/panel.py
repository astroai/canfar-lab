"""Headless review-bench panels (``astroai panel``).

Same freeze → blind-parallel → audit protocol as ``~/dsh/panel.sh``, but
resolved through the vendored bench (``data/review-bench``) and the shared
AstroAI credential plumbing, so it runs from laptop, any contributed
session, marimo notebooks (via :func:`run_panel`), or plain CLI.
"""

from __future__ import annotations

import re
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any

from astroai_lab.agent.review_bench import managed_bench_dir, vendored_review_bench_root
from astroai_lab.errors import LabError

PANEL_LENSES = (
    "statistician",
    "mathematician",
    "data scientist",
    "ml_engineer",
    "physicist",
    "astrophysicist",
    "software_engineer",
    "writing_editor",
    "canfar_expert",
    "devops",
    "plot_master",
    "desloper",
    "scientific_innovator",
    "devils_advocate",
)


def slugify(slug: str) -> str:
    slug = re.sub(r"[^a-z0-9-]+", "-", slug.lower()).strip("-")
    return slug or "review"


def resolve_repo(repo: str | Path | None) -> Path:
    if repo:
        return Path(repo).expanduser().resolve()
    from astroai_lab.config.settings import get_settings

    try:
        work = get_settings().resolve_work_dir()
        if work.is_dir():
            return work.resolve()
    except Exception:  # noqa: BLE001 — fall back to cwd
        pass
    return Path.cwd().resolve()


def panel_id_for(repo: Path, slug: str) -> str:
    base = f"{datetime.now().strftime('%F')}-{slugify(slug)}"
    if not (repo / "panel" / base / "00-brief.md").exists():
        return base
    return f"{base}-{datetime.now().strftime('%H%M')}"


def scaffold_repo_dsh(target: Path, *, force: bool = False) -> list[str]:
    """Install ``.dsh/cordis.patch.yml`` + ``README.md`` from the template."""
    template = vendored_review_bench_root() / "project-template" / ".dsh"
    wrote: list[str] = []
    for name in ("cordis.patch.yml", "README.md"):
        src, dst = template / name, target / ".dsh" / name
        if not src.is_file():
            continue
        if dst.is_file() and not force:
            continue
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        wrote.append(str(dst))
    return wrote


def build_task(repo: Path, claims: str, panel_id: str) -> str:
    """Task prompt identical to ``panel.sh`` (phases 0–2), managed paths."""
    try:
        bench = managed_bench_dir()
        skill_dir = bench / "skills" / "review-panel"
        if not (skill_dir / "SKILL.md").is_file():
            skill_dir = vendored_review_bench_root() / "skills" / "review-panel"
    except LabError:
        skill_dir = vendored_review_bench_root() / "skills" / "review-panel"
    preset = managed_bench_dir() / "presets" / "review-bench" / "agent.cordis.yml"
    if not preset.is_file():
        preset = vendored_review_bench_root() / "presets" / "review-bench" / "agent.cordis.yml"
    lenses = ", ".join(PANEL_LENSES)
    return f"""You are chairing a review panel. Protocol: load {skill_dir}/SKILL.md
and follow phases 0–2 (freeze, blind parallel round, evidence audit), with the
finding schema in {skill_dir}/references/rubric.md.

Claims under review:
{claims}

Artefact: this repo @ HEAD. Report dir: panel/{panel_id}/ (create it if missing).

Phase 0 — freeze and brief. Record: git rev-parse HEAD, git status --porcelain,
pixi.lock (or requirements) hash, torch/CUDA versions, and the exact command
reproducing the headline result (run it once, keep output). Write
panel/{panel_id}/00-brief.md using
{skill_dir}/references/brief-template.md. If the claims above are
too vague to be falsifiable (named metric, split, threshold), propose a claim
list first and proceed with your best reading — state the assumption in the brief.

Phase 1 — blind parallel round via the workflow tool. Read
{skill_dir}/references/panel-round1.js and run it with one lens
per claim-group: {lenses} (drop lenses that
are irrelevant to the artefact and state the panel size in the brief).
Headless workflow children share one generic persona, so paste each lens's
specialist persona text VERBATIM from
{preset} (the ask_<role> rows) into its
workflow task, followed by: open with the strongest alternative explanation
for the headline result, run that persona's mandatory probes, then judgement
checks. One pass each, <=25 tool calls, no delegation, no writes to the repo;
mutation experiments only on copies under /tmp. Persist raw returns to
panel/{panel_id}/01-findings.json BEFORE reasoning further.

Phase 2 — audit before belief. Re-run every cited command yourself; a finding
whose evidence does not reproduce is UNVERIFIED. One targeted follow-up round
only for contested findings, then verdict.

Write panel/{panel_id}/02-report.md using
{skill_dir}/references/report-template.md: verdict per claim
(established | suggestive | speculative, binding on the prose), fix list,
verbatim dissent, and open falsification tests. Print the verdict table to
stdout at the end."""


def dsh_cmd(*, patch: Path | None, task: str, dsh_bin: str | None = None) -> list[str]:
    """Build a headless ``dsh`` argv. Prefer a real binary over ``npx -y``."""
    from astroai_lab import studio as _studio

    resolved = dsh_bin or _studio.dsh_binary()
    if resolved is None:
        raise LabError(
            "No `dsh` executable found.",
            hint=(
                f"Install it globally: npm install -g {_studio.DSH_NPM}\n"
                "Do not use `npx -y @deepseek-ai/dsh …`: npm swallows the "
                "launcher flags Panel depends on."
            ),
        )
    cmd = [resolved, "--profile", "headless"]
    if patch is not None and patch.is_file():
        cmd += ["--patch", str(patch)]
    cmd.append(task)
    return cmd


def run_panel(
    repo: str | Path | None,
    claims: str,
    slug: str = "review",
    *,
    dry_run: bool = False,
    ensure_credentials: bool = True,
) -> dict[str, Any]:
    """Run (or plan, with ``dry_run``) a headless AstroAI Panel.

    Returns ``{"repo", "panel_id", "route", "task", "report_dir"}``; with
    ``dry_run`` nothing executes. Importable from marimo notebooks without a
    shell: ``from astroai_lab.panel import run_panel``.
    """
    from astroai_lab.agent import review_bench as _rb
    from astroai_lab.agent.support import load_support

    repo_path = resolve_repo(repo)
    if not repo_path.is_dir():
        raise LabError(
            f"Repo not found: {repo_path}",
            hint="Pass an existing checkout, e.g. astroai panel run /scratch/src/torchsky ...",
        )
    patch = repo_path / ".dsh" / "cordis.patch.yml"
    if not patch.is_file() and not dry_run:
        scaffold_repo_dsh(repo_path)
    pid = panel_id_for(repo_path, slug)
    keys = _rb.discover_dsh_keys()
    route: str | None = None
    if ensure_credentials and not dry_run:
        _rb.ensure_dsh_dotenv(dry_run=False)
        route = _rb.ensure_dsh_settings(dry_run=False)
    elif keys:
        catalog = load_support()
        first = next((k for k in catalog.dsh_keys if k in keys), None)
        if first is not None:
            route, _ = catalog.key_to_route()[first]
    if not keys and not dry_run:
        raise LabError(
            "No dsh provider key found (checked env, ~/.astroai/lab/.env, opencode auth).",
            hint="Run `opencode auth login` or `export DEEPSEEK_API_KEY=...`, "
            "then `astroai agent setup` to persist it.",
        )
    task = build_task(repo_path, claims, pid)
    if dry_run:
        return {
            "repo": str(repo_path),
            "panel_id": pid,
            "route": route,
            "task": task,
            "report_dir": str(repo_path / "panel" / pid),
        }
    from astroai_lab.utils.subprocess import run

    (repo_path / "panel" / pid).mkdir(parents=True, exist_ok=True)
    cmd = dsh_cmd(patch=patch if patch.is_file() else None, task=task)
    fallback_note: str | None = None
    try:
        run(cmd, cwd=repo_path)
    except LabError as exc:
        if not _rb.is_opencode_go_headless_error(str(exc)):
            raise
        fallback = _rb.next_fallback_provider(route, keys)
        if fallback is None:
            raise LabError(
                "OpenCode Go rejected headless (missing session). "
                "No alternate provider key available.",
                hint="Export DEEPSEEK_API_KEY or GEMINI_API_KEY, or use "
                "`astroai panel web` on a laptop.",
            ) from exc
        fallback_note = (
            f"OpenCode Go headless failed (session required); "
            f"falling back to {fallback} for this run."
        )
        _rb.ensure_dsh_settings(dry_run=False, force_provider=fallback)
        route = fallback
        run(cmd, cwd=repo_path)
    return {
        "repo": str(repo_path),
        "panel_id": pid,
        "route": route,
        "task": task,
        "report_dir": str(repo_path / "panel" / pid),
        "fallback_note": fallback_note,
    }


def panel_status(panel_dir: str | Path) -> str:
    """Print the verdict table from a finished panel's ``02-report.md``."""
    report = Path(panel_dir).expanduser().resolve() / "02-report.md"
    if not report.is_file():
        raise LabError(
            f"No report at {report}",
            hint="Panels write panel/<date>-<slug>/02-report.md under the repo.",
        )
    lines = report.read_text(encoding="utf-8").splitlines()
    verdicts = [
        line.strip()
        for line in lines
        if re.search(r"established|suggestive|speculative|UNVERIFIED|INFRA_ERROR", line)
    ]
    if not verdicts:
        return report.read_text(encoding="utf-8")[:2000]
    return "\n".join(verdicts)
