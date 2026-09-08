from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def validate_candidate_contract(
    *,
    candidate_manifest: Path,
    sbom: Path,
    expected_commit: str,
) -> dict[str, Any]:
    if len(expected_commit) != 40:
        raise ValueError("expected commit must be a full Git hash")
    candidate = json.loads(candidate_manifest.read_text(encoding="utf-8"))
    bom = json.loads(sbom.read_text(encoding="utf-8"))
    provenance = candidate.get("provenance", {}) if isinstance(candidate, dict) else {}
    files = candidate.get("files", {}) if isinstance(candidate, dict) else {}
    components = bom.get("components", []) if isinstance(bom, dict) else []
    gates = {
        "candidate_schema": candidate.get("schema_version") == 1,
        "candidate_commit_matches": provenance.get("source_commit") == expected_commit,
        "candidate_has_files": isinstance(files, dict) and bool(files),
        "sbom_cyclonedx": bom.get("bomFormat") == "CycloneDX" and bom.get("specVersion") == "1.5",
        "sbom_has_components": isinstance(components, list) and len(components) >= 8,
        "sbom_has_application": isinstance(bom.get("metadata"), dict)
        and isinstance(bom["metadata"].get("component"), dict)
        and bom["metadata"]["component"].get("name") == "ThothPad",
    }
    return {
        "gates": gates,
        "all_green": all(gates.values()),
        "candidate_file_count": len(files) if isinstance(files, dict) else 0,
        "sbom_component_count": len(components) if isinstance(components, list) else 0,
    }
