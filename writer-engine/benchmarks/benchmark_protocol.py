from __future__ import annotations

import argparse
import json
import platform
import statistics
import threading
import time
import uuid
from pathlib import Path

from backend.sidecar import PROTOCOL_MAJOR, PROTOCOL_MINOR, PersistentWorker


def request(operation: str, **params):
    return {
        "protocol_major": PROTOCOL_MAJOR,
        "protocol_minor": PROTOCOL_MINOR,
        "request_id": uuid.uuid4().hex,
        "document_id": uuid.uuid4().hex,
        "document_revision": 1,
        "operation": operation,
        "params": params,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--words", type=int, default=10_000)
    parser.add_argument("--trials", type=int, default=5)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    unit = "Mara noticed the clock and moved quickly through the quiet room. "
    text = " ".join((unit * (args.words // len(unit.split()) + 1)).split()[:args.words])
    worker = PersistentWorker()
    samples = []
    try:
        for _ in range(max(1, args.trials)):
            started = time.perf_counter()
            response = worker.execute(
                request(
                    "analyze_document",
                    text=text,
                    analyzers=["filter_words", "possible_adverbs"],
                    confirm_adverbs=False,
                    initial_page_size=0,
                ),
                threading.Event(),
            )
            if response.get("ok") is not True:
                raise RuntimeError(response)
            samples.append((time.perf_counter() - started) * 1000)
        payload = {
            "benchmark": "writer-engine-persistent-protocol",
            "python": platform.python_version(),
            "platform": platform.platform(),
            "words": args.words,
            "trials": len(samples),
            "worker_starts": worker.starts,
            "milliseconds": {
                "minimum": round(min(samples), 3),
                "median": round(statistics.median(samples), 3),
                "maximum": round(max(samples), 3),
            },
        }
    finally:
        worker.stop()
    serialized = json.dumps(payload, indent=2)
    print(serialized)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(serialized + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
