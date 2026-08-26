"""Live-region latency benchmark for the ThothPad desktop sidecar protocol.

Opens one 100k-word document over the framed sidecar protocol, then times
warm ``analyze_region`` calls against an 8,000-character region of it:

* ``live_region_ms`` -- full live preset (all LIVE_ANALYZERS) over the region;
  s-grade-v1 gate ``live-region`` requires p95 <= 150 ms.
* ``live_pos_ms`` -- high-confidence POS lane: the same live preset path
  restricted to the possible_adverbs/possible_adjectives/possible_verbs
  analyzers. The live preset forces ``_live_lexical_only``, so spaCy
  contextual confirmation stays off and only lexical (high-confidence) POS
  matches are reported; s-grade-v1 gate ``live-pos`` requires p95 <= 250 ms.

Gate limits are read from benchmarks/thresholds/s-grade-v1.json (immutable).
Results are written as machine-readable JSON including hardware identity.
Measurements taken outside the attested 2vcpu-8gb-integrated-ssd target are
labelled non-attesting dev-host evidence.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
import threading
import time
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from benchmark_cancellation import hardware_identity, stats_block  # noqa: E402

from backend.sidecar import PROTOCOL_MAJOR, PROTOCOL_MINOR, PersistentWorker  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[2]
THRESHOLDS_PATH = REPO_ROOT / "benchmarks" / "thresholds" / "s-grade-v1.json"
POS_ANALYZERS = ["possible_adverbs", "possible_adjectives", "possible_verbs"]


def request(operation: str, document_id: str, **params) -> dict:
    return {
        "protocol_major": PROTOCOL_MAJOR,
        "protocol_minor": PROTOCOL_MINOR,
        "request_id": uuid.uuid4().hex,
        "document_id": document_id,
        "document_revision": 1,
        "operation": operation,
        "params": params,
    }


def gate_limit(requirement_id: str) -> float:
    standard = json.loads(THRESHOLDS_PATH.read_text(encoding="utf-8"))
    requirement = next(
        item for item in standard["requirements"] if item["id"] == requirement_id
    )
    if requirement["statistic"] != "p95" or requirement["operator"] != "<=":
        raise ValueError(f"unexpected gate shape for {requirement_id}: {requirement}")
    return float(requirement["limit"])


def timed_region_calls(
    worker: PersistentWorker,
    document_id: str,
    *,
    analyzers: list[str] | None,
    region_chars: int,
    warmup: int,
    trials: int,
) -> list[float]:
    cancelled = threading.Event()
    for _ in range(warmup):
        response = worker.execute(
            request(
                "analyze_region",
                document_id,
                start_utf16=0,
                end_utf16=region_chars,
                analyzers=analyzers,
            ),
            cancelled,
        )
        if response.get("ok") is not True:
            raise RuntimeError(response)
    samples: list[float] = []
    for _ in range(trials):
        started = time.perf_counter()
        response = worker.execute(
            request(
                "analyze_region",
                document_id,
                start_utf16=0,
                end_utf16=region_chars,
                analyzers=analyzers,
            ),
            cancelled,
        )
        samples.append((time.perf_counter() - started) * 1000)
        if response.get("ok") is not True:
            raise RuntimeError(response)
    return samples


def evaluate_gates(payload: dict) -> list[dict]:
    checks = []
    for requirement_id, key in (("live-region", "live_region_ms"), ("live-pos", "live_pos_ms")):
        limit = gate_limit(requirement_id)
        measured = payload[key].get("p95_ms")
        checks.append({
            "gate": requirement_id,
            "metric": key,
            "requirement": f"p95 <= {limit:g} ms",
            "measured_p95_ms": measured,
            "pass": bool(measured is not None and measured <= limit),
        })
    return checks


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--words", type=int, default=100_000)
    parser.add_argument("--region-chars", type=int, default=8_000)
    parser.add_argument("--trials", type=int, default=30)
    parser.add_argument("--warmup", type=int, default=5)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    unit = "Mara noticed the clock and moved quickly through the quiet room. "
    text = " ".join((unit * (args.words // len(unit.split()) + 1)).split()[:args.words])
    document_id = uuid.uuid4().hex

    worker = PersistentWorker()
    try:
        opened = worker.execute(
            request("open_document", document_id, text=text, language="en"),
            threading.Event(),
        )
        if opened.get("ok") is not True:
            raise RuntimeError(opened)

        region_samples = timed_region_calls(
            worker, document_id,
            analyzers=None, region_chars=args.region_chars,
            warmup=args.warmup, trials=args.trials,
        )
        pos_samples = timed_region_calls(
            worker, document_id,
            analyzers=list(POS_ANALYZERS), region_chars=args.region_chars,
            warmup=args.warmup, trials=args.trials,
        )
    finally:
        worker.stop()

    payload = {
        "benchmark": "writer-engine-live-region",
        "created_utc": dt.datetime.now(dt.UTC).isoformat(timespec="milliseconds"),
        "evidence_class": "dev-host non-attesting (target 2vcpu-8gb-integrated-ssd)",
        "hardware": hardware_identity(),
        "worker_starts": worker.starts,
        "document_words": len(text.split()),
        "region_chars": args.region_chars,
        "warmup_trials": args.warmup,
        "timed_trials": args.trials,
        "pos_analyzers": POS_ANALYZERS,
        "live_region_ms": stats_block(region_samples),
        "live_pos_ms": stats_block(pos_samples),
    }
    payload["gates"] = evaluate_gates(payload)
    payload["overall_pass"] = all(check["pass"] for check in payload["gates"])

    output = args.output or (
        REPO_ROOT / "benchmark-results"
        / f"live-region-{dt.date.today().isoformat()}.json"
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    serialized = json.dumps(payload, indent=2)
    output.write_text(serialized + "\n", encoding="utf-8")
    print(serialized)
    print(f"wrote {output}")
    return 0 if payload["overall_pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
