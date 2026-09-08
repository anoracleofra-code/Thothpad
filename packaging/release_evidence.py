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

_REFERENCE_BENCHMARK_LABELS = {
    "clean-10000",
    "dense-10000",
    "dialogue-10000",
    "unicode-10000",
}


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _is_sha256(value: Any) -> bool:
    return isinstance(value, str) and len(value) == 64 and all(ch in "0123456789abcdef" for ch in value.lower())


def _is_git_commit(value: Any) -> bool:
    return isinstance(value, str) and len(value) == 40 and all(ch in "0123456789abcdef" for ch in value.lower())


def _positive_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and float(value) >= 0.0


def _unix_runtime_receipt(
    receipt: Any,
    *,
    platform: str,
    format_name: str,
    variant: str,
    minimum_trials: int,
) -> bool:
    if not isinstance(receipt, dict):
        return False
    checks = _dict(receipt.get("checks"))
    trials = _list(receipt.get("trials"))
    requested = receipt.get("launch_trials_requested")
    completed = receipt.get("launch_trials_completed")
    return (
        receipt.get("schema_version") == 1
        and receipt.get("status") == "passed"
        and receipt.get("platform") == platform
        and receipt.get("format") == format_name
        and receipt.get("variant") == variant
        and _is_sha256(receipt.get("artifact_sha256"))
        and isinstance(requested, int)
        and not isinstance(requested, bool)
        and requested >= minimum_trials
        and completed == requested
        and len(trials) == completed
        and all(checks.get(key) is True for key in ("variant", "bundled_engine", "no_startup_tcp", "unicode_byte_identical", "stable_main_process"))
        and all(
            isinstance(trial, dict)
            and isinstance(trial.get("engine_process_count"), int)
            and trial.get("engine_process_count", 0) >= 1
            and trial.get("tcp_socket_count") == 0
            for trial in trials
        )
    )


def _reproducibility_receipt(receipt: Any) -> bool:
    if not isinstance(receipt, dict):
        return False
    builders = _dict(receipt.get("builders"))
    pools = _dict(receipt.get("runner_pools"))
    candidates = _dict(receipt.get("candidate_manifests"))
    return (
        receipt.get("schema_version") == 1
        and receipt.get("matched") is True
        and isinstance(receipt.get("file_count"), int)
        and not isinstance(receipt.get("file_count"), bool)
        and receipt.get("file_count", 0) > 0
        and _is_sha256(receipt.get("normalized_tree_sha256"))
        and _is_git_commit(receipt.get("source_commit"))
        and _is_sha256(receipt.get("toolchain_lock_sha256"))
        and isinstance(builders.get("left"), str)
        and bool(builders.get("left"))
        and isinstance(builders.get("right"), str)
        and bool(builders.get("right"))
        and builders.get("left") != builders.get("right")
        and pools == {"left": "repro-a", "right": "repro-b"}
        and _is_sha256(candidates.get("left"))
        and _is_sha256(candidates.get("right"))
    )


def validate_benchmark_evidence(evidence: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(evidence, dict):
        raise TypeError("benchmark evidence must be an object")
    results = evidence.get("results")
    cases = results if isinstance(results, list) else []
    all_cases_are_objects = bool(cases) and all(isinstance(item, dict) for item in cases)
    objects = [item for item in cases if isinstance(item, dict)]
    labels = {str(item.get("label", "")) for item in objects}
    gates = {
        "benchmark_named": evidence.get("benchmark") == "writer-engine-analysis",
        "python_recorded": isinstance(evidence.get("python"), str) and bool(evidence.get("python")),
        "platform_recorded": isinstance(evidence.get("platform"), str) and bool(evidence.get("platform")),
        "cases_present": all_cases_are_objects,
        "reference_cases_complete": _REFERENCE_BENCHMARK_LABELS.issubset(labels),
        "reference_sizes_and_trials": all(
            item.get("words") == 10_000
            and isinstance(item.get("trials"), int)
            and not isinstance(item.get("trials"), bool)
            and item.get("trials", 0) >= 3
            for item in objects
            if item.get("label") in _REFERENCE_BENCHMARK_LABELS
        )
        and _REFERENCE_BENCHMARK_LABELS.issubset(labels),
        "case_hashes_valid": all_cases_are_objects and all(_is_sha256(item.get("sha256")) for item in objects),
        "timings_present": all_cases_are_objects
        and all(
            isinstance(item.get("milliseconds"), dict)
            and all(_positive_number(item["milliseconds"].get(key)) for key in ("p50", "p95", "maximum"))
            and float(item["milliseconds"]["p50"]) <= float(item["milliseconds"]["p95"])
            and float(item["milliseconds"]["p95"]) <= float(item["milliseconds"]["maximum"])
            for item in objects
        ),
    }
    return {"gates": gates, "all_green": all(gates.values()), "case_count": len(cases)}


def validate_clean_install_evidence(evidence: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(evidence, dict):
        raise TypeError("clean-install evidence must be an object")
    variants = _dict(evidence.get("variants"))

    def valid_receipt(variant: str) -> bool:
        receipt = variants.get(variant)
        if not isinstance(receipt, dict):
            return False
        launch_trials = receipt.get("launch_trials")
        tcp_samples = receipt.get("tcp_samples")
        return (
            receipt.get("schema_version") == 1
            and receipt.get("package_variant") == variant
            and receipt.get("clean_machine") is True
            and _is_sha256(receipt.get("installer_sha256"))
            and isinstance(launch_trials, int)
            and not isinstance(launch_trials, bool)
            and launch_trials >= 100
            and receipt.get("installed_and_launched") is True
            and receipt.get("main_window_responding") is True
            and receipt.get("bundled_engine_started") is True
            and receipt.get("deterministic_tcp_connections") == 0
            and receipt.get("process_tree_monitored") is True
            and isinstance(tcp_samples, int)
            and not isinstance(tcp_samples, bool)
            and tcp_samples >= launch_trials * 12
            and _positive_number(receipt.get("tree_monitor_seconds"))
            and float(receipt.get("tree_monitor_seconds", 0.0)) > 0.0
            and receipt.get("unicode_sample_preserved") is True
            and receipt.get("uninstall_completed") is True
        )

    gates = {"core_clean_install": valid_receipt("Core"), "full_clean_install": valid_receipt("Full")}
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
    variants = _dict(evidence.get("variants"))
    gates: dict[str, bool] = {}
    for variant in ("Core", "Full"):
        item = _dict(variants.get(variant))
        gates[f"{variant.casefold()}_appimage"] = _unix_runtime_receipt(
            item.get("appimage"), platform="linux", format_name="appimage", variant=variant, minimum_trials=100
        )
        gates[f"{variant.casefold()}_flatpak"] = _unix_runtime_receipt(
            item.get("flatpak"), platform="linux", format_name="flatpak", variant=variant, minimum_trials=1
        )
    return {"gates": gates, "all_green": all(gates.values())}


def validate_macos_release_evidence(evidence: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(evidence, dict):
        raise TypeError("macOS release evidence must be an object")
    variants = _dict(evidence.get("variants"))
    gates: dict[str, bool] = {}
    for variant in ("Core", "Full"):
        item = _dict(variants.get(variant))
        runtime = item.get("runtime")
        signing = _dict(item.get("signing"))
        runtime_green = _unix_runtime_receipt(
            runtime,
            platform="macos",
            format_name="dmg",
            variant=variant,
            minimum_trials=100,
        )
        runtime_hash = runtime.get("artifact_sha256") if isinstance(runtime, dict) else None
        signing_green = (
            signing.get("schema_version") == 1
            and signing.get("variant") == variant
            and signing.get("public_release") is True
            and _is_sha256(signing.get("artifact_sha256"))
            and signing.get("artifact_sha256") == runtime_hash
            and signing.get("developer_id_valid") is True
            and signing.get("notarization_accepted") is True
            and signing.get("staple_valid") is True
            and signing.get("spctl_accepted") is True
            and isinstance(signing.get("signing_subject"), str)
            and bool(signing.get("signing_subject"))
        )
        gates[f"{variant.casefold()}_runtime"] = runtime_green
        gates[f"{variant.casefold()}_signing"] = signing_green
    return {"gates": gates, "all_green": all(gates.values())}


def validate_independent_reviews(evidence: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(evidence, dict):
        raise TypeError("review evidence must be an object")
    reviews = _dict(evidence.get("reviews"))
    normalized_reviews = {category: _dict(reviews.get(category)) for category in REVIEW_CATEGORIES}
    gates = {
        category: bool(normalized_reviews[category])
        and normalized_reviews[category].get("status") == "PASS"
        and isinstance(normalized_reviews[category].get("reviewer"), str)
        and bool(normalized_reviews[category].get("reviewer"))
        and normalized_reviews[category].get("independent") is True
        and _is_git_commit(normalized_reviews[category].get("source_commit"))
        and _is_sha256(normalized_reviews[category].get("evidence_sha256"))
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
    matrix = _dict(evidence.get("matrix"))
    gates: dict[str, bool] = {}
    for target in REPRODUCIBILITY_TARGETS:
        item = matrix.get(target)
        if target.startswith("linux-"):
            gates[target] = isinstance(item, dict) and _reproducibility_receipt(
                item.get("appimage")
            ) and _reproducibility_receipt(item.get("flatpak"))
        else:
            gates[target] = _reproducibility_receipt(item)
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
        "schema": evidence.get("schema_version") == 1,
        "source_commit": _is_git_commit(evidence.get("source_commit")),
        "update_manifest_hash": _is_sha256(evidence.get("update_manifest_sha256")),
        "previous_artifact_hash": _is_sha256(evidence.get("previous_artifact_sha256")),
        "target_artifact_hash": _is_sha256(evidence.get("target_artifact_sha256")),
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
    for path in sorted(base.rglob("*")):
        if path.is_symlink():
            raise ValueError("release evidence bundles must not contain symlinks")
        if not path.is_file():
            continue
        relative = path.relative_to(base).as_posix()
        raw = path.read_bytes()
        files.append({"path": relative, "size": len(raw), "sha256": hashlib.sha256(raw).hexdigest()})
    payload = {"format": "thothpad-release-evidence", "version": 1, "files": files}
    payload["manifest_sha256"] = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return payload
