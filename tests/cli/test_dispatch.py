"""``canfar lab`` must start without the platform client's config loader."""

from __future__ import annotations

import subprocess
import sys


def test_lab_dispatch_does_not_load_platform_config() -> None:
    proc = subprocess.run(
        [sys.executable, "-m", "canfar_lab.cli.dispatch", "lab", "studio", "--help"],
        check=False,
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stderr
    assert "ConfigResetRequiredError" not in proc.stderr
    assert "--no-open" in proc.stdout
