"""evals/astro_eval.py scoring helpers (no network)."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

SPEC = importlib.util.spec_from_file_location(
    "astro_eval", Path(__file__).resolve().parents[2] / "evals" / "astro_eval.py"
)
assert SPEC and SPEC.loader
ev = importlib.util.module_from_spec(SPEC)
sys.modules["astro_eval"] = ev  # dataclasses resolve annotations through sys.modules
SPEC.loader.exec_module(ev)


def test_final_answer_takes_the_last_answer_line() -> None:
    text = "ANSWER: draft\nreasoning...\n**ANSWER**: 10.6847, 41.2688 deg\n"
    assert ev.final_answer(text) == "10.6847, 41.2688 deg"
    assert ev.final_answer("no answer here") is None


def test_numbers_handle_commas_units_and_unicode_minus() -> None:
    assert ev.numbers("RA 10.68°, Dec −41.27°") == [10.68, -41.27]
    assert ev.numbers("1.2e-3 pc") == [0.0012]
    assert ev.numbers("1,933 rows; 10.6847,41.2688") == [1933.0, 10.6847, 41.2688]


@pytest.mark.parametrize(
    ("args", "expected"),
    [
        ((10.0, 20.0, 10.0, 20.0), 0.0),
        ((0.0, 0.0, 0.0, 90.0), 90.0),
        ((0.0, 0.0, 180.0, 0.0), 180.0),
        ((359.9, 0.0, 0.1, 0.0), 0.2),
        ((0.0, 89.0, 180.0, 89.0), 2.0),
    ],
)
def test_angular_separation_limits(args: tuple[float, ...], expected: float) -> None:
    assert ev.angular_separation_deg(*args) == pytest.approx(expected, abs=1e-9)


def test_numeric_and_string_checks() -> None:
    task = next(t for t in ev.TASKS if t.id == "m31-coords")
    assert task.check("10.6849, 41.2690", [10.6847, 41.2687])
    assert not task.check("41.2690, 10.6849", [10.6847, 41.2687])
    assert not task.check("10.6847", [10.6847, 41.2687])
    assert not task.check(None, [10.6847, 41.2687])
    vega = next(t for t in ev.TASKS if t.id == "vega-sptype")
    assert vega.check("A0Va", "A0")
    assert not vega.check("B9V", "A0")


def test_growing_archive_count_uses_relative_tolerance() -> None:
    task = next(t for t in ev.TASKS if t.id == "cadc-megaprime-m31")
    assert task.check("There are 1,950 observations", [1955.0])
    assert not task.check("1900", [1955.0])
    assert task.literature_ok([2500.0])
    assert not task.literature_ok([1500.0])


def test_literature_bands_hold_known_values() -> None:
    barnard = next(t for t in ev.TASKS if t.id == "barnard-distance")
    assert barnard.literature_ok([1000 / 546.976])
    assert not barnard.literature_ok([1 / 546.976])  # parallax read as arcsec


def test_parse_run_collects_text_tools_and_done() -> None:
    lines = [
        {
            "type": "tool_use",
            "part": {"tool": "astroai_resolve_target", "state": {"status": "completed"}},
        },
        {"type": "text", "part": {"text": "M31 is at\nANSWER: 10.68, 41.27"}},
        {"type": "done", "status": "completed", "tokens": {"output": 12}, "cost": 0},
    ]
    run = ev.parse_run("noise\n" + "\n".join(json.dumps(x) for x in lines))
    assert run["tools"] == ["astroai_resolve_target:completed"]
    assert ev.final_answer(run["text"]) == "10.68, 41.27"
    assert run["done"]["tokens"]["output"] == 12


def test_task_ids_are_unique_and_eight() -> None:
    ids = [t.id for t in ev.TASKS]
    assert len(ids) == len(set(ids)) == 8
