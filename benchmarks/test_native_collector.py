#!/usr/bin/env python3
"""Focused dependency-free checks for native collector orchestration."""

from __future__ import annotations

import copy
import os
import tempfile
from pathlib import Path

import run_external_collector as native


def fixture() -> dict:
    return {
        "id": "clean-10000",
        "corpus": "clean",
        "word_count": 10_000,
        "corpus_sha256": "a" * 64,
    }


def config(suite: str = "smoke") -> dict:
    return {
        "suite": suite,
        "warm_trials": 1 if suite == "smoke" else 30,
        "cold_trials": 1 if suite == "smoke" else 20,
        "platform": "windows",
        "hardware": {"logical_cpus": 2, "memory_bytes": 8, "machine": "x", "processor": "y"},
        "build": {"git_commit": "abc", "dirty": True, "source_sha256": "b" * 64},
        "target": {"id": "2vcpu-8gb-integrated-ssd", "attested": False},
    }


def result(configuration: dict, workload: dict) -> dict:
    metrics = []
    for name, kind in native.METRICS.items():
        count = configuration[f"{kind}_trials"]
        metrics.append({
            "metric": name,
            "unit": "ms",
            "status": "measured",
            "collector": "qt-native-efficiency-v1",
            "trial_kind": kind,
            "samples": [1.0] * count,
        })
    return {
        "schema_version": 1,
        "run_id": "test",
        "created_utc": "2026-08-19T00:00:00Z",
        "suite": configuration["suite"],
        "platform": configuration["platform"],
        "hardware": configuration["hardware"],
        "build": configuration["build"],
        "target": configuration["target"],
        "workload": workload,
        "metrics": metrics,
        "automatic_failures": [],
    }


def expect_invalid(value: dict, configuration: dict, workload: dict) -> None:
    try:
        native.validate_result(value, configuration, workload)
    except ValueError:
        return
    raise AssertionError("invalid collector evidence was accepted")


def main() -> int:
    assert native.parse_workload("unicode-500000") == ("unicode", 500_000)
    assert len(native.selected_workloads(None)) == 9
    assert native.selected_workloads([("clean", 10_000), ("clean", 10_000)]) == [("clean", 10_000)]
    native_text = native.native_corpus_text("unicode", 10_000)
    assert len(native_text.split()) == 10_000
    assert native_text.split() == native.gate.corpus_text("unicode", 10_000).split()
    assert "\n\n" in native_text

    for suite in ("smoke", "certification"):
        configuration = config(suite)
        workload = fixture()
        value = result(configuration, workload)
        native.validate_result(value, configuration, workload)

        wrong_count = copy.deepcopy(value)
        wrong_count["metrics"][0]["samples"].clear()
        expect_invalid(wrong_count, configuration, workload)

        wrong_identity = copy.deepcopy(value)
        wrong_identity["workload"]["corpus_sha256"] = "c" * 64
        expect_invalid(wrong_identity, configuration, workload)

    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        executable = root / ("efficiencycollector.exe" if os.name == "nt" else "efficiencycollector")
        executable.write_bytes(b"collector")
        assert native.find_collector(root) == executable

    print("Native collector orchestration tests passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
