#!/usr/bin/env python3
"""Deterministic CycloneDX SBOM generator for Rocket Chat.

Produces a CycloneDX 1.5 JSON document from the repository's lockfiles so an
SBOM artifact can be generated **without** an external scanner (``syft``/
``trivy`` are an optional enhancement in ``release.yml``, not a build
requirement). Components come from three sources:

* Python distributions — ``uv.lock`` ``[[package]]`` entries;
* npm packages — the ``packages:`` keys of ``pnpm-lock.yaml`` (lockfile v9);
* container base images — the ``FROM`` lines of ``deploy/docker/Dockerfile.*``.

Output is deterministic: components are sorted by purl and the optional
timestamp is only emitted when ``SOURCE_DATE_EPOCH`` (or ``--timestamp``) is
provided, so the same inputs always hash to the same bytes. That property is
what makes the dry run in the delivery reproducible.

Usage::

    python3 scripts/generate_sbom.py --output sbom/rocket-chat-source.cdx.json
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import tomllib
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
UV_LOCK = REPO_ROOT / "uv.lock"
PNPM_LOCK = REPO_ROOT / "pnpm-lock.yaml"
PACKAGE_JSON = REPO_ROOT / "package.json"
DOCKERFILES = sorted((REPO_ROOT / "deploy" / "docker").glob("Dockerfile.*"))

# A fixed UUID namespace keeps the serialNumber stable across runs.
SBOM_NAMESPACE = uuid.uuid5(uuid.NAMESPACE_URL, "rocket-chat/sbom")
PNPM_PACKAGE_LINE = re.compile(r"^  (.+):$")
FROM_LINE = re.compile(r"^FROM\s+(\S+)(?:\s+AS\s+\S+)?\s*$", re.IGNORECASE)


def _read_project_version() -> str:
    try:
        return json.loads(PACKAGE_JSON.read_text(encoding="utf-8")).get("version", "0.0.0")
    except (OSError, json.JSONDecodeError):
        return "0.0.0"


def _python_components() -> list[dict[str, Any]]:
    if not UV_LOCK.exists():
        return []
    lock = tomllib.loads(UV_LOCK.read_text(encoding="utf-8"))
    components: list[dict[str, Any]] = []
    for package in lock.get("package", []):
        name = package.get("name")
        version = package.get("version")
        if not name or not version:
            continue
        purl = f"pkg:pypi/{name.lower().replace('_', '-')}@{version}"
        component: dict[str, Any] = {
            "type": "library",
            "name": name,
            "version": version,
            "purl": purl,
            "properties": [{"name": "rocket:ecosystem", "value": "python"}],
        }
        source = package.get("source", {})
        if isinstance(source, dict) and "editable" in source:
            component["properties"].append(
                {"name": "rocket:first_party", "value": str(source["editable"])}
            )
        components.append(component)
    return components


def _split_npm_key(key: str) -> tuple[str, str] | None:
    """Split a pnpm ``name@version(peers)`` key into ``(name, version)``."""
    base = key.split("(", 1)[0]
    if base.startswith("@"):
        scope, _, remainder = base[1:].partition("/")
        name, separator, version = remainder.rpartition("@")
        if not separator:
            return None
        return f"@{scope}/{name}", version
    name, separator, version = base.rpartition("@")
    if not separator:
        return None
    return name, version


def _npm_components() -> list[dict[str, Any]]:
    if not PNPM_LOCK.exists():
        return []
    lines = PNPM_LOCK.read_text(encoding="utf-8").splitlines()
    components: list[dict[str, Any]] = []
    seen: set[str] = set()
    in_packages = False
    for line in lines:
        if line == "packages:":
            in_packages = True
            continue
        if in_packages and line and not line.startswith(" "):
            break  # left the packages section (e.g. `snapshots:`)
        if not in_packages:
            continue
        match = PNPM_PACKAGE_LINE.match(line)
        if not match:
            continue
        raw = match.group(1).strip().strip("'\"")
        parsed = _split_npm_key(raw)
        if not parsed:
            continue
        name, version = parsed
        purl = f"pkg:npm/{name.replace('@', '%40')}@{version}"
        if purl in seen:
            continue
        seen.add(purl)
        components.append(
            {
                "type": "library",
                "name": name,
                "version": version,
                "purl": purl,
                "properties": [{"name": "rocket:ecosystem", "value": "npm"}],
            }
        )
    return components


def _image_components() -> list[dict[str, Any]]:
    components: list[dict[str, Any]] = []
    for dockerfile in DOCKERFILES:
        for line in dockerfile.read_text(encoding="utf-8").splitlines():
            match = FROM_LINE.match(line.strip())
            if not match:
                continue
            image = match.group(1)
            if "$" in image:
                continue  # build-arg driven image references cannot be pinned statically
            registry = ""
            reference = image
            if "/" in image and ("." in image.split("/")[0] or ":" in image.split("/")[0]):
                registry, _, reference = image.partition("/")
            name, _, tag = reference.rpartition(":")
            if not name:  # no tag present
                name, tag = reference, "latest"
            purl = f"pkg:docker/{name}@{tag}"
            if registry:
                purl += f"?repository_url={registry}"
            components.append(
                {
                    "type": "container",
                    "name": image,
                    "version": tag,
                    "purl": purl,
                    "properties": [
                        {"name": "rocket:source", "value": dockerfile.name},
                    ],
                }
            )
    return components


def build_sbom(timestamp: str | None) -> dict[str, Any]:
    version = _read_project_version()
    components = sorted(
        _python_components() + _npm_components() + _image_components(),
        key=lambda component: str(component["purl"]),
    )

    metadata: dict[str, Any] = {
        "component": {
            "type": "application",
            "name": "rocket-chat",
            "version": version,
            "purl": f"pkg:generic/rocket-chat@{version}",
        },
        "tools": {
            "components": [
                {
                    "type": "application",
                    "name": "rocket-chat-sbom-generator",
                    "version": "1.0.0",
                }
            ]
        },
    }
    if timestamp:
        metadata["timestamp"] = timestamp

    # A content hash in the serial number makes duplicate identifiers impossible while
    # keeping the document reproducible for identical inputs.
    digest = hashlib.sha256(json.dumps(components, sort_keys=True).encode("utf-8")).hexdigest()
    serial = uuid.uuid5(SBOM_NAMESPACE, digest)

    return {
        "bomFormat": "CycloneDX",
        "specVersion": "1.5",
        "serialNumber": f"urn:uuid:{serial}",
        "version": 1,
        "metadata": metadata,
        "components": components,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, help="Path to the CycloneDX JSON artifact.")
    parser.add_argument(
        "--timestamp",
        default=None,
        help="RFC3339 timestamp to embed; omit for a byte-reproducible document.",
    )
    args = parser.parse_args(argv)

    timestamp = args.timestamp
    if timestamp is None:
        import os

        epoch = os.environ.get("SOURCE_DATE_EPOCH")
        if epoch:
            timestamp = datetime.fromtimestamp(int(epoch), tz=UTC).isoformat()

    sbom = build_sbom(timestamp)
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(sbom, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    by_ecosystem: dict[str, int] = {}
    for component in sbom["components"]:
        ecosystem = next(
            (
                prop["value"]
                for prop in component["properties"]
                if prop["name"] == "rocket:ecosystem"
            ),
            "container",
        )
        by_ecosystem[ecosystem] = by_ecosystem.get(ecosystem, 0) + 1
    print(f"wrote {output} ({len(sbom['components'])} components: {by_ecosystem})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
