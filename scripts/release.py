#!/usr/bin/env python3
"""
Release and Version Management Devtool for Rocket Chat.

Supports the Keep a Changelog + Semantic Versioning pattern with targeted
component breakdowns for GitHub Release notes, container descriptions, and Helm charts.
Handles end-to-end release preparation:
- Bumps manifests across Node (package.json, apps/web/package.json), Helm (Chart.yaml, values.yaml),
  Python (root pyproject.toml, apps/api/pyproject.toml, apps/api/src/api/main.py, packages/*/pyproject.toml).
- Patches documentation & deployment files containing version references (README.md, doc/deployment-and-ops/helm-chart.md,
  doc/deployment-and-ops/index.md, etc.).
- Promotes [Unreleased] changelog notes to [version] with current ISO date and resets [Unreleased].
- Generates RELEASE_NOTES.md.
- Synchronizes Python lockfile (uv sync) and Node lockfile (pnpm install).
- Runs automated preflight quality gates (ruff, mypy, pytest, pnpm lint/type-check, helm lint, docs:check).
- Optionally creates a release branch, git commit, git push, and GitHub PR via --create-pr.

Usage:
    python3 scripts/release.py prepare <version> [--skip-tests] [--create-pr]
    python3 scripts/release.py notes <version> [--output <file>]
    python3 scripts/release.py check
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
from datetime import date
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
CHANGELOG_PATH = ROOT_DIR / "CHANGELOG.md"
ROOT_PKG_JSON = ROOT_DIR / "package.json"
ROOT_PYPROJECT = ROOT_DIR / "pyproject.toml"
WEB_PKG_JSON = ROOT_DIR / "apps" / "web" / "package.json"
API_PYPROJECT = ROOT_DIR / "apps" / "api" / "pyproject.toml"
API_MAIN_PY = ROOT_DIR / "apps" / "api" / "src" / "api" / "main.py"
HELM_CHART_YAML = ROOT_DIR / "deploy" / "helm" / "platform" / "Chart.yaml"
HELM_VALUES_YAML = ROOT_DIR / "deploy" / "helm" / "platform" / "values.yaml"
PACKAGES_DIR = ROOT_DIR / "packages"
README_MD = ROOT_DIR / "README.md"
HELM_DOC_MD = ROOT_DIR / "doc" / "deployment-and-ops" / "helm-chart.md"
OPS_INDEX_MD = ROOT_DIR / "doc" / "deployment-and-ops" / "index.md"

SEMVER_REGEX = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)(?:-([0-9A-Za-z.-]+))?$")

UNRELEASED_TEMPLATE = """## [Unreleased]

### Added
- `orchestrator`:
- `sandboxes`:
- `api`:
- `web`:
- `settings`:
- `git`:
- `helm`:

### Changed

### Fixed

### Security
"""


def normalize_version(raw_version: str) -> str:
    """Normalize version string by stripping leading 'v' and validating SemVer."""
    match = SEMVER_REGEX.match(raw_version.strip())
    if not match:
        raise ValueError(
            f"Invalid Semantic Version '{raw_version}'. Expected format: X.Y.Z or vX.Y.Z (e.g. 0.2.0, 1.0.0)"
        )
    core = f"{match.group(1)}.{match.group(2)}.{match.group(3)}"
    if match.group(4):
        core = f"{core}-{match.group(4)}"
    return core


def get_current_versions() -> dict[str, str]:
    """Read version numbers across manifests."""
    versions: dict[str, str] = {}
    if ROOT_PKG_JSON.exists():
        data = json.loads(ROOT_PKG_JSON.read_text(encoding="utf-8"))
        versions["root_package_json"] = data.get("version", "unknown")

    if WEB_PKG_JSON.exists():
        data = json.loads(WEB_PKG_JSON.read_text(encoding="utf-8"))
        versions["web_package_json"] = data.get("version", "unknown")

    if HELM_CHART_YAML.exists():
        content = HELM_CHART_YAML.read_text(encoding="utf-8")
        v_match = re.search(r"^version:\s*([^\s]+)", content, re.MULTILINE)
        versions["helm_chart"] = v_match.group(1) if v_match else "unknown"

    if ROOT_PYPROJECT.exists():
        content = ROOT_PYPROJECT.read_text(encoding="utf-8")
        v_match = re.search(r'^version\s*=\s*"([^"]+)"', content, re.MULTILINE)
        if v_match:
            versions["root_pyproject"] = v_match.group(1)

    if API_PYPROJECT.exists():
        content = API_PYPROJECT.read_text(encoding="utf-8")
        v_match = re.search(r'^version\s*=\s*"([^"]+)"', content, re.MULTILINE)
        if v_match:
            versions["api_pyproject"] = v_match.group(1)

    if PACKAGES_DIR.exists():
        for pkg_pyproj in sorted(PACKAGES_DIR.glob("*/pyproject.toml")):
            content = pkg_pyproj.read_text(encoding="utf-8")
            v_match = re.search(r'^version\s*=\s*"([^"]+)"', content, re.MULTILINE)
            if v_match:
                versions[f"pkg_{pkg_pyproj.parent.name}"] = v_match.group(1)

    return versions


def update_file_versions(version: str) -> None:
    """Update version strings across all package manifests and documentation references."""
    # 1. Root package.json
    if ROOT_PKG_JSON.exists():
        data = json.loads(ROOT_PKG_JSON.read_text(encoding="utf-8"))
        data["version"] = version
        ROOT_PKG_JSON.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")

    # 2. apps/web/package.json
    if WEB_PKG_JSON.exists():
        data = json.loads(WEB_PKG_JSON.read_text(encoding="utf-8"))
        data["version"] = version
        WEB_PKG_JSON.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")

    # 3. deploy/helm/platform/Chart.yaml
    if HELM_CHART_YAML.exists():
        content = HELM_CHART_YAML.read_text(encoding="utf-8")
        content = re.sub(
            r"^version:\s*([^\s]+)",
            f"version: {version}",
            content,
            flags=re.MULTILINE,
        )
        content = re.sub(
            r'^appVersion:\s*"?([^"\s]+)"?',
            f'appVersion: "{version}"',
            content,
            flags=re.MULTILINE,
        )
        HELM_CHART_YAML.write_text(content, encoding="utf-8")

    # 4. deploy/helm/platform/values.yaml tags
    if HELM_VALUES_YAML.exists():
        content = HELM_VALUES_YAML.read_text(encoding="utf-8")
        content = re.sub(
            r'tag:\s*"[0-9A-Za-z.-]+"',
            f'tag: "{version}"',
            content,
        )
        HELM_VALUES_YAML.write_text(content, encoding="utf-8")

    # 5. Root pyproject.toml
    if ROOT_PYPROJECT.exists():
        content = ROOT_PYPROJECT.read_text(encoding="utf-8")
        content = re.sub(
            r'^version\s*=\s*"[^"]+"', f'version = "{version}"', content, flags=re.MULTILINE
        )
        ROOT_PYPROJECT.write_text(content, encoding="utf-8")

    # 6. apps/api/pyproject.toml and main.py
    if API_PYPROJECT.exists():
        content = API_PYPROJECT.read_text(encoding="utf-8")
        content = re.sub(
            r'^version\s*=\s*"[^"]+"', f'version = "{version}"', content, flags=re.MULTILINE
        )
        API_PYPROJECT.write_text(content, encoding="utf-8")

    if API_MAIN_PY.exists():
        content = API_MAIN_PY.read_text(encoding="utf-8")
        content = re.sub(r'version="[^"]+"', f'version="{version}"', content)
        API_MAIN_PY.write_text(content, encoding="utf-8")

    # 7. packages/*/pyproject.toml
    if PACKAGES_DIR.exists():
        for pkg_pyproj in PACKAGES_DIR.glob("*/pyproject.toml"):
            content = pkg_pyproj.read_text(encoding="utf-8")
            content = re.sub(
                r'^version\s*=\s*"[^"]+"', f'version = "{version}"', content, flags=re.MULTILINE
            )
            pkg_pyproj.write_text(content, encoding="utf-8")

    # 8. README.md Helm & container versions
    if README_MD.exists():
        content = README_MD.read_text(encoding="utf-8")
        content = re.sub(
            r"(--version\s+)[0-9A-Za-z.-]+",
            rf"\g<1>{version}",
            content,
        )
        README_MD.write_text(content, encoding="utf-8")

    # 9. doc/deployment-and-ops/helm-chart.md
    if HELM_DOC_MD.exists():
        content = HELM_DOC_MD.read_text(encoding="utf-8")
        content = re.sub(
            r"(--version\s+)[0-9A-Za-z.-]+",
            rf"\g<1>{version}",
            content,
        )
        HELM_DOC_MD.write_text(content, encoding="utf-8")

    # 10. doc/deployment-and-ops/index.md
    if OPS_INDEX_MD.exists():
        content = OPS_INDEX_MD.read_text(encoding="utf-8")
        content = re.sub(
            r"(oci://ghcr\.io/rocket-chat/charts/rocket-chat:)[0-9A-Za-z.-]+",
            rf"\g<1>{version}",
            content,
        )
        OPS_INDEX_MD.write_text(content, encoding="utf-8")


def sync_lockfiles() -> None:
    """Synchronize python uv.lock and node pnpm-lock.yaml after manifest updates."""
    print(" Synchronizing lockfiles (uv & pnpm)...")
    if shutil.which("uv"):
        try:
            subprocess.run(["uv", "sync"], cwd=ROOT_DIR, check=True, capture_output=True, text=True)
            print("   uv.lock synchronized.")
        except subprocess.CalledProcessError as e:
            print(f"  Warning: uv sync failed: {e.stderr}", file=sys.stderr)
    else:
        print("  Warning: uv not found on PATH, skipping uv sync.", file=sys.stderr)

    if shutil.which("pnpm"):
        try:
            subprocess.run(
                ["pnpm", "install", "--no-frozen-lockfile"],
                cwd=ROOT_DIR,
                check=True,
                capture_output=True,
                text=True,
            )
            print("   pnpm-lock.yaml synchronized.")
        except subprocess.CalledProcessError as e:
            print(f"  Warning: pnpm install failed: {e.stderr}", file=sys.stderr)


def run_command_with_status(cmd: list[str], label: str) -> None:
    """Run a shell command with real-time feedback and clean error handling."""
    print(f" Running quality gate: {label}...")
    proc = subprocess.run(cmd, cwd=ROOT_DIR, capture_output=True, text=True)
    if proc.returncode != 0:
        print(f"\n❌ Quality gate failed: {label}\nCommand: {' '.join(cmd)}", file=sys.stderr)
        if proc.stdout:
            print(proc.stdout, file=sys.stderr)
        if proc.stderr:
            print(proc.stderr, file=sys.stderr)
        sys.exit(1)
    print(f"   {label} passed.")


def run_quality_gates() -> None:
    """Execute standard repository quality gates as defined in AGENTS.md."""
    print("\n Executing preflight quality gates...")
    run_command_with_status(["helm", "lint", "deploy/helm/platform"], "Helm Lint")
    run_command_with_status(["pnpm", "run", "docs:check"], "Docs Link Check")
    run_command_with_status(["pnpm", "--filter", "web", "lint"], "Web Lint")
    run_command_with_status(["pnpm", "--filter", "web", "type-check"], "Web Type Check")
    run_command_with_status(["uv", "run", "ruff", "check", "."], "Ruff Check")
    run_command_with_status(["uv", "run", "ruff", "format", "--check", "."], "Ruff Format Check")
    run_command_with_status(["uv", "run", "mypy", "packages", "apps/api"], "Mypy Type Check")
    run_command_with_status(["uv", "run", "pytest", "tests/"], "Pytest Test Suite")
    print(" All preflight quality gates passed successfully!\n")


def extract_version_notes(changelog_text: str, target_version: str) -> str:
    """Extract changelog notes for a specific version or unreleased."""
    target_clean = (
        normalize_version(target_version)
        if target_version.lower() != "unreleased"
        else "unreleased"
    )

    if target_clean == "unreleased":
        pattern = r"##\s*\[Unreleased\](.*?)(?=\n##\s*\[|\Z)"
    else:
        # Match e.g. ## [0.2.0] or ## [v1.0.0]
        pattern = rf"##\s*\[v?{re.escape(target_clean)}\](.*?)(?=\n##\s*\[|\Z)"

    match = re.search(pattern, changelog_text, re.DOTALL | re.IGNORECASE)
    if not match:
        raise ValueError(f"Could not find section for '{target_version}' in CHANGELOG.md")

    return match.group(1).strip()


def build_release_body(version: str, notes: str) -> str:
    """Format release notes for GitHub Releases and GHCR image descriptions."""
    today = date.today().isoformat()
    return f"""# Rocket Chat v{version} ({today})

{notes}

---

### Deployment Artifacts & Container Images

* **Backend Image (Multi-Arch `linux/amd64`, `linux/arm64`)**:
  ```bash
  docker pull ghcr.io/rocket-chat/backend:v{version}
  ```

* **Frontend Web Cockpit (`linux/amd64`, `linux/arm64`)**:
  ```bash
  docker pull ghcr.io/rocket-chat/frontend:v{version}
  ```

* **Kubernetes Helm Chart (OCI Registry)**:
  ```bash
  helm install rocket-chat oci://ghcr.io/rocket-chat/charts/rocket-chat --version {version}
  ```
"""


def cmd_prepare(
    version_arg: str,
    skip_tests: bool = False,
    create_pr: bool = False,
) -> None:
    version = normalize_version(version_arg)
    today = date.today().isoformat()

    if not CHANGELOG_PATH.exists():
        sys.exit(f"Error: {CHANGELOG_PATH} not found.")

    changelog_content = CHANGELOG_PATH.read_text(encoding="utf-8")

    # Extract unreleased notes
    try:
        unreleased_notes = extract_version_notes(changelog_content, "unreleased")
    except ValueError as e:
        sys.exit(f"Error: {e}")

    # Remove template placeholders that have no content (e.g. "- `component`: \n")
    cleaned_notes = []
    for line in unreleased_notes.splitlines():
        # Keep heading lines, bullet points with actual text, or empty lines
        if line.startswith("- `") and line.endswith(":"):
            continue
        cleaned_notes.append(line)
    notes_text = "\n".join(cleaned_notes).strip()

    if not notes_text:
        notes_text = f"Automated release of Rocket Chat version v{version}."

    # Build new version section
    new_version_section = f"## [{version}] - {today}\n\n{notes_text}\n"

    # Replace [Unreleased] with fresh template + the new version section
    pattern = r"##\s*\[Unreleased\](.*?)(?=\n##\s*\[|\Z)"
    replacement = f"{UNRELEASED_TEMPLATE}\n{new_version_section}"
    updated_changelog = re.sub(pattern, replacement, changelog_content, count=1, flags=re.DOTALL)

    CHANGELOG_PATH.write_text(updated_changelog, encoding="utf-8")
    update_file_versions(version)
    sync_lockfiles()

    # Write standalone RELEASE_NOTES.md
    release_body = build_release_body(version, notes_text)
    release_notes_file = ROOT_DIR / "RELEASE_NOTES.md"
    release_notes_file.write_text(release_body, encoding="utf-8")

    print(f" Successfully prepared Rocket Chat version v{version}!")
    print(f" Updated {CHANGELOG_PATH.name} (moved [Unreleased] -> [{version}] - {today})")
    print(f" Updated package manifests and documentation references to {version}")
    print(f" Generated {release_notes_file.name} for GitHub Release & GHCR descriptions\n")

    # Verification consistency check
    cmd_check()

    if not skip_tests:
        run_quality_gates()
    else:
        print("⚡ Skipping quality gate execution (--skip-tests).")

    if create_pr:
        create_release_pr(version, notes_text)
    else:
        print("\nNext steps:")
        print("  1. Review changes: git diff")
        print(f"  2. Commit release: git commit -am 'chore(release): prepare v{version}'")
        print(f"  3. Tag version:    git tag -a v{version} -m 'Release v{version}'")
        print("  4. Push to origin: git push origin main --tags")


def create_release_pr(version: str, notes_text: str) -> None:
    """Create a dedicated release branch, commit changes, push, and open a GitHub PR."""
    if not shutil.which("git") or not shutil.which("gh"):
        sys.exit("Error: Both 'git' and 'gh' CLI tools must be installed to use --create-pr.")

    branch_name = f"release/v{version}"
    commit_msg = f"chore(release): prepare v{version}"

    print(f"\n Automated PR creation enabled for v{version}:")
    print(f"   Creating branch: {branch_name}")
    subprocess.run(["git", "checkout", "-B", branch_name], cwd=ROOT_DIR, check=True)
    subprocess.run(["git", "add", "-A"], cwd=ROOT_DIR, check=True)
    subprocess.run(["git", "commit", "-m", commit_msg], cwd=ROOT_DIR, check=True)
    subprocess.run(["git", "push", "-u", "origin", branch_name], cwd=ROOT_DIR, check=True)

    pr_title = f"chore(release): prepare v{version}"
    pr_body = f"## Release v{version} ({date.today().isoformat()})\n\n{notes_text}\n\n---\n*Automated release preparation generated by `scripts/release.py prepare {version} --create-pr`.*"

    print("   Opening GitHub Pull Request...")
    pr_cmd = [
        "gh",
        "pr",
        "create",
        "--title",
        pr_title,
        "--body",
        pr_body,
        "--base",
        "main",
        "--head",
        branch_name,
    ]
    result = subprocess.run(pr_cmd, cwd=ROOT_DIR, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"Warning: gh pr create returned non-zero code:\n{result.stderr}", file=sys.stderr)
    else:
        print(f"   Pull request created successfully:\n{result.stdout.strip()}")


def cmd_notes(version_arg: str, output_path: str | None = None) -> None:
    if not CHANGELOG_PATH.exists():
        sys.exit(f"Error: {CHANGELOG_PATH} not found.")

    changelog_content = CHANGELOG_PATH.read_text(encoding="utf-8")
    try:
        notes = extract_version_notes(changelog_content, version_arg)
    except ValueError as e:
        sys.exit(f"Error: {e}")

    version_clean = (
        normalize_version(version_arg) if version_arg.lower() != "unreleased" else "unreleased"
    )
    body = build_release_body(version_clean, notes) if version_clean != "unreleased" else notes

    if output_path:
        out_file = Path(output_path)
        out_file.write_text(body, encoding="utf-8")
        print(f"Wrote release notes for '{version_arg}' to {output_path}")
    else:
        print(body)


def cmd_check() -> None:
    versions = get_current_versions()
    print("Rocket Chat Manifest Versions:")
    for manifest, ver in versions.items():
        print(f"  - {manifest}: {ver}")

    unique = set(versions.values())
    if len(unique) > 1:
        print("\n WARNING: Version mismatch detected across manifests!", file=sys.stderr)
        sys.exit(1)
    else:
        print(f"\n All components synchronized at version {next(iter(unique))}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Rocket Chat Release & Version Management Devtool")
    subparsers = parser.add_subparsers(dest="command", required=True)

    # prepare <version> [--skip-tests] [--create-pr]
    prep_parser = subparsers.add_parser("prepare", help="Prepare a new release version")
    prep_parser.add_argument("version", help="New semantic version (e.g. 0.2.0 or v0.2.0)")
    prep_parser.add_argument(
        "--skip-tests",
        action="store_true",
        help="Skip executing preflight quality gates (ruff, mypy, pytest, pnpm, helm lint)",
    )
    prep_parser.add_argument(
        "--create-pr",
        action="store_true",
        help="Automatically create a release branch, git commit, push, and open a GitHub PR via gh CLI",
    )

    # notes <version> [--output <file>]
    notes_parser = subparsers.add_parser("notes", help="Extract release notes for a version")
    notes_parser.add_argument("version", help="Version to extract (e.g. 0.2.0 or unreleased)")
    notes_parser.add_argument("--output", "-o", help="Target output file path", default=None)

    # check
    subparsers.add_parser("check", help="Verify version consistency across all manifests")

    args = parser.parse_args()

    if args.command == "prepare":
        cmd_prepare(args.version, skip_tests=args.skip_tests, create_pr=args.create_pr)
    elif args.command == "notes":
        cmd_notes(args.version, args.output)
    elif args.command == "check":
        cmd_check()


if __name__ == "__main__":
    main()
