from __future__ import annotations

from pathlib import Path

import pytest

from astroai_lab.agent import install as install_mod
from astroai_lab.errors import LabError


def test_gh_release_uses_public_curl_when_gh_not_authed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("TMPDIR", str(tmp_path))
    monkeypatch.setattr(install_mod, "_gh_auth_ok", lambda: False)
    monkeypatch.setattr(install_mod, "_bin_dir", lambda: tmp_path / "bin")
    (tmp_path / "bin").mkdir()
    seen: list[tuple[str, str, Path]] = []

    def fake_curl(repo: str, asset: str, dest: Path) -> None:
        seen.append((repo, asset, dest))
        dest.parent.mkdir(parents=True, exist_ok=True)
        # Minimal tar.gz with a codex binary inside.
        import tarfile

        bin_path = tmp_path / "codex"
        bin_path.write_text("#!/bin/sh\n", encoding="utf-8")
        bin_path.chmod(0o755)
        with tarfile.open(dest, "w:gz") as tf:
            tf.add(bin_path, arcname="codex")

    monkeypatch.setattr(install_mod, "_download_public_gh_release", fake_curl)
    install_mod._gh_release_bin("openai/codex", "codex-x86_64-unknown-linux-musl.tar.gz", "codex")
    assert seen
    assert (tmp_path / "bin" / "codex").is_file()


def test_gh_release_requires_auth_when_flag_set(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(install_mod, "_gh_auth_ok", lambda: False)
    with pytest.raises(LabError, match="requires GitHub CLI authentication"):
        install_mod._gh_release_bin(
            "private/org",
            "tool.tar.gz",
            "tool",
            requires_gh_auth=True,
        )
