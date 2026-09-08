from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

REVIEW_CATEGORIES = (
    "architecture",
    "analyzer_quality",
    "ux_accessibility",
    "security_privacy",
    "performance_reliability",
    "packaging",
)

REPRODUCIBILITY_TARGETS = (
    "windows-core",
    "windows-full",
    "linux-core",
    "linux-full",
    "macos-core",
    "macos-full",
)


def _is_sha256(value: Any) -> bool:
    return isinstance(value, str) and len(value) == 64 and all(ch in "0123456789abcdef" for ch in value.lower())


def validate_benchmark_evidence(evidence: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(evidence, dict):
        raise TypeError("benchmark evidence must be an object")
    results = evidence.get("results")
    cases = results if isinstance(results, list) else []
    gates = {
        "benchmark_named": evidence.get("benchmark") == "writer-engine-analysis",
        "python_recorded": isinstance(evidence.get("python"), str) and bool(evidence.get("python")),
        "platform_recorded": isinstance(evidence.get("platform"), str) and bool(evidence.get("platform")),
        "cases_present": bool(cases),
        "case_hashes_valid": bool(cases)
        and all(_is_sha256(item.get("sha256")) for item in cases if isinstance(item, dict)),
        "timings_present": bool(cases)
        and all(
            isinstance(item.get("milliseconds"), dict)
            and all(isinstance(item["milliseconds"].get(key), (int, float)) for key in ("p50", "p95", "maximum"))
            for item in cases
            if isinstance(item, dict)
        ),
    }
    return {"gates": gates, "all_green": all(gates.values()), "case_count": len(cases)}


def validate_clean_install_evidence(evidence: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(evidence, dict):
        raise TypeError("clean-install evidence must be an object")
    gates = {
        "package_variant_recorded": evidence.get("package_variant") in {"Core", "Full"},
        "clean_machine": evidence.get("clean_machine") is True,
        "unicode_install_path": evidence.get("unicode_install_path") is True,
        "document_preserved": evidence.get("document_preserved") is True,
        "engine_started": evidence.get("engine_started") is True,
        "zero_deterministic_tcp": evidence.get("deterministic_tcp_connections") == 0,
        "uninstall_passed": evidence.get("uninstall_passed") is True,
    }
    return {"gates": gates, "all_green": all(gates.values())}


def validate_windows_signing_evidence(evidence: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(evidence, dict):
        raise TypeError("Windows signing evidence must be an object")
    gates = {
        "artifact_sha256": _is_sha256(evidence.get("artifact_sha256")),
        "authenticode_valid": evidence.get("authenticode_valid") is True,
        "trusted_chain": evidence.get("trusted_chain") is True,
        "timestamp_valid": evidence.get("timestamp_valid") is True,
        "subject_recorded": isinstance(evidence.get("signing_subject"), str)
        and bool(evidence.get("signing_subject")),
    }
    return {"gates": gates, "all_green": all(gates.values())}


def validate_linux_runtime_evidence(evidence: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(evidence, dict):
        raise TypeError("Linux runtime evidence must be an object")
    formats = evidence.get("formats") if isinstance(evidence.get("formats"), dict) else {}
    gates = {
        "appimage_passed": isinstance(formats.get("appimage"), dict)
        and formats["appimage"].get("passed") is True,
        "flatpak_passed": isinstance(formats.get("flatpak"), dict)
        and formats["flatpak"].get("passed") is True,
        "unicode_launch": evidence.get("unicode_launch") is True,
        "engine_child": evidence.get("engine_child") is True,
        "zero_deterministic_tcp": evidence.get("deterministic_tcp_connections") == 0,
    }
    return {"gates": gates, "all_green": all(gates.values())}


def validate_macos_release_evidence(evidence: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(evidence, dict):
        raise TypeError("macOS release evidence must be an object")
    gates = {
        "dmg_runtime_passed": evidence.get("dmg_runtime_passed") is True,
        "developer_id_valid": evidence.get("developer_id_valid") is True,
        "notarization_accepted": evidence.get("notarization_accepted") is True,
        "staple_valid": evidence.get("staple_valid") is True,
        "unicode_launch": evidence.get("unicode_launch") is True,
        "zero_deterministic_tcp": evidence.get("deterministic_tcp_connections") == 0,
    }
    return {"gates": gates, "all_green": all(gates.values())}


def validate_independent_reviews(evidence: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(evidence, dict):
        raise TypeError("review evidence must be an object")
    reviews = evidence.get("reviews") if isinstance(evidence.get("reviews"), dict) else {}
    gates = {
        category: isinstance(reviews.get(category), dict)
        and reviews[category].get("status") == "PASS"
        and isinstance(reviews[category].get("reviewer"), str)
        and bool(reviews[category].get("reviewer"))
        for category in REVIEW_CATEGORIES
    }
    return {
        "gates": gates,
        "all_green": all(gates.values()),
        "required_categories": list(REVIEW_CATEGORIES),
    }


def validate_reproducibility_evidence(evidence: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(evidence, dict):
        raise TypeError("reproducibility evidence must be an object")
    matrix = evidence.get("matrix") if isinstance(evidence.get("matrix"), dict) else {}
    gates: dict[str, bool] = {}
    for target in REPRODUCIBILITY_TARGETS:
        item = matrix.get(target)
        gates[target] = (
            isinstance(item, dict)
            and item.get("equivalent") is True
            and item.get("candidate_a_pool") == "repro-a"
            and item.get("candidate_b_pool") == "repro-b"
            and item.get("candidate_a_builder") != item.get("candidate_b_builder")
            and _is_sha256(item.get("source_commit"))
            and _is_sha256(item.get("toolchain_lock_sha256"))
        )
    return {
        "gates": gates,
        "all_green": all(gates.values()),
        "required_targets": list(REPRODUCIBILITY_TARGETS),
        "independent_builder_pools_required": True,
    }


def validate_update_rollback_evidence(evidence: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(evidence, dict):
        raise TypeError("update/rollback evidence must be an object")
    gates = {
        "update_manifest_verified": evidence.get("update_manifest_verified") is True,
        "upgrade_launch_passed": evidence.get("upgrade_launch_passed") is True,
        "writer_state_preserved": evidence.get("writer_state_preserved") is True,
        "rollback_launch_passed": evidence.get("rollback_launch_passed") is True,
        "rollback_state_restored": evidence.get("rollback_state_restored") is True,
        "manuscript_bytes_preserved": evidence.get("manuscript_bytes_preserved") is True,
    }
    return {"gates": gates, "all_green": all(gates.values())}


def evidence_bundle_manifest(root: str | Path) -> dict[str, Any]:
    base = Path(root).resolve(strict=True)
    if not base.is_dir():
        raise ValueError("release evidence root must be a directory")
    files = []
    for path in sorted(item for item in base.rglob("*") if item.is_file()):
        relative = path.relative_to(base).as_posix()
        raw = path.read_bytes()
        files.append({"path": relative, "size": len(raw), "sha256": hashlib.sha256(raw).hexdigest()})
    payload = {"format": "thothpad-release-evidence", "version": 1, "files": files}
    payload["manifest_sha256"] = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return payload
