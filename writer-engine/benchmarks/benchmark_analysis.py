from __future__ import annotations

import argparse
import hashlib
import json
import platform
import statistics
import time
from pathlib import Path

from backend.desktop_engine import analyze_text

UNITS = {
    "clean": "Mara opened the iron door and counted twelve coins before crossing the quiet room. ",
    "dense": "It was really quickly clearly deeply suddenly quietly a dime a dozen, not fear but memory. ",
    "dialogue": '"I already know," Mara said quietly. "Do you really?" Tomas asked. ',
    "unicode": "Mara \U0001f600 crossed the cafe, touched the na\u00efve carving, and moved quickly. ",
}


def corpus(unit: str, word_count: int) -> str:
    return " ".join((unit * (word_count // len(unit.split()) + 1)).split()[:word_count])


def percentile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int((len(ordered) - 1) * fraction))]


def run_case(label: str, text: str, *, trials: int, contextual: bool) -> dict:
    durations = []
    report = None
    for _ in range(trials):
        started = time.perf_counter()
        report = analyze_text(
            text,
            preset="full",
            confirm_adverbs=contextual,
        )
        durations.append((time.perf_counter() - started) * 1000)
    assert report is not None
    return {
        "label": label,
        "words": len(text.split()),
        "characters": len(text),
        "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
        "findings": len(report["diagnostics"]),
        "trials": trials,
        "milliseconds": {
            "p50": round(statistics.median(durations), 3),
            "p95": round(percentile(durations, 0.95), 3),
            "maximum": round(max(durations), 3),
        },
        "last_stage_timings_ms": report["stage_timings_ms"],
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--words", default="10000")
    parser.add_argument("--trials", type=int, default=3)
    parser.add_argument("--contextual", action="store_true")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    sizes = [int(value) for value in args.words.split(",")]
    results = [
        run_case(
            f"{name}-{size}", corpus(unit, size),
            trials=max(1, args.trials), contextual=args.contextual,
        )
        for size in sizes
        for name, unit in UNITS.items()
    ]
    payload = {
        "benchmark": "writer-engine-analysis",
        "python": platform.python_version(),
        "platform": platform.platform(),
        "contextual_pos": args.contextual,
        "results": results,
    }
    serialized = json.dumps(payload, indent=2)
    print(serialized)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(serialized + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
