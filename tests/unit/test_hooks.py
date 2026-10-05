from __future__ import annotations

from pathlib import Path

_HOOKS = Path(__file__).resolve().parents[2] / "src" / "canfar_lab" / "data" / "shell" / "hooks.sh"


def test_hooks_keep_scratch_reminder_off_the_quota_nag() -> None:
    text = _HOOKS.read_text(encoding="utf-8")
    assert "__canfar_lab_scratch_reminder" in text
    assert "PROMPT_COMMAND" in text
    assert "quota_reminder" not in text
    assert "__canfar_lab_quota_used_pct" not in text
    assert "check canfar lab status for details" not in text
    assert '*"__canfar_lab_scratch_reminder"*' in text
