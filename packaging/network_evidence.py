from __future__ import annotations

from typing import Any


def validate_network_capture_evidence(evidence: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(evidence, dict):
        raise TypeError("network capture evidence must be an object")
    launched = evidence.get("installed_and_launched") is True or evidence.get("application_launched") is True
    required = {
        "process_tree_monitored": evidence.get("process_tree_monitored") is True,
        "deterministic_tcp_connections_zero": evidence.get("deterministic_tcp_connections") == 0,
        "tcp_samples_positive": isinstance(evidence.get("tcp_samples"), int) and evidence["tcp_samples"] > 0,
        "monitor_window_positive": isinstance(evidence.get("tree_monitor_seconds"), (int, float))
        and evidence["tree_monitor_seconds"] > 0,
        "application_launched": launched,
        "engine_started": evidence.get("bundled_engine_started") is True,
    }
    return {
        "gates": required,
        "all_green": all(required.values()),
        "network_capture_is_external_runtime_evidence": True,
    }
