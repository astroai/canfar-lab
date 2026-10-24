from __future__ import annotations

import json

import pytest

from canfar_lab import dsh_release
from canfar_lab import studio as studio_mod


def test_semver_key_orders_prereleases_below_releases():
    assert dsh_release.semver_key("0.2.1") > dsh_release.semver_key("0.2.1-rc.2")
    assert dsh_release.semver_key("0.2.1-rc.2") > dsh_release.semver_key("0.2.1-rc.1")
    assert dsh_release.semver_key("0.2.1-rc.2") > dsh_release.semver_key("0.2.1-alpha.5")
    assert dsh_release.semver_key("0.2.1-alpha.5") > dsh_release.semver_key("0.2.0")
    assert dsh_release.semver_key("garbage") < dsh_release.semver_key("0.0.1")


def test_resolve_pin_is_offline(monkeypatch):
    monkeypatch.setattr(studio_mod, "DSH_VERSION", "1.2.3")

    def boom():  # pragma: no cover — must not be called
        raise AssertionError("network should not be touched for pin")

    release = dsh_release.resolve("pin", fetcher=boom)
    assert release.version == "1.2.3"
    assert release.source == "pin"


def test_resolve_none_defaults_to_pin():
    release = dsh_release.resolve(None)
    assert release.version == studio_mod.DSH_VERSION
    assert release.source == "pin"


def test_resolve_tags_and_newest():
    tags = {"latest": "0.2.0-rc.2", "next": "0.2.1-rc.9", "alpha": "0.2.1-alpha.5"}
    assert dsh_release.resolve("newest", fetcher=lambda: tags).version == "0.2.1-rc.9"
    assert dsh_release.resolve("alpha", fetcher=lambda: tags).version == "0.2.1-alpha.5"
    assert dsh_release.resolve("latest", fetcher=lambda: tags).version == "0.2.0-rc.2"

    newest = dsh_release.resolve("newest", fetcher=lambda: tags)
    assert newest.source == "newest"


def test_resolve_offline_falls_back_to_pin(monkeypatch):
    monkeypatch.setattr(studio_mod, "DSH_VERSION", "9.9.9")

    def boom():
        raise OSError("offline")

    release = dsh_release.resolve("newest", fetcher=boom)
    assert release.version == "9.9.9"
    assert release.source == "offline"
    assert "unreachable" in release.note


def test_resolve_newest_keeps_pin_when_registry_lags(monkeypatch):
    monkeypatch.setattr(studio_mod, "DSH_VERSION", "9.9.9")
    tags = {"latest": "0.2.0-rc.2", "next": "0.2.1-rc.2"}
    release = dsh_release.resolve("newest", fetcher=lambda: tags)
    assert release.version == "9.9.9"
    assert release.source == "pin"
    assert "registry newest" in release.note


def test_resolve_missing_tag_falls_back_to_pin(monkeypatch):
    monkeypatch.setattr(studio_mod, "DSH_VERSION", "1.0.0")
    release = dsh_release.resolve("alpha", fetcher=dict)
    assert release.version == "1.0.0"
    assert release.source == "offline"


def test_resolve_exact_version_never_fetches():
    def boom():  # pragma: no cover — must not be called
        raise AssertionError("network should not be touched for exact versions")

    release = dsh_release.resolve("1.2.3-rc.4", fetcher=boom)
    assert release.version == "1.2.3-rc.4"
    assert release.source == "exact"


def test_installed_version_reads_package_manifest(tmp_path):
    dsh_pkg = tmp_path / "node_modules" / "@deepseek-ai" / "dsh"
    binary = dsh_pkg / "bin" / "dsh"
    binary.parent.mkdir(parents=True)
    binary.write_text("#!/usr/bin/env node\n", encoding="utf-8")
    (dsh_pkg / "package.json").write_text(
        json.dumps({"name": "@deepseek-ai/dsh", "version": "0.7.1"}), encoding="utf-8"
    )
    assert dsh_release.installed_version(str(binary)) == "0.7.1"
    assert dsh_release.installed_version(None) is None
    assert dsh_release.installed_version(str(tmp_path / "nope")) is None


def test_installed_version_ignores_other_packages(tmp_path):
    pkg = tmp_path / "other"
    pkg.mkdir()
    (pkg / "package.json").write_text(
        json.dumps({"name": "not-dsh", "version": "1.0.0"}), encoding="utf-8"
    )
    assert dsh_release.installed_version(str(pkg / "binary")) is None


def test_install_command_and_has_update(monkeypatch):
    assert dsh_release.install_command("1.2.3") == [
        "npm",
        "install",
        "-g",
        "@deepseek-ai/dsh@1.2.3",
    ]

    pinned = dsh_release.Release("2.0.0", "pin")
    assert dsh_release.has_update(None, pinned) is True
    assert dsh_release.has_update("2.0.0", pinned) is False
    assert dsh_release.has_update("1.9.9", pinned) is True

    newest = dsh_release.Release("0.2.1-rc.2", "newest")
    assert dsh_release.has_update("0.2.1-alpha.5", newest) is True
    assert dsh_release.has_update("0.2.1-rc.2", newest) is False
    assert dsh_release.has_update("0.3.0", newest) is False


@pytest.mark.parametrize("value", ["", "   "])
def test_resolve_blank_spec_means_pin(value):
    assert dsh_release.resolve(value).source == "pin"
