from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def canonical_sha256(value: Any) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def build_attestation(
    *,
    source_commit: str,
    toolchain_lock: Path,
    sbom: Path,
    candidate_manifest: Path,
    test_evidence: dict[str, Any],
) -> dict[str, Any]:
    if len(source_commit) != 40 or any(ch not in "0123456789abcdef" for ch in source_commit.casefold()):
        raise ValueError("source_commit must be a 40-character hexadecimal commit")
    evidence = {
        "schema_version": 1,
        "source_commit": source_commit.casefold(),
        "toolchain_lock_sha256": file_sha256(toolchain_lock),
        "sbom_sha256": file_sha256(sbom),
        "candidate_manifest_sha256": file_sha256(candidate_manifest),
        "test_evidence": dict(test_evidence),
    }
    evidence["attestation_sha256"] = canonical_sha256(evidence)
    return evidence
