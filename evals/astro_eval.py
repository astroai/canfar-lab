"""Astronomy task eval for agents that use the astroai MCP tools (OpenScience first).

Each task has a prompt, a live reference computed with the same tools the agent
gets (canfar_workload.astro_data), a literature band the reference must fall in,
and a checker for the agent's final ``ANSWER:`` line.

    python evals/astro_eval.py oracle              # references only, no model key needed
    python evals/astro_eval.py agent [-m provider/model] [--only id,id]  # needs a provider key

``oracle`` checks the tools and the literature bands; ``agent`` runs
``openscience run --format json`` once per task (gated tools are denied) and
scores the answer against the live reference. Results go to stdout as a table
and to ``--out`` as JSON.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from canfar_workload import astro_data as ad

ANSWER_RE = re.compile(r"^\s*\**ANSWER\**\s*[:=]\s*(.+?)\s*$", re.IGNORECASE | re.MULTILINE)
NUMBER_RE = re.compile(r"[-+]?(?:\d+\.\d*|\.\d+|\d+)(?:[eE][-+]?\d+)?")
THOUSANDS_RE = re.compile(r"(?<=\d),(?=\d{3}(?!\d|\.\d))")

SUFFIX = (
    "\n\nUse the astroai data tools rather than memory. Finish with one line of the form "
    "`ANSWER: <value>` giving only the requested value(s) in the requested units."
)


def numbers(text: str) -> list[float]:
    text = THOUSANDS_RE.sub("", text).replace(",", " ").replace("−", "-")
    return [float(x) for x in NUMBER_RE.findall(text)]


def final_answer(text: str) -> str | None:
    found = ANSWER_RE.findall(text or "")
    return found[-1].strip().strip("`") if found else None


def close(values: list[float], target: list[float], tol: list[float]) -> bool:
    return len(values) >= len(target) and all(
        abs(v - t) <= e for v, t, e in zip(values, target, tol, strict=False)
    )


def angular_separation_deg(ra1: float, dec1: float, ra2: float, dec2: float) -> float:
    """Vincenty formula: stable at small and near-antipodal separations."""
    r1, d1, r2, d2 = map(math.radians, (ra1, dec1, ra2, dec2))
    dra = r2 - r1
    num = math.hypot(
        math.cos(d2) * math.sin(dra),
        math.cos(d1) * math.sin(d2) - math.sin(d1) * math.cos(d2) * math.cos(dra),
    )
    den = math.sin(d1) * math.sin(d2) + math.cos(d1) * math.cos(d2) * math.cos(dra)
    return math.degrees(math.atan2(num, den))


def gaia_by_name(name: str, columns: str) -> list[str]:
    """Gaia DR3 row for a named star via its SIMBAD cross-identifier (no epoch trap)."""
    ids = ad.tap_query(
        "simbad",
        "SELECT i2.id FROM ident AS i1 JOIN ident AS i2 ON i1.oidref = i2.oidref "
        f"WHERE i1.id = {ad._adql_str(name)} AND i2.id LIKE 'Gaia DR3 %'",
    )["rows"]
    if not ids:
        raise RuntimeError(f"no Gaia DR3 identifier for {name} in SIMBAD")
    source_id = int(ids[0][0].split()[-1])
    rows = ad.tap_query(
        "gaia", f"SELECT {columns} FROM gaiadr3.gaia_source WHERE source_id = {source_id}"
    )["rows"]
    return rows[0]


@dataclass
class Task:
    id: str
    prompt: str
    oracle: Callable[[], list[float] | str]
    #: Literature value(s) with tolerance; the live reference must agree.
    literature: list[float] | str
    tol: list[float] = field(default_factory=list)
    rel_tol: float | None = None
    note: str = ""

    def literature_ok(self, reference: list[float] | str) -> bool:
        """The live reference agrees with the literature (counts may only grow)."""
        if isinstance(reference, str) or isinstance(self.literature, str):
            return str(reference).upper().startswith(str(self.literature).upper())
        if self.rel_tol is not None:
            return all(
                lit * (1 - self.rel_tol) <= ref <= lit * 5
                for ref, lit in zip(reference, self.literature, strict=False)
            )
        return close(reference, self.literature, self.tol)

    def check(self, answer: str | None, reference: list[float] | str) -> bool:
        if answer is None:
            return False
        if isinstance(reference, str):
            return answer.strip().upper().startswith(reference.upper())
        values = numbers(answer)
        if self.rel_tol is not None:
            return bool(values) and all(
                abs(v - r) <= self.rel_tol * abs(r) for v, r in zip(values, reference, strict=False)
            )
        return close(values, reference, self.tol)


def _m31() -> list[float]:
    r = ad.resolve_target("M31")
    return [r["ra_deg"], r["dec_deg"]]


def _barnard_pc() -> list[float]:
    (parallax_mas,) = gaia_by_name("Barnard's star", "parallax")
    return [1000.0 / float(parallax_mas)]


def _proxima_g() -> list[float]:
    (gmag,) = gaia_by_name("Proxima Centauri", "phot_g_mean_mag")
    return [float(gmag)]


def _vega_sptype() -> str:
    rows = ad.tap_query(
        "simbad",
        "SELECT sp_type FROM basic JOIN ident ON oidref = oid WHERE id = 'Vega'",
    )["rows"]
    return rows[0][0][:2]


def _z_3c273() -> list[float]:
    rows = ad.tap_query(
        "simbad",
        "SELECT rvz_redshift FROM basic JOIN ident ON oidref = oid WHERE id = '3C 273'",
    )["rows"]
    return [float(rows[0][0])]


def _m31_m33() -> list[float]:
    a, b = ad.resolve_target("M31"), ad.resolve_target("M33")
    return [angular_separation_deg(a["ra_deg"], a["dec_deg"], b["ra_deg"], b["dec_deg"])]


def _cadc_megaprime() -> list[float]:
    out = ad.cadc_search(
        target="M31",
        radius_deg=0.05,
        collection="CFHT",
        instrument="MegaPrime",
        maxrec=20000,
        preview=1,
    )
    if out["maybe_truncated_by_maxrec"]:
        raise RuntimeError("CADC count hit maxrec")
    return [float(out["row_count"])]


def _rrlyr_period() -> list[float]:
    r = ad.resolve_target("RR Lyr")
    rows = ad.tap_query(
        "gaia",
        "SELECT v.pf FROM gaiadr3.gaia_source AS g JOIN gaiadr3.vari_rrlyrae AS v "
        "USING (source_id) WHERE 1 = CONTAINS(POINT(g.ra, g.dec), "
        f"CIRCLE({r['ra_deg']}, {r['dec_deg']}, 0.01)) AND g.phot_g_mean_mag < 9",
    )["rows"]
    return [float(rows[0][0])]


TASKS: list[Task] = [
    Task(
        "m31-coords",
        "What are the ICRS coordinates of the Andromeda galaxy (M31) nucleus, as RA and Dec "
        "in decimal degrees?",
        _m31,
        literature=[10.6847, 41.2688],
        tol=[0.005, 0.005],
    ),
    Task(
        "barnard-distance",
        "What is the distance to Barnard's Star in parsecs, from its Gaia DR3 parallax?",
        _barnard_pc,
        literature=[1.828],
        tol=[0.01],
    ),
    Task(
        "proxima-gmag",
        "What is the Gaia DR3 G-band mean magnitude of Proxima Centauri?",
        _proxima_g,
        literature=[8.98],
        tol=[0.03],
        note="high proper motion: a J2000 cone search misses the Gaia 2016.0 position",
    ),
    Task(
        "vega-sptype",
        "What is the spectral type of Vega according to SIMBAD? Give the MK class.",
        _vega_sptype,
        literature="A0",
    ),
    Task(
        "3c273-redshift",
        "What is the redshift of the quasar 3C 273?",
        _z_3c273,
        literature=[0.158],
        tol=[0.002],
    ),
    Task(
        "m31-m33-separation",
        "What is the angular separation between M31 and M33 on the sky, in degrees?",
        _m31_m33,
        literature=[14.78],
        tol=[0.05],
    ),
    Task(
        "cadc-megaprime-m31",
        "How many CFHT MegaPrime observations does the CADC archive (ObsCore) hold whose "
        "footprint intersects a 0.05 degree radius circle around M31? Give the count.",
        _cadc_megaprime,
        literature=[1933.0],
        rel_tol=0.01,
        note="archive grows; the agent is scored against the live count",
    ),
    Task(
        "rrlyr-period",
        "What is the pulsation period, in days, of the star RR Lyrae according to Gaia DR3?",
        _rrlyr_period,
        literature=[0.5668],
        tol=[0.001],
    ),
]


def run_oracle(tasks: list[Task]) -> list[dict[str, Any]]:
    rows = []
    for task in tasks:
        t0 = time.monotonic()
        row: dict[str, Any] = {"task": task.id, "literature": task.literature}
        try:
            ref = task.oracle()
            row.update(reference=ref, **{"pass": task.literature_ok(ref)})
        except Exception as exc:  # noqa: BLE001 — report every task
            row.update(error=str(exc)[:200], **{"pass": False})
        row["seconds"] = round(time.monotonic() - t0, 1)
        rows.append(row)
    return rows


def parse_run(stdout: str) -> dict[str, Any]:
    text, tools, done = [], [], {}
    for line in stdout.splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            continue
        kind = event.get("type")
        if kind == "text":
            text.append(event.get("part", {}).get("text", ""))
        elif kind == "tool_use":
            part = event.get("part", {})
            tools.append(f"{part.get('tool')}:{part.get('state', {}).get('status')}")
        elif kind == "done":
            done = event
    return {"text": "\n".join(text), "tools": tools, "done": done}


def run_agent(tasks: list[Task], *, binary: str, model: str | None, deadline: int) -> list[dict]:
    rows = []
    for task in tasks:
        try:
            ref = task.oracle()
        except Exception as exc:  # noqa: BLE001
            rows.append({"task": task.id, "error": f"reference: {exc}"[:200], "pass": False})
            continue
        with tempfile.TemporaryDirectory(prefix=f"eval-{task.id}-") as work:
            cmd = [
                binary,
                "run",
                "--format",
                "json",
                "--deny-prompts",
                "--workspace",
                work,
                "--deadline",
                str(deadline),
            ]
            if model:
                cmd += ["-m", model]
            t0 = time.monotonic()
            try:
                proc = subprocess.run(
                    [*cmd, task.prompt + SUFFIX],
                    cwd=work,
                    capture_output=True,
                    text=True,
                    timeout=deadline + 60,
                    check=False,
                )
                out, rc = proc.stdout, proc.returncode
            except subprocess.TimeoutExpired as exc:
                out = exc.stdout.decode() if isinstance(exc.stdout, bytes) else exc.stdout or ""
                rc = -1
        run = parse_run(out)
        answer = final_answer(run["text"])
        done = run["done"]
        rows.append(
            {
                "task": task.id,
                "reference": ref,
                "answer": answer,
                "pass": task.check(answer, ref),
                "tools": run["tools"],
                "exit": rc,
                "seconds": round(time.monotonic() - t0, 1),
                "tokens": (done.get("tokens") or {}).get("output"),
                "cost": done.get("cost"),
            }
        )
    return rows


def fmt(value: Any) -> str:
    if isinstance(value, list):
        return ", ".join(f"{v:.6g}" if isinstance(v, float) else str(v) for v in value)
    return "" if value is None else str(value)


def table(rows: list[dict[str, Any]], mode: str) -> str:
    cols = (
        ["task", "reference", "literature", "pass", "seconds"]
        if mode == "oracle"
        else ["task", "reference", "answer", "pass", "tools", "seconds"]
    )
    out = ["| " + " | ".join(cols) + " |", "|" + "---|" * len(cols)]
    for row in rows:
        cells = []
        for col in cols:
            val = row.get(col)
            if col == "tools":
                val = len([t for t in val or [] if t.startswith("astroai_")])
            if col == "reference" and "error" in row:
                val = "error: " + row["error"]
            cells.append(fmt(val).replace("|", "/"))
        out.append("| " + " | ".join(cells) + " |")
    passed = sum(r["pass"] for r in rows)
    out.append(f"\n{passed}/{len(rows)} passed")
    return "\n".join(out)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("mode", choices=["oracle", "agent"])
    parser.add_argument("-m", "--model", help="provider/model for openscience run")
    parser.add_argument("--only", help="comma-separated task ids")
    parser.add_argument("--binary", default=os.environ.get("OPENSCIENCE", "openscience"))
    parser.add_argument("--deadline", type=int, default=600, help="seconds per task")
    parser.add_argument("--out", type=Path, help="write results JSON here")
    args = parser.parse_args(argv)
    tasks = TASKS
    if args.only:
        wanted = set(args.only.split(","))
        tasks = [t for t in TASKS if t.id in wanted]
        if unknown := wanted - {t.id for t in tasks}:
            parser.error(f"unknown task ids: {', '.join(sorted(unknown))}")
    with tempfile.TemporaryDirectory(prefix="astro-eval-data-") as data_dir:
        # oracle tools write result tables; keep them out of the caller's cwd
        os.environ.setdefault("ASTROAI_DATA_DIR", data_dir)
        rows = (
            run_oracle(tasks)
            if args.mode == "oracle"
            else run_agent(tasks, binary=args.binary, model=args.model, deadline=args.deadline)
        )
    print(table(rows, args.mode))
    if args.out:
        args.out.write_text(json.dumps(rows, indent=2) + "\n", encoding="utf-8")
    return 0 if all(r["pass"] for r in rows) else 1


if __name__ == "__main__":
    sys.exit(main())
