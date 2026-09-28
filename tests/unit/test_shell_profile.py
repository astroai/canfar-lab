from __future__ import annotations

import shutil
import subprocess
import time
from pathlib import Path

import pytest

_PROFILE = (
    Path(__file__).resolve().parents[2] / "src" / "canfar_lab" / "data" / "shell" / "profile.sh"
)

pytestmark = pytest.mark.skipif(shutil.which("bash") is None, reason="needs bash")


def _run(home: Path, script: str, *, interactive: bool = False) -> str:
    flags = ["--noprofile", "--norc"] + (["-i"] if interactive else [])
    proc = subprocess.run(
        ["bash", *flags, "-c", f'source "{_PROFILE}"\n{script}'],
        env={"HOME": str(home), "PATH": "/usr/bin:/bin", "CANFAR_LAB_SHELL_DIR": str(home)},
        capture_output=True,
        text=True,
        timeout=60,
        check=True,
    )
    return proc.stdout


def test_open_shell_picks_up_saved_and_removed_keys(tmp_path: Path) -> None:
    keys = tmp_path / ".astroai" / "lab" / ".env"
    keys.parent.mkdir(parents=True)
    keys.write_text("ASTRO_T1=one\n", encoding="utf-8")
    out = _run(
        tmp_path,
        f"""echo "start=${{ASTRO_T1:-none}}"
_astroai_keys_load
echo "unchanged=${{ASTRO_T1:-none}}"
printf 'ASTRO_T2=two\\n' > "{keys}.tmp" && mv "{keys}.tmp" "{keys}"
_astroai_keys_load
echo "after=${{ASTRO_T1:-none}} ${{ASTRO_T2:-none}}"
rm "{keys}"
_astroai_keys_load
echo "gone=${{ASTRO_T2:-none}}"
""",
    )
    assert "start=one" in out
    assert "unchanged=one" in out
    assert "after=none two" in out  # removed from the file -> unset in the shell
    assert "gone=none" in out


def test_key_saved_while_idle_at_the_prompt_reaches_the_next_command(tmp_path: Path) -> None:
    keys = tmp_path / ".astroai" / "lab" / ".env"
    keys.parent.mkdir(parents=True)
    keys.write_text("ASTRO_T1=one\n", encoding="utf-8")
    proc = subprocess.Popen(
        ["bash", "--noprofile", "--norc", "-i"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        env={"HOME": str(tmp_path), "PATH": "/usr/bin:/bin", "CANFAR_LAB_SHELL_DIR": str(tmp_path)},
        text=True,
    )
    assert proc.stdin is not None
    proc.stdin.write(f'source "{_PROFILE}"\necho ready\n')
    proc.stdin.flush()
    time.sleep(1.0)  # the shell is back at its prompt
    tmp = keys.with_name(".env.tmp")
    tmp.write_text("ASTRO_T1=one\nASTRO_T2=two\n", encoding="utf-8")
    tmp.replace(keys)
    time.sleep(2.2)  # past the 2 s throttle, as when coming back from the hub
    out, _ = proc.communicate('echo "next=${ASTRO_T2:-none}"\nexit\n', timeout=60)
    assert "ready" in out
    assert "next=two" in out


def test_only_interactive_shells_hook_the_prompt(tmp_path: Path) -> None:
    assert "_astroai_keys_load" not in _run(tmp_path, 'echo "pc=${PROMPT_COMMAND:-}"')
    assert "pc=_astroai_keys_load" in _run(
        tmp_path, 'echo "pc=${PROMPT_COMMAND:-}"', interactive=True
    )
