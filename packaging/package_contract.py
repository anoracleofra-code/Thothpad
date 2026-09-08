from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def _is_hex(value: Any, length: int) -> bool:
    return (
        isinstance(value, str)
        and len(value) == length
        and all(character in "0123456789abcdef" for character in value.casefold())
    )


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _component_sha256(component: dict[str, Any]) -> str | None:
    hashes = _list(component.get("hashes"))
    for item in hashes:
        if isinstance(item, dict) and item.get("alg") == "SHA-256" and _is_hex(item.get("content"), 64):
            return str(item["content"]).casefold()
    return None


def validate_candidate_contract(
    *,
    candidate_manifest: Path,
    sbom: Path,
    expected_commit: str,
) -> dict[str, Any]:
    if not _is_hex(expected_commit, 40):
        raise ValueError("expected commit must be a full hexadecimal Git hash")
    candidate = json.loads(candidate_manifest.read_text(encoding="utf-8"))
    bom = json.loads(sbom.read_text(encoding="utf-8"))
    provenance = candidate.get("provenance", {}) if isinstance(candidate, dict) else {}
    files = candidate.get("files", {}) if isinstance(candidate, dict) else {}
    components = _list(bom.get("components")) if isinstance(bom, dict) else []
    candidate_files_valid = isinstance(files, dict) and bool(files) and all(
        isinstance(path, str)
        and bool(path)
        and isinstance(value, dict)
        and _is_hex(value.get("sha256"), 64)
        and isinstance(value.get("size"), int)
        and not isinstance(value.get("size"), bool)
        and value.get("size", -1) >= 0
        for path, value in files.items()
    )
    sbom_files = {
        str(component.get("name")): _component_sha256(component)
        for component in components
        if isinstance(component, dict) and component.get("type") == "file" and isinstance(component.get("name"), str)
    }
    candidate_paths = set(files) if isinstance(files, dict) else set()
    bindable_paths = {path for path in candidate_paths if not path.casefold().endswith(".json")}
    gates = {
        "candidate_schema": candidate.get("schema_version") == 1,
        "candidate_commit_matches": provenance.get("source_commit") == expected_commit,
        "candidate_toolchain_hash": _is_hex(provenance.get("toolchain_lock_sha256"), 64),
        "candidate_files_valid": candidate_files_valid,
        "sbom_cyclonedx": bom.get("bomFormat") == "CycloneDX" and bom.get("specVersion") == "1.5",
        "sbom_has_components": isinstance(components, list) and len(components) >= 8,
        "sbom_has_application": isinstance(bom.get("metadata"), dict)
        and isinstance(bom["metadata"].get("component"), dict)
        and bom["metadata"]["component"].get("name") == "ThothPad",
        "sbom_covers_candidate_paths": candidate_files_valid and candidate_paths.issubset(sbom_files),
        # Reproducibility manifests intentionally hash normalized JSON, while
        # CycloneDX file components hash raw staged bytes. Cross-bind every
        # non-JSON staged file exactly; JSON path coverage is still required.
        "non_json_candidate_hashes_match_sbom": candidate_files_valid
        and bool(bindable_paths)
        and all(sbom_files.get(path) == str(files[path]["sha256"]).casefold() for path in bindable_paths),
    }
    return {
        "gates": gates,
        "all_green": all(gates.values()),
        "candidate_file_count": len(files) if isinstance(files, dict) else 0,
        "sbom_component_count": len(components) if isinstance(components, list) else 0,
    }
