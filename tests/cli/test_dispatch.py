"""``canfar lab`` must start without the platform client's config loader."""

from __future__ import annotations

import os
import re
import subprocess
import sys

_ANSI = re.compile(r"\x1b\[[0-9;]*m")


def _help_text(proc: subprocess.CompletedProcess[str]) -> str:
    """Help as one line. GitHub Actions color and a narrow column split flags."""
    return _ANSI.sub("", proc.stdout).replace("\n", "")


def test_lab_dispatch_does_not_load_platform_config() -> None:
    env = os.environ.copy()
    env["COLUMNS"] = "120"
    env["NO_COLOR"] = "1"
    env.pop("FORCE_COLOR", None)
    proc = subprocess.run(
        [sys.executable, "-m", "canfar_lab.cli.dispatch", "lab", "studio", "--help"],
        check=False,
        capture_output=True,
        text=True,
        env=env,
    )
    assert proc.returncode == 0, proc.stderr
    assert "ConfigResetRequiredError" not in proc.stderr
    assert "--no-open" in _help_text(proc)
