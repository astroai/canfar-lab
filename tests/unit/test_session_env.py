from __future__ import annotations

import pwd
from pathlib import Path

import pytest

from astroai_lab.core.session_common import scratch_cache_root
from astroai_lab.shell.session_env import export_shell, resolve_session_env


def test_user_tag_numeric_uid_when_passwd_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    import os

    from astroai_lab.core import session_common

    monkeypatch.delenv("USER", raising=False)
    monkeypatch.delenv("LOGNAME", raising=False)

    def _missing(_uid: int) -> pwd.struct_passwd:
        raise KeyError(_uid)

    monkeypatch.setattr(session_common.pwd, "getpwuid", _missing)
    assert session_common.user_tag() == str(os.getuid())


def _scratch_session(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path]:
    """Point WORK/SCRATCH at tmp_path subdirs; return (work, scratch)."""
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    work = tmp_path / "srcdir"
    work.mkdir()
    monkeypatch.setenv("WORK", str(work))
    monkeypatch.setenv("SCRATCH", str(scratch))
    monkeypatch.delenv("ASTROAI_LAB_BIN_DIR", raising=False)
    return work, scratch


def test_resolve_session_env_prefers_home_bin(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    work, scratch = _scratch_session(tmp_path, monkeypatch)
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    for var in (
        "UV_CACHE_DIR",
        "PIP_CACHE_DIR",
        "NPM_CONFIG_CACHE",
        "PIXI_CACHE_DIR",
        "MAMBA_PKGS_DIRS",
        "PIXI_HOME",
        "UV_PYTHON_INSTALL_DIR",
    ):
        monkeypatch.delenv(var, raising=False)

    env = resolve_session_env(ensure=True)
    assert env.astroai_lab_bin_dir == home / ".local" / "bin"
    assert env.uv_cache_dir == scratch_cache_root(work, scratch) / "uv"


def test_scratch_overrides_image_build_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    work, scratch = _scratch_session(tmp_path, monkeypatch)
    monkeypatch.setenv("PIXI_CACHE_DIR", "/usr/local/share/pixi/cache")
    monkeypatch.setenv("UV_PYTHON_INSTALL_DIR", "/usr/local/share/uv/python")
    monkeypatch.setenv("PIXI_HOME", "/usr/local/share/pixi")

    env = resolve_session_env(ensure=False)
    cache_root = scratch_cache_root(work, scratch)
    assert env.pixi_cache_dir == cache_root / "pixi"
    assert env.uv_python_install_dir == env.astroai_lab_runtime_root / "uv" / "python"
    assert env.pixi_home == env.astroai_lab_runtime_root / "pixi"


def test_package_caches_never_land_on_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """XDG/uv/pixi/rattler caches stay off $HOME even when the platform sets them there."""
    work, scratch = _scratch_session(tmp_path, monkeypatch)
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("XDG_CACHE_HOME", str(home / ".cache"))
    monkeypatch.setenv("UV_CACHE_DIR", str(home / ".cache" / "uv"))
    monkeypatch.setenv("PIXI_CACHE_DIR", str(home / ".pixi" / "cache"))
    monkeypatch.setenv("RATTLER_CACHE_DIR", str(home / ".cache" / "rattler"))

    env = resolve_session_env(ensure=False)
    cache_root = scratch_cache_root(work, scratch)
    home_s = str(home)
    for path in (
        env.xdg_cache_home,
        env.uv_cache_dir,
        env.pixi_cache_dir,
        env.rattler_cache_dir,
    ):
        assert str(path) != home_s
        assert not str(path).startswith(home_s + "/")
    assert env.xdg_cache_home == cache_root
    assert env.uv_cache_dir == cache_root / "uv"
    assert env.pixi_cache_dir == cache_root / "pixi"
    assert env.rattler_cache_dir == cache_root / "rattler"


def test_package_caches_off_home_without_scratch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    work = tmp_path / "work"
    work.mkdir()
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("WORK", str(work))
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("XDG_CACHE_HOME", str(home / ".cache"))
    monkeypatch.setattr("astroai_lab.shell.session_env.resolve_scratch_dir", lambda: None)

    env = resolve_session_env(ensure=False)
    cache_root = scratch_cache_root(work, None)
    assert env.xdg_cache_home == cache_root
    assert env.uv_cache_dir == cache_root / "uv"
    assert env.rattler_cache_dir == cache_root / "rattler"
    assert not str(env.xdg_cache_home).startswith(str(home))


def test_cache_root_not_home_when_work_is_on_home(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "home"
    work = home / "work"
    work.mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("WORK", str(work))
    monkeypatch.setattr("astroai_lab.shell.session_env.resolve_scratch_dir", lambda: None)

    env = resolve_session_env(ensure=False)
    home_s = str(home)
    for path in (env.xdg_cache_home, env.uv_cache_dir, env.pixi_cache_dir, env.rattler_cache_dir):
        assert str(path) != home_s
        assert not str(path).startswith(home_s + "/")
    assert str(env.xdg_cache_home).startswith("/tmp/")


def test_resolve_session_env_honors_scratch_backed_cache_var(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Pre-existing cache vars pointing under scratch are kept, not redirected.

    Complement to test_scratch_overrides_image_build_env: a cache var that a
    user already set to a scratch-backed location must survive resolution,
    while a stray system-prefix value is still redirected to the session cache.
    """
    work, scratch = _scratch_session(tmp_path, monkeypatch)

    custom_uv = scratch / "custom-uv"
    custom_uv.mkdir()
    custom_pip = scratch / "custom-pip"
    custom_pip.mkdir()
    custom_xdg = scratch / "xdg-cache"
    custom_xdg.mkdir()
    monkeypatch.setenv("UV_CACHE_DIR", str(custom_uv))
    monkeypatch.setenv("PIP_CACHE_DIR", str(custom_pip))
    monkeypatch.setenv("XDG_CACHE_HOME", str(custom_xdg))
    # A stray system-prefix value (not under work or scratch) is redirected.
    monkeypatch.setenv("PIXI_CACHE_DIR", "/usr/local/share/pixi/cache")

    env = resolve_session_env(ensure=False)
    cache_root = scratch_cache_root(work, scratch)
    # Pre-existing scratch-backed locations are honored verbatim.
    assert env.uv_cache_dir == custom_uv
    assert env.pip_cache_dir == custom_pip
    assert env.xdg_cache_home == custom_xdg
    # The system-prefix value is redirected to the session cache default.
    assert env.pixi_cache_dir == cache_root / "pixi"


def test_export_shell_includes_astroai_lab_vars(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    work = tmp_path / "work"
    work.mkdir()
    monkeypatch.setenv("WORK", str(work))
    monkeypatch.delenv("SCRATCH", raising=False)

    out = export_shell(ensure=False)
    assert "export ASTROAI_LAB_BIN_DIR=" in out
    assert "export ASTROAI_LAB_RUNTIME_ROOT=" in out
    assert "export WORK=" in out
    assert "export SRCDIR=" in out
    # NOTE: no "SCRATCH absent" assertion — a writable /scratch on the host is
    # the canonical scratch default and is legitimately exported when unset.
    assert "CANFAR_LAB_" not in out
