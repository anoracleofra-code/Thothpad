from __future__ import annotations

from typing import Any


def aggregate_quality_gates(evidence: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(evidence, dict):
        raise ValueError("quality gate evidence must be an object")
    normalized: dict[str, dict[str, Any]] = {}
    for name, value in sorted(evidence.items()):
        if not isinstance(name, str) or not name or len(name) > 120:
            raise ValueError("quality gate names must be 1-120 character strings")
        if isinstance(value, bool):
            normalized[name] = {"passed": value, "required": True}
        elif isinstance(value, dict):
            passed = value.get("passed")
            required = value.get("required", True)
            if not isinstance(passed, bool) or not isinstance(required, bool):
                raise ValueError("quality gate objects require boolean passed/required fields")
            normalized[name] = {
                "passed": passed,
                "required": required,
                "evidence": value.get("evidence"),
            }
        else:
            raise ValueError("quality gate values must be booleans or gate objects")
    required = [item for item in normalized.values() if item["required"]]
    optional = [item for item in normalized.values() if not item["required"]]
    return {
        "gates": normalized,
        "required_count": len(required),
        "required_passed": sum(1 for item in required if item["passed"]),
        "optional_count": len(optional),
        "all_required_green": all(item["passed"] for item in required),
        "failed_required": [name for name, item in normalized.items() if item["required"] and not item["passed"]],
    }
