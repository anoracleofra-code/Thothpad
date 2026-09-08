from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from backend.story.interface_fingerprint import interface_fingerprint
from backend.story.performance_budget import performance_budget_report
from backend.story.project import StoryProject
from backend.story.quality_gates import aggregate_quality_gates
from backend.story.relocation import relocation_readiness
from backend.story.soak import run_soak_replay
from backend.story.store import StoryStore
from backend.story.support_bundle import build_support_bundle


@dataclass(slots=True)
class ReleaseReadiness:
    project: StoryProject
    store: StoryStore
    story_tools: list[dict[str, Any]]

    def report(self, *, external_evidence: dict[str, Any] | None = None) -> dict[str, Any]:
        soak = run_soak_replay(self.project.root, cycles=2)
        support = build_support_bundle(self.project, self.store)
        interface = interface_fingerprint(self.story_tools)
        performance = performance_budget_report(self.project, self.store)
        relocation = relocation_readiness(self.project, self.store)
        privacy = support["privacy"]
        support_privacy_green = all(
            not value for key, value in privacy.items() if key != "telemetry_upload_required"
        ) and privacy["telemetry_upload_required"] is False
        internal = aggregate_quality_gates(
            {
                "phase46_soak": soak["semantic_fingerprint_stable"]
                and soak["durable_writer_state_unchanged"]
                and soak["all_cycles_green"],
                "phase47_support_privacy": support_privacy_green,
                "phase48_interface_fingerprint": len(str(interface["fingerprint"])) == 64,
                "phase50_performance_budget": performance["all_green"],
                "phase51_relocation": relocation["all_green"],
            }
        )
        external = external_evidence or {}
        expected_external = {
            "network_capture": bool(external.get("network_capture")),
            "quality_gate_reviews": bool(external.get("quality_gate_reviews")),
            "clean_install_contract": bool(external.get("clean_install_contract")),
            "update_manifest": bool(external.get("update_manifest")),
            "platform_release_actions": bool(external.get("platform_release_actions")),
        }
        pending = [name for name, passed in expected_external.items() if not passed]
        return {
            "project_id": self.project.project_id,
            "internal": {
                "all_green": internal["all_required_green"],
                "quality_gates": internal,
                "soak": soak,
                "support_bundle_privacy": privacy,
                "interface_fingerprint": interface["fingerprint"],
                "performance": performance,
                "relocation": relocation,
            },
            "external_release_evidence": expected_external,
            "external_pending": pending,
            "production_release_ready": internal["all_required_green"] and not pending,
            "story_engine_release_hardening_green": internal["all_required_green"],
            "honesty_rule": (
                "Platform signing/notarization and clean-machine/network evidence remain explicit external actions; "
                "ThothPad never converts missing platform evidence into a green release claim."
            ),
        }
