"""Unit tests for scripts/release.py devtool."""

from __future__ import annotations

import json
from pathlib import Path

from scripts.release import (
    extract_version_notes,
    get_current_versions,
    normalize_version,
    update_file_versions,
)


def test_normalize_version():
    assert normalize_version("0.1.3") == "0.1.3"
    assert normalize_version("v0.1.3") == "0.1.3"
    assert normalize_version("1.0.0-rc.1") == "1.0.0-rc.1"
    assert normalize_version("v2.4.0") == "2.4.0"


def test_extract_version_notes():
    sample_changelog = """# Changelog

## [Unreleased]

### Added
- Feature A

### Fixed
- Bug B

## [0.1.2] - 2026-10-06

### Added
- Feature old
"""
    unreleased = extract_version_notes(sample_changelog, "unreleased")
    assert "Feature A" in unreleased
    assert "Bug B" in unreleased
    assert "Feature old" not in unreleased

    v012 = extract_version_notes(sample_changelog, "0.1.2")
    assert "Feature old" in v012
    assert "Feature A" not in v012


def test_get_current_versions():
    versions = get_current_versions()
    assert "root_package_json" in versions
    assert "web_package_json" in versions
    assert "helm_chart" in versions
    assert "root_pyproject" in versions
    # All manifests must be synchronized to the same valid semver version
    root_ver = versions["root_package_json"]
    assert len(root_ver.split(".")) == 3
    for k, v in versions.items():
        assert v == root_ver, f"Manifest {k} version {v} does not match root version {root_ver}"


def test_update_file_versions_dry_run(tmp_path: Path, monkeypatch):
    # Set up mock files in tmp_path
    pkg_json = tmp_path / "package.json"
    pkg_json.write_text(json.dumps({"name": "test", "version": "0.1.0"}))

    chart_yaml = tmp_path / "Chart.yaml"
    chart_yaml.write_text('version: 0.1.0\nappVersion: "0.1.0"\n')

    readme_md = tmp_path / "README.md"
    readme_md.write_text("helm upgrade --install rocket-chat ... --version 0.1.0 \\\n")

    monkeypatch.setattr("scripts.release.ROOT_PKG_JSON", pkg_json)
    monkeypatch.setattr("scripts.release.HELM_CHART_YAML", chart_yaml)
    monkeypatch.setattr("scripts.release.README_MD", readme_md)
    monkeypatch.setattr("scripts.release.WEB_PKG_JSON", tmp_path / "nonexistent.json")
    monkeypatch.setattr("scripts.release.HELM_VALUES_YAML", tmp_path / "nonexistent.yaml")
    monkeypatch.setattr("scripts.release.ROOT_PYPROJECT", tmp_path / "nonexistent.toml")
    monkeypatch.setattr("scripts.release.API_PYPROJECT", tmp_path / "nonexistent.toml")
    monkeypatch.setattr("scripts.release.API_MAIN_PY", tmp_path / "nonexistent.py")
    monkeypatch.setattr("scripts.release.PACKAGES_DIR", tmp_path / "nonexistent_dir")
    monkeypatch.setattr("scripts.release.HELM_DOC_MD", tmp_path / "nonexistent.md")
    monkeypatch.setattr("scripts.release.OPS_INDEX_MD", tmp_path / "nonexistent.md")

    update_file_versions("0.2.0")

    updated_pkg = json.loads(pkg_json.read_text())
    assert updated_pkg["version"] == "0.2.0"

    chart_content = chart_yaml.read_text()
    assert "version: 0.2.0" in chart_content
    assert 'appVersion: "0.2.0"' in chart_content

    readme_content = readme_md.read_text()
    assert "--version 0.2.0" in readme_content
