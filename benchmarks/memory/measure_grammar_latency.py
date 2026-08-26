"""Live-lane grammar latency tradeoff probe for the single-Harper release fix.

Drives the frozen writer-engine sidecar through the standard stdio protocol and
measures full analyze_region round-trip latency on an identical region:

  phase A   cold supervisor: first grammar hit spawns the persistent Harper
  phase A'  warm repeats (session reuse)
  dispose_document  (releases Harper + report worker per the lifecycle fix)
  phase B   post-release cold hit (Harper must respawn lazily)
  phase B'  warm repeats

The delta between each cold hit and its warm distribution isolates the lazy
spawn cost; warm p95 is compared against the historical live-grammar budget
(<= 84 ms p95). Also counts thothpad-harper processes in the tree as evidence
that exactly one instance exists while warm and zero after dispose.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from run_scenarios import PROTOCOL_MAJOR, RUN_DATE, SidecarClient  # noqa: E402
from sampler import dump_json, hardware_identity, percentile  # noqa: E402

try:
    import psutil
except ImportError as _exc:  # pragma: no cover - pinned venv ships psutil
    raise SystemExit(
        "psutil is required: pip install psutil"
    ) from _exc

REPO_ROOT = Path(os.environ.get("THOTHPAD_REPO", HERE.parents[1]))
ENGINE_EXE_DEFAULT = (
    REPO_ROOT / "writer-engine" / "dist" / "writer-engine" / "writer-engine.exe"
)
RESULTS_DIR_DEFAULT = REPO_ROOT / "benchmark-results" / "memory-after"

HARPER_PROCESS_NAME = "thothpad-harper.exe" if os.name == "nt" else "thothpad-harper"
WARM_BUDGET_MS = 84.0  # historical live-grammar p95 lane budget
SETTLE_S = 2.0

REGION_TEXT = (
    "Mara noticed the ledger before she noticed the man. It lay open on the "
    "desk, its columns ruled in a careful hand, and beside it a cup of coffee "
    "gone cold hours ago. She did not touch anything. She had learned that "
    "much in ten years of this work: the room tells you what happened only if "
    "you let it speak first. The window rattled. Rain again. She wrote the "
    "time in her notebook, then the temperature of the coffee, then the fact "
    "that the man's shoes were dry despite the weather, which meant he had "
    "been here since before the storm rolled in off the harbor."
)


def harper_process_count(root_pid: int) -> int:
    root = psutil.Process(root_pid)
    try:
        children = root.children(recursive=True)
    except psutil.Error:
        return -1
    return sum(1 for child in children if child.name() == HARPER_PROCESS_NAME)


def timed_region(client: SidecarClient, document_id: str) -> dict[str, Any]:
    started = time.perf_counter()
    result = client.request(
        "analyze_region",
        document_id=document_id,
        document_revision=1,
        text=REGION_TEXT,
        language="en",
        base_offset_utf16=0,
    )
    elapsed_ms = (time.perf_counter() - started) * 1000
    return {
        "round_trip_ms": round(elapsed_ms, 3),
        "engine_duration_ms": float(result.get("duration_ms", 0.0)),
        "diagnostics": len(result.get("diagnostics", [])),
    }


def warm_series(client: SidecarClient, document_id: str, reps: int) -> list[float]:
    values = []
    for _ in range(reps):
        values.append(timed_region(client, document_id)["round_trip_ms"])
    return values


def series_stats(values: list[float]) -> dict[str, Any]:
    ordered = sorted(values)
    return {
        "n": len(values),
        "min_ms": round(min(values), 3),
        "p50_ms": round(percentile(ordered, 0.50), 3),
        "p95_ms": round(percentile(ordered, 0.95), 3),
        "max_ms": round(max(values), 3),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--engine", type=Path, default=ENGINE_EXE_DEFAULT)
    parser.add_argument("--results-dir", type=Path, default=RESULTS_DIR_DEFAULT)
    parser.add_argument("--warm-reps", type=int, default=30)
    args = parser.parse_args()

    args.results_dir.mkdir(parents=True, exist_ok=True)

    client = SidecarClient(args.engine)
    evidence: dict[str, Any] = {
        "schema_version": 1,
        "kind": "live-grammar-latency-tradeoff",
        "run_date": RUN_DATE,
        "protocol_major": PROTOCOL_MAJOR,
        "engine_exe": str(args.engine),
        "hardware": hardware_identity(),
        "region_chars": len(REGION_TEXT),
        "warm_reps_per_phase": args.warm_reps,
        "warm_budget_p95_ms": WARM_BUDGET_MS,
    }
    try:
        client.request("initialize")

        # Phase A: fresh supervisor, Harper has never spawned.
        client.request(
            "open_document",
            document_id="latency-probe",
            revision=1,
            text=REGION_TEXT,
            language="en",
        )
        cold_a = timed_region(client, "latency-probe")
        warm_a = series_stats(warm_series(client, "latency-probe", args.warm_reps))
        harper_warm = harper_process_count(client.pid)

        # Release via dispose_document, then let the process tree settle.
        client.request("dispose_document", document_id="latency-probe")
        time.sleep(SETTLE_S)
        harper_after_dispose = harper_process_count(client.pid)

        # Phase B: document reopened after release; Harper must respawn lazily.
        client.request(
            "open_document",
            document_id="latency-probe",
            revision=1,
            text=REGION_TEXT,
            language="en",
        )
        cold_b = timed_region(client, "latency-probe")
        warm_b = series_stats(warm_series(client, "latency-probe", args.warm_reps))
    finally:
        try:
            client.request("dispose_document", document_id="latency-probe")
        except Exception:
            pass
        client.close()

    evidence["phase_a_cold_fresh_supervisor"] = cold_a
    evidence["phase_a_warm"] = warm_a
    evidence["harper_processes_warm"] = harper_warm
    evidence["harper_processes_after_dispose"] = harper_after_dispose
    evidence["phase_b_cold_after_release"] = cold_b
    evidence["phase_b_warm"] = warm_b
    evidence["cold_spawn_cost_estimate_ms"] = {
        "phase_a_vs_warm_p50": round(cold_a["round_trip_ms"] - warm_a["p50_ms"], 3),
        "phase_b_vs_warm_p50": round(cold_b["round_trip_ms"] - warm_b["p50_ms"], 3),
    }

    worst_warm_p95 = max(warm_a["p95_ms"], warm_b["p95_ms"])
    evidence["worst_warm_p95_ms"] = worst_warm_p95
    evidence["warm_budget_verdict"] = (
        "PASS" if worst_warm_p95 <= WARM_BUDGET_MS else "FAIL"
    )
    evidence["metric_notes"] = (
        "round_trip_ms covers the full analyze_region protocol round trip on an "
        "identical region; the cold-minus-warm-p50 delta isolates the lazy "
        "Harper spawn cost because every other stage is unchanged between hits."
    )

    path = args.results_dir / "live-grammar-latency.json"
    dump_json(str(path), evidence)

    print(f"[write] {path}")
    print(f"cold A (fresh supervisor) : {cold_a['round_trip_ms']:.1f} ms round trip")
    print(f"warm A  p50/p95           : {warm_a['p50_ms']:.1f} / {warm_a['p95_ms']:.1f} ms (n={warm_a['n']})")
    print(f"harper processes warm     : {harper_warm}")
    print(f"harper processes disposed : {harper_after_dispose}")
    print(f"cold B (after release)    : {cold_b['round_trip_ms']:.1f} ms round trip")
    print(f"warm B  p50/p95           : {warm_b['p50_ms']:.1f} / {warm_b['p95_ms']:.1f} ms (n={warm_b['n']})")
    print(f"spawn cost estimate       : A {evidence['cold_spawn_cost_estimate_ms']['phase_a_vs_warm_p50']:.1f} ms, "
          f"B {evidence['cold_spawn_cost_estimate_ms']['phase_b_vs_warm_p50']:.1f} ms")
    print(f"warm p95 budget <= {WARM_BUDGET_MS:.0f} ms : {evidence['warm_budget_verdict']}")
    return 0 if evidence["warm_budget_verdict"] == "PASS" and harper_warm == 1 else 1


if __name__ == "__main__":
    raise SystemExit(main())
