from __future__ import annotations

import hashlib
import sys
from pathlib import Path

import pytest

PACKAGING = Path(__file__).resolve().parents[1]
if str(PACKAGING) not in sys.path:
    sys.path.insert(0, str(PACKAGING))

from release_evidence import (
    REPRODUCIBILITY_TARGETS,
    REVIEW_CATEGORIES,
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
from release_promotion import (
    production_promotion_readiness,
    release_evidence_portability,
)


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _benchmark() -> dict:
    return {
        "benchmark": "writer-engine-analysis",
        "python": "3.11.9",
        "platform": "Windows-reference",
        "results": [
            {
                "label": "clean-10000",
                "sha256": _sha("clean-10000"),
                "milliseconds": {"p50": 10.0, "p95": 12.0, "maximum": 13.0},
            }
        ],
    }


def _clean_install() -> dict:
    return {
        "package_variant": "Core",
        "clean_machine": True,
        "unicode_install_path": True,
        "document_preserved": True,
        "engine_started": True,
        "deterministic_tcp_connections": 0,
        "uninstall_passed": True,
    }


def _windows_signing() -> dict:
    return {
        "artifact_sha256": _sha("windows-artifact"),
        "authenticode_valid": True,
        "trusted_chain": True,
        "timestamp_valid": True,
        "signing_subject": "Example Developer",
    }


def _linux_runtime() -> dict:
    return {
        "formats": {"appimage": {"passed": True}, "flatpak": {"passed": True}},
        "unicode_launch": True,
        "engine_child": True,
        "deterministic_tcp_connections": 0,
    }


def _macos_release() -> dict:
    return {
        "dmg_runtime_passed": True,
        "developer_id_valid": True,
        "notarization_accepted": True,
        "staple_valid": True,
        "unicode_launch": True,
        "deterministic_tcp_connections": 0,
    }


def _reviews() -> dict:
    return {
        "reviews": {
            category: {"status": "PASS", "reviewer": f"independent-{category}"}
            for category in REVIEW_CATEGORIES
        }
    }


def _reproducibility() -> dict:
    return {
        "matrix": {
            target: {
                "equivalent": True,
                "candidate_a_pool": "repro-a",
                "candidate_b_pool": "repro-b",
                "candidate_a_builder": f"{target}-builder-a",
                "candidate_b_builder": f"{target}-builder-b",
                "source_commit": _sha("source"),
                "toolchain_lock_sha256": _sha("toolchain"),
            }
            for target in REPRODUCIBILITY_TARGETS
        }
    }


def _update_rollback() -> dict:
    return {
        "update_manifest_verified": True,
        "upgrade_launch_passed": True,
        "writer_state_preserved": True,
        "rollback_launch_passed": True,
        "rollback_state_restored": True,
        "manuscript_bytes_preserved": True,
    }


def _all_evidence() -> dict:
    return {
        "benchmark": _benchmark(),
        "windows_clean_install": _clean_install(),
        "windows_signing": _windows_signing(),
        "linux_runtime": _linux_runtime(),
        "macos_release": _macos_release(),
        "independent_reviews": _reviews(),
        "reproducibility": _reproducibility(),
        "update_rollback": _update_rollback(),
    }


def test_phase56_benchmark_evidence_requires_hashes_and_timings():
    assert validate_benchmark_evidence(_benchmark())["all_green"] is True
    broken = _benchmark()
    broken["results"][0]["sha256"] = "bad"
    assert validate_benchmark_evidence(broken)["all_green"] is False


def test_phase57_clean_install_evidence_is_fail_closed():
    assert validate_clean_install_evidence(_clean_install())["all_green"] is True
    broken = _clean_install()
    broken["clean_machine"] = False
    assert validate_clean_install_evidence(broken)["all_green"] is False


def test_phase58_windows_signing_requires_chain_timestamp_and_subject():
    assert validate_windows_signing_evidence(_windows_signing())["all_green"] is True
    broken = _windows_signing()
    broken["trusted_chain"] = False
    assert validate_windows_signing_evidence(broken)["all_green"] is False


def test_phase59_linux_runtime_requires_both_package_formats():
    assert validate_linux_runtime_evidence(_linux_runtime())["all_green"] is True
    broken = _linux_runtime()
    broken["formats"]["flatpak"]["passed"] = False
    assert validate_linux_runtime_evidence(broken)["all_green"] is False


def test_phase60_macos_release_requires_notarization_and_staple():
    assert validate_macos_release_evidence(_macos_release())["all_green"] is True
    broken = _macos_release()
    broken["notarization_accepted"] = False
    assert validate_macos_release_evidence(broken)["all_green"] is False


def test_phase61_independent_reviews_require_every_category():
    assert validate_independent_reviews(_reviews())["all_green"] is True
    broken = _reviews()
    del broken["reviews"][REVIEW_CATEGORIES[0]]
    assert validate_independent_reviews(broken)["all_green"] is False


def test_phase62_reproducibility_requires_independent_a_b_builders():
    assert validate_reproducibility_evidence(_reproducibility())["all_green"] is True
    broken = _reproducibility()
    target = REPRODUCIBILITY_TARGETS[0]
    broken["matrix"][target]["candidate_b_builder"] = broken["matrix"][target]["candidate_a_builder"]
    assert validate_reproducibility_evidence(broken)["all_green"] is False


def test_phase63_update_rollback_requires_writer_and_manuscript_preservation():
    assert validate_update_rollback_evidence(_update_rollback())["all_green"] is True
    broken = _update_rollback()
    broken["manuscript_bytes_preserved"] = False
    assert validate_update_rollback_evidence(broken)["all_green"] is False


def test_phase64_release_evidence_bundle_manifest_is_relative_and_content_addressed(tmp_path: Path):
    root = tmp_path / "evidence"
    nested = root / "windows" / "Core"
    nested.mkdir(parents=True)
    (nested / "acceptance.json").write_text('{"status":"PASS"}', encoding="utf-8")
    manifest = evidence_bundle_manifest(root)
    assert manifest["files"][0]["path"] == "windows/Core/acceptance.json"
    assert len(manifest["manifest_sha256"]) == 64
    portable = release_evidence_portability(str(root))
    assert portable["portable"] is True
    assert portable["absolute_paths"] == []


def test_phase65_production_promotion_readiness_never_invents_missing_external_evidence():
    missing = production_promotion_readiness({})
    assert missing["production_promotion_ready"] is False
    assert set(missing["missing_or_failed"]) == set(missing["gates"])
    complete = production_promotion_readiness(_all_evidence())
    assert complete["production_promotion_ready"] is True
    assert complete["missing_or_failed"] == []
    assert complete["fail_closed"] is True


def test_release_evidence_validators_reject_non_object_payloads():
    validators = (
        validate_benchmark_evidence,
        validate_clean_install_evidence,
        validate_windows_signing_evidence,
        validate_linux_runtime_evidence,
        validate_macos_release_evidence,
        validate_independent_reviews,
        validate_reproducibility_evidence,
        validate_update_rollback_evidence,
    )
    for validator in validators:
        with pytest.raises(TypeError):
            validator([])  # type: ignore[arg-type]
