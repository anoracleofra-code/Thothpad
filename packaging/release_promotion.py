from __future__ import annotations

from typing import Any

from release_evidence import (
    evidence_bundle_manifest,
    validate_benchmark_evidence,
    validate_clean_install_evidence,
    validate_independent_reviews,
    validate_linux_runtime_evidence,
    validate_macos_release_evidence,
    validate_reproducibility_evidence,
    validate_update_rollback_evidence,
    validate_windows_signing_evidence,
)


def production_promotion_readiness(evidence: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(evidence, dict):
        raise TypeError("release promotion evidence must be an object")
    sections = {
        "benchmark": validate_benchmark_evidence(evidence.get("benchmark", {})),
        "windows_clean_install": validate_clean_install_evidence(evidence.get("windows_clean_install", {})),
        "windows_signing": validate_windows_signing_evidence(evidence.get("windows_signing", {})),
        "linux_runtime": validate_linux_runtime_evidence(evidence.get("linux_runtime", {})),
        "macos_release": validate_macos_release_evidence(evidence.get("macos_release", {})),
        "independent_reviews": validate_independent_reviews(evidence.get("independent_reviews", {})),
        "reproducibility": validate_reproducibility_evidence(evidence.get("reproducibility", {})),
        "update_rollback": validate_update_rollback_evidence(evidence.get("update_rollback", {})),
    }
    gates = {name: bool(section.get("all_green")) for name, section in sections.items()}
    return {
        "sections": sections,
        "gates": gates,
        "production_promotion_ready": all(gates.values()),
        "missing_or_failed": [name for name, passed in gates.items() if not passed],
        "fail_closed": True,
        "interpretation_limit": (
            "This contract does not create signing, notarization, clean-install, platform-runtime, benchmark, "
            "or independent-review evidence. Missing evidence remains a hard production-promotion blocker."
        ),
    }


def release_evidence_portability(root: str) -> dict[str, Any]:
    manifest = evidence_bundle_manifest(root)
    absolute_paths = [item["path"] for item in manifest["files"] if item["path"].startswith(("/", "\\"))]
    parent_traversal = [item["path"] for item in manifest["files"] if ".." in item["path"].split("/")]
    return {
        "manifest": manifest,
        "absolute_paths": absolute_paths,
        "parent_traversal": parent_traversal,
        "portable": not absolute_paths and not parent_traversal,
    }
