#!/usr/bin/env python3
"""Generate a CycloneDX SBOM from a staged ThothPad tree."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import re
import uuid
import tomllib
from datetime import datetime, timezone
from email.parser import BytesParser
from pathlib import Path
from urllib.parse import quote


def digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(chunk)
    return value.hexdigest()


def license_entry(value: str) -> dict:
    value = value.strip() or "NOASSERTION"
    if re.fullmatch(r"[A-Za-z0-9.+-]+", value):
        return {"license": {"id": value}}
    return {"license": {"name": value}}


def declared_component(value: str) -> dict:
    parts = value.split("|", 3)
    if len(parts) != 4 or not all(parts):
        raise argparse.ArgumentTypeError(
            "components use name|version|license|purl"
        )
    name, version, license_name, purl = parts
    return {
        "type": "library",
        "bom-ref": purl,
        "name": name,
        "version": version,
        "purl": purl,
        "licenses": [license_entry(license_name)],
    }


def python_components(root: Path) -> list[dict]:
    components: dict[str, dict] = {}
    for metadata_path in root.rglob("*.dist-info/METADATA"):
        metadata = BytesParser().parsebytes(metadata_path.read_bytes())
        name = str(metadata.get("Name") or metadata_path.parent.name.split("-")[0])
        version = str(metadata.get("Version") or "unknown")
        normalized = re.sub(r"[-_.]+", "-", name).lower()
        purl = f"pkg:pypi/{quote(normalized)}@{quote(version)}"
        license_name = str(
            metadata.get("License-Expression") or metadata.get("License") or "NOASSERTION"
        )
        components[purl] = {
            "type": "library",
            "bom-ref": purl,
            "name": name,
            "version": version,
            "purl": purl,
            "licenses": [license_entry(license_name)],
            "properties": [{"name": "thothpad:source", "value": "python-dist-info"}],
        }
    return sorted(components.values(), key=lambda item: item["purl"])


def locked_python_components(lock_path: Path) -> list[dict]:
    document = tomllib.loads(lock_path.read_text(encoding="utf-8"))
    components: dict[str, dict] = {}
    for package in document.get("package", []):
        name = str(package.get("name", "")).strip()
        version = str(package.get("version", "")).strip()
        if not name or not version:
            continue
        normalized = re.sub(r"[-_.]+", "-", name).lower()
        purl = f"pkg:pypi/{quote(normalized)}@{quote(version)}"
        hashes: set[str] = set()
        sdist = package.get("sdist")
        if isinstance(sdist, dict) and str(sdist.get("hash", "")).startswith("sha256:"):
            hashes.add(str(sdist["hash"]).split(":", 1)[1])
        for wheel in package.get("wheels", []):
            if isinstance(wheel, dict) and str(wheel.get("hash", "")).startswith("sha256:"):
                hashes.add(str(wheel["hash"]).split(":", 1)[1])
        component = {
            "type": "library",
            "bom-ref": purl,
            "name": name,
            "version": version,
            "purl": purl,
            "licenses": [license_entry("NOASSERTION")],
            "properties": [{"name": "thothpad:source", "value": "uv-lock"}],
        }
        if hashes:
            component["hashes"] = [
                {"alg": "SHA-256", "content": value}
                for value in sorted(hashes)
            ]
        components[purl] = component
    return sorted(components.values(), key=lambda item: item["purl"])


def locked_cargo_components(lock_path: Path) -> list[dict]:
    document = tomllib.loads(lock_path.read_text(encoding="utf-8"))
    components: dict[str, dict] = {}
    for package in document.get("package", []):
        name = str(package.get("name", "")).strip()
        version = str(package.get("version", "")).strip()
        if not name or not version:
            continue
        purl = f"pkg:cargo/{quote(name)}@{quote(version)}"
        component = {
            "type": "library",
            "bom-ref": purl,
            "name": name,
            "version": version,
            "purl": purl,
            "licenses": [license_entry("NOASSERTION")],
            "properties": [{"name": "thothpad:source", "value": "cargo-lock"}],
        }
        checksum = str(package.get("checksum", "")).strip().lower()
        if re.fullmatch(r"[0-9a-f]{64}", checksum):
            component["hashes"] = [{"alg": "SHA-256", "content": checksum}]
        source = str(package.get("source", "")).strip()
        if source:
            component["properties"].append(
                {"name": "thothpad:cargo-source", "value": source}
            )
        components[purl] = component
    return sorted(components.values(), key=lambda item: item["purl"])


def is_native_library(path: Path) -> bool:
    name = path.name.lower()
    return (
        name.endswith((".dll", ".dylib", ".so"))
        or ".so." in name
        or ".framework/" in path.as_posix().lower()
    )


def file_components(root: Path, output: Path) -> list[dict]:
    components = []
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        if path.resolve() == output.resolve():
            continue
        relative = path.relative_to(root).as_posix()
        properties = [
            {"name": "thothpad:file-size", "value": str(path.stat().st_size)},
            {"name": "thothpad:source", "value": "staged-file"},
        ]
        if is_native_library(Path(relative)):
            properties.append({"name": "thothpad:native-library", "value": "true"})
        components.append({
            "type": "file",
            "bom-ref": f"file:{relative}",
            "name": relative,
            "hashes": [{"alg": "SHA-256", "content": digest(path)}],
            "properties": properties,
        })
    return components


def build_timestamp() -> datetime:
    epoch = os.getenv("SOURCE_DATE_EPOCH")
    if epoch:
        try:
            return datetime.fromtimestamp(int(epoch), tz=timezone.utc)
        except (ValueError, OSError, OverflowError) as error:
            raise SystemExit("SOURCE_DATE_EPOCH must be a valid Unix timestamp") from error
    return datetime.now(timezone.utc)


def lock_component(path: Path, role: str) -> dict:
    return {
        "type": "file",
        "bom-ref": f"build-lock:{role}",
        "name": path.name,
        "hashes": [{"alg": "SHA-256", "content": digest(path)}],
        "properties": [
            {"name": "thothpad:source", "value": "build-lock"},
            {"name": "thothpad:lock-role", "value": role},
        ],
    }


def merge_components(groups: list[list[dict]]) -> list[dict]:
    merged: dict[str, dict] = {}
    for group in groups:
        for component in group:
            reference = component["bom-ref"]
            if reference not in merged:
                merged[reference] = component
                continue
            current = merged[reference]
            for key in ("hashes", "externalReferences", "properties"):
                if key in component:
                    values = current.setdefault(key, [])
                    for value in component[key]:
                        if value not in values:
                            values.append(value)
            current_licenses = current.get("licenses", [])
            if current_licenses == [license_entry("NOASSERTION")] and component.get("licenses"):
                current["licenses"] = component["licenses"]
    return sorted(merged.values(), key=lambda item: item["bom-ref"])


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--app-version", required=True)
    parser.add_argument("--lock", type=Path)
    parser.add_argument("--cargo-lock", type=Path)
    parser.add_argument("--toolchain-lock", type=Path)
    parser.add_argument("--component", action="append", default=[], type=declared_component)
    args = parser.parse_args()

    root = args.root.resolve()
    output = args.output.resolve()
    if not root.is_dir():
        parser.error(f"staged root does not exist: {root}")
    output.parent.mkdir(parents=True, exist_ok=True)

    app_ref = f"pkg:generic/thothpad@{quote(args.app_version)}"
    python_ref = f"pkg:generic/python@{platform.python_version()}"
    dependencies = [python_ref]
    dependencies.extend(item["bom-ref"] for item in args.component)
    python_packages_by_ref = {
        item["bom-ref"]: item for item in python_components(root)
    }
    if args.lock:
        lock_path = args.lock.resolve()
        if not lock_path.is_file():
            parser.error(f"Python lock file does not exist: {lock_path}")
        python_packages_by_ref.update(
            {item["bom-ref"]: item for item in locked_python_components(lock_path)}
        )
    python_packages = sorted(python_packages_by_ref.values(), key=lambda item: item["purl"])
    dependencies.extend(item["bom-ref"] for item in python_packages)
    cargo_packages: list[dict] = []
    if args.cargo_lock:
        cargo_lock_path = args.cargo_lock.resolve()
        if not cargo_lock_path.is_file():
            parser.error(f"Cargo lock file does not exist: {cargo_lock_path}")
        cargo_packages = locked_cargo_components(cargo_lock_path)
        dependencies.extend(item["bom-ref"] for item in cargo_packages)
    build_locks = []
    for role, path in (("python", args.lock), ("cargo", args.cargo_lock), ("toolchain", args.toolchain_lock)):
        if path:
            resolved = path.resolve()
            if not resolved.is_file():
                parser.error(f"{role} lock file does not exist: {resolved}")
            component = lock_component(resolved, role)
            build_locks.append(component)
            dependencies.append(component["bom-ref"])
    generated_at = build_timestamp().replace(microsecond=0)
    staged_files = file_components(root, output)
    tree_identity = hashlib.sha256(json.dumps(
        [(item["bom-ref"], item["hashes"][0]["content"]) for item in staged_files],
        separators=(",", ":"),
    ).encode("utf-8")).hexdigest()
    serial_seed = f"thothpad:{args.app_version}:{generated_at.isoformat()}:{tree_identity}"
    locked_components = merge_components([
        args.component,
        python_packages,
        cargo_packages,
        build_locks,
    ])
    components = [
        {
            "type": "platform",
            "bom-ref": python_ref,
            "name": "Python",
            "version": platform.python_version(),
            "purl": python_ref,
            "licenses": [license_entry("PSF-2.0")],
        },
        *locked_components,
        *staged_files,
    ]
    document = {
        "bomFormat": "CycloneDX",
        "specVersion": "1.5",
        "serialNumber": f"urn:uuid:{uuid.uuid5(uuid.NAMESPACE_URL, serial_seed)}",
        "version": 1,
        "metadata": {
            "timestamp": generated_at.isoformat(),
            "component": {
                "type": "application",
                "bom-ref": app_ref,
                "name": "ThothPad",
                "version": args.app_version,
                "purl": app_ref,
                "licenses": [license_entry("GPL-3.0-or-later")],
            },
        },
        "components": components,
        "dependencies": [{"ref": app_ref, "dependsOn": sorted(set(dependencies))}],
    }
    output.write_text(json.dumps(document, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
