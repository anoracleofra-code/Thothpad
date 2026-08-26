#!/usr/bin/env python3
"""Run, aggregate, and certify ThothPad's sealed performance gate."""

from __future__ import annotations

import argparse
import ctypes
import hashlib
import json
import math
import os
import platform
import statistics
import subprocess
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


ROOT = Path(__file__).resolve().parents[1]
WORKLOADS_PATH = ROOT / "benchmarks" / "workloads.json"
RUNNERS_PATH = ROOT / "benchmarks" / "runner-definitions.json"
THRESHOLDS_PATH = ROOT / "benchmarks" / "thresholds" / "s-grade-v1.json"
THRESHOLDS_LOCK_PATH = ROOT / "benchmarks" / "thresholds" / "s-grade-v1.sha256"
SCHEMA_PATH = ROOT / "benchmarks" / "schema" / "benchmark-result.schema.json"

TEMPLATES = {
    "clean": (
        "Mara opened the iron door and counted twelve coins before crossing the quiet room. "
        "Rain tapped the eastern window while the clerk recorded each payment in a blue ledger. "
    ),
    "representative": (
        "Tomas checked the worn compass, crossed the market, and found Inez beside the fountain. "
        "She lowered her voice. “The north road closed at dawn,” she said. He studied the muddy cart tracks. "
    ),
    "dialogue": (
        "“You heard the bell?” Nia asked. “I heard two,” Rowan said, “and I don't trust the second.” "
        "“Then we're leaving.” She took his coat. “Tell me the rest outside.” "
    ),
    "unicode": (
        "Zoë passed the café where José wrote ‘mañana’ beside 東京 and Αθήνα. "
        "A family waved 👋🏽; her élan survived the cold, the curly quotes, and the long dash — intact. "
    ),
    "dense": (
        "It was really clearly deeply suddenly quiet, not fear but memory, a dime a dozen as shadows danced. "
        "He quickly watched, felt, and noticed the air grow thick while time slowed and his heart pounded. "
    ),
}


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def corpus_text(corpus: str, word_count: int) -> str:
    words = TEMPLATES[corpus].split()
    repeats, remainder = divmod(word_count, len(words))
    text = " ".join(words * repeats + words[:remainder])
    if len(text.split()) != word_count:
        raise RuntimeError(f"fixture {corpus}-{word_count} does not contain exactly {word_count} words")
    return text


def platform_id() -> str:
    value = platform.system().lower()
    return {"darwin": "macos"}.get(value, value)


def memory_bytes() -> int:
    if sys.platform == "win32":
        class MemoryStatus(ctypes.Structure):
            _fields_ = [
                ("length", ctypes.c_ulong),
                ("memory_load", ctypes.c_ulong),
                ("total_physical", ctypes.c_ulonglong),
                ("available_physical", ctypes.c_ulonglong),
                ("total_page_file", ctypes.c_ulonglong),
                ("available_page_file", ctypes.c_ulonglong),
                ("total_virtual", ctypes.c_ulonglong),
                ("available_virtual", ctypes.c_ulonglong),
                ("available_extended_virtual", ctypes.c_ulonglong),
            ]
        status = MemoryStatus()
        status.length = ctypes.sizeof(status)
        if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
            return int(status.total_physical)
    if hasattr(os, "sysconf"):
        try:
            return int(os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES"))
        except (ValueError, OSError):
            pass
    return 1


def git_output(*args: str) -> str:
    completed = subprocess.run(
        ["git", *args], cwd=ROOT, check=False, capture_output=True, text=True
    )
    return completed.stdout.strip() if completed.returncode == 0 else "unknown"


def source_hash() -> str:
    listing = git_output("ls-files", "--cached", "--others", "--exclude-standard", "-z")
    paths = sorted(item for item in listing.split("\0") if item)
    digest = hashlib.sha256()
    for relative in paths:
        path = ROOT / relative
        if not path.is_file():
            continue
        digest.update(relative.replace("\\", "/").encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def build_metadata(artifact: Path | None) -> dict[str, Any]:
    commit = git_output("rev-parse", "HEAD")
    dirty = bool(git_output("status", "--porcelain"))
    return {
        "git_commit": commit,
        "dirty": dirty,
        "source_sha256": sha256_file(artifact) if artifact else source_hash(),
        **({"artifact": str(artifact.resolve())} if artifact else {}),
    }


def hardware_metadata() -> dict[str, Any]:
    return {
        "logical_cpus": os.cpu_count() or 1,
        "memory_bytes": memory_bytes(),
        "machine": platform.machine() or "unknown",
        "processor": platform.processor() or "unknown",
        "os_release": platform.release(),
        "python": platform.python_version(),
    }


def fixture_records(corpora: Iterable[str], word_counts: Iterable[int]) -> list[dict[str, Any]]:
    records = []
    for word_count in word_counts:
        for corpus in corpora:
            text = corpus_text(corpus, word_count)
            records.append({
                "id": f"{corpus}-{word_count}",
                "corpus": corpus,
                "word_count": word_count,
                "bytes": len(text.encode("utf-8")),
                "sha256": sha256_bytes(text.encode("utf-8")),
            })
    return records


def command_verify(_: argparse.Namespace) -> int:
    errors: list[str] = []
    for path in (WORKLOADS_PATH, RUNNERS_PATH, THRESHOLDS_PATH, SCHEMA_PATH):
        try:
            read_json(path)
        except Exception as exc:  # configuration diagnostics belong in one report
            errors.append(f"{path.relative_to(ROOT)}: {exc}")
    if THRESHOLDS_LOCK_PATH.exists():
        expected = THRESHOLDS_LOCK_PATH.read_text(encoding="ascii").split()[0]
        actual = sha256_file(THRESHOLDS_PATH)
        if actual != expected:
            errors.append(f"immutable threshold hash mismatch: expected {expected}, got {actual}")
    else:
        errors.append("threshold lock file is missing")

    workloads = read_json(WORKLOADS_PATH)
    actual_matrix = {
        item["id"] for item in fixture_records(
            [item["id"] for item in workloads["corpora"]], workloads["word_counts"]
        )
    }
    if actual_matrix != set(workloads["required_matrix"]):
        errors.append("required workload matrix does not match corpus and word-count definitions")
    requirements = read_json(THRESHOLDS_PATH)["requirements"]
    ids = [item["id"] for item in requirements]
    if len(ids) != len(set(ids)):
        errors.append("threshold requirement IDs are not unique")
    if errors:
        print("\n".join(f"ERROR: {item}" for item in errors), file=sys.stderr)
        return 1
    print(f"Benchmark contract valid; {len(actual_matrix)} workloads and {len(ids)} immutable S requirements.")
    return 0


def command_fixtures(args: argparse.Namespace) -> int:
    workloads = read_json(WORKLOADS_PATH)
    output = args.output.resolve()
    records = fixture_records([item["id"] for item in workloads["corpora"]], workloads["word_counts"])
    if args.materialize:
        for item in records:
            path = output / f"{item['id']}.md"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(corpus_text(item["corpus"], item["word_count"]), encoding="utf-8", newline="\n")
    manifest = {
        "schema_version": 1,
        "fixture_version": workloads["fixture_version"],
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "materialized": args.materialize,
        "fixtures": records,
    }
    write_json(output / "corpus-manifest.json", manifest)
    print(f"Recorded {len(records)} deterministic corpus hashes in {output / 'corpus-manifest.json'}")
    return 0


def result_envelope(
    suite: str, corpus: str, word_count: int, corpus_hash: str,
    build: dict[str, Any], hardware: dict[str, Any],
) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "run_id": str(uuid.uuid4()),
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "suite": suite,
        "platform": platform_id(),
        "hardware": hardware,
        "build": build,
        "target": {
            "id": read_json(RUNNERS_PATH)["target"]["id"],
            "attested": os.environ.get("THOTHPAD_BENCHMARK_TARGET_ATTESTED") == "1",
        },
        "workload": {
            "id": f"{corpus}-{word_count}",
            "corpus": corpus,
            "word_count": word_count,
            "corpus_sha256": corpus_hash,
        },
        "metrics": [],
        "automatic_failures": [],
    }


def command_run_engine(args: argparse.Namespace) -> int:
    policy = read_json(RUNNERS_PATH)["trial_policy"][args.suite]
    trials = args.trials if args.trials is not None else policy["warm"]
    if args.suite == "certification" and trials < policy["warm"]:
        raise SystemExit(f"certification requires at least {policy['warm']} warm trials")
    sys.path.insert(0, str(ROOT / "writer-engine"))
    from backend.desktop_engine import analyze_text  # type: ignore

    workloads = read_json(WORKLOADS_PATH)
    corpora = args.corpus or [item["id"] for item in workloads["corpora"]]
    word_counts = args.word_count or workloads["word_counts"]
    output = args.output.resolve() / "raw" / platform_id()
    artifact = args.build_artifact.resolve() if args.build_artifact else None
    build = build_metadata(artifact)
    hardware = hardware_metadata()
    failures = 0
    for item in fixture_records(corpora, word_counts):
        text = corpus_text(item["corpus"], item["word_count"])
        result = result_envelope(args.suite, item["corpus"], item["word_count"], item["sha256"], build, hardware)
        timings: list[float] = []
        counts: list[float] = []
        errors: list[str] = []
        for _ in range(trials):
            started = time.perf_counter_ns()
            try:
                report = analyze_text(text, preset="full", confirm_adverbs=True)
                timings.append((time.perf_counter_ns() - started) / 1_000_000)
                counts.append(float(len(report.get("diagnostics", []))))
            except Exception as exc:
                errors.append(f"{type(exc).__name__}: {exc}")
                break
        status = "measured" if not errors and len(timings) == trials else "error"
        result["metrics"] = [
            {
                "metric": "full_analysis_ms", "unit": "ms", "status": status,
                "collector": "engine-source", "trial_kind": "warm", "samples": timings,
                "notes": "; ".join(errors),
            },
            {
                "metric": "diagnostic_count", "unit": "count", "status": status,
                "collector": "engine-source", "trial_kind": "warm", "samples": counts,
                "notes": "; ".join(errors),
            },
        ]
        if errors:
            failures += 1
        destination = output / f"engine-{item['id']}.json"
        write_json(destination, result)
        print(f"{item['id']}: {status}; {len(timings)}/{trials} measured trials")
    return 1 if failures else 0


def percentile(samples: list[float], fraction: float) -> float:
    if not samples:
        raise ValueError("percentile requires samples")
    ordered = sorted(samples)
    return ordered[max(0, math.ceil(fraction * len(ordered)) - 1)]


def summarize(samples: list[float]) -> dict[str, float | int]:
    return {
        "count": len(samples),
        "min": min(samples),
        "p50": percentile(samples, 0.50),
        "p95": percentile(samples, 0.95),
        "p99": percentile(samples, 0.99),
        "max": max(samples),
        "mean": statistics.fmean(samples),
    }


def load_results(paths: Iterable[Path]) -> list[dict[str, Any]]:
    files: list[Path] = []
    for path in paths:
        if path.is_dir():
            files.extend(path.rglob("*.json"))
        elif path.suffix == ".json":
            files.append(path)
    results = []
    for path in sorted(set(files)):
        try:
            value = read_json(path)
        except (OSError, json.JSONDecodeError):
            continue
        if value.get("schema_version") == 1 and "metrics" in value and "workload" in value:
            results.append(value)
    return results


def aggregate_results(results: list[dict[str, Any]]) -> dict[str, Any]:
    rows = []
    for result in results:
        for metric in result["metrics"]:
            row = {
                "platform": result["platform"],
                "suite": result["suite"],
                "target_attested": result["target"]["attested"],
                **result["workload"],
                "metric": metric["metric"],
                "unit": metric["unit"],
                "status": metric["status"],
                "collector": metric["collector"],
                "trial_kind": metric.get("trial_kind", "single"),
                "samples": metric["samples"],
            }
            if metric["status"] == "measured" and metric["samples"]:
                row["summary"] = summarize(metric["samples"])
            rows.append(row)
    return {
        "schema_version": 1,
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "result_count": len(results),
        "rows": rows,
    }


def command_aggregate(args: argparse.Namespace) -> int:
    aggregate = aggregate_results(load_results(args.paths))
    write_json(args.output.resolve(), aggregate)
    print(f"Aggregated {aggregate['result_count']} result files into {args.output}")
    return 0


def compare(value: float, operator: str, limit: float) -> bool:
    return {"<=": value <= limit, "<": value < limit, ">=": value >= limit, "==": value == limit}[operator]


def command_evaluate(args: argparse.Namespace) -> int:
    results = load_results(args.paths)
    aggregate = aggregate_results(results)
    standard = read_json(THRESHOLDS_PATH)
    workloads = read_json(WORKLOADS_PATH)
    required_platforms = set(read_json(RUNNERS_PATH)["platforms"])
    required_corpora = {item["id"] for item in workloads["corpora"]}
    checks = []
    for requirement in standard["requirements"]:
        expected_corpora = {requirement["corpus"]} if "corpus" in requirement else (
            required_corpora if "word_count" in requirement else {"global"}
        )
        for expected_platform in sorted(required_platforms):
            for expected_corpus in sorted(expected_corpora):
                matches = [row for row in aggregate["rows"] if
                    row["platform"] == expected_platform and
                    row["metric"] == requirement["metric"] and
                    row["corpus"] == expected_corpus and
                    ("word_count" not in requirement or row["word_count"] == requirement["word_count"])
                ]
                measured = [row for row in matches if row["status"] == "measured" and row.get("summary")]
                status = "uncertified"
                observed = None
                notes = "missing measured evidence"
                if measured:
                    expected_trial_kind = requirement.get("trial_kind", "warm")
                    minimum_samples = (
                        1 if expected_trial_kind == "single" else
                        standard["evidence_policy"][f"{expected_trial_kind}_min_samples"]
                    )
                    eligible = [row for row in measured if
                        row["suite"] == "certification" and
                        row["trial_kind"] == expected_trial_kind and
                        row["summary"]["count"] >= minimum_samples and
                        (row["target_attested"] or requirement["domain"] in {"accuracy", "packaging"})
                    ]
                    if not eligible:
                        notes = "evidence is not a target-attested certification run"
                    else:
                        statistic = requirement["statistic"]
                        values = [float(row["summary"][statistic]) for row in eligible]
                        observed = max(values) if requirement["operator"] in {"<=", "<"} else min(values)
                        status = "pass" if compare(observed, requirement["operator"], float(requirement["limit"])) else "fail"
                        notes = ""
                checks.append({
                    "requirement_id": requirement["id"], "domain": requirement["domain"],
                    "platform": expected_platform, "corpus": expected_corpus,
                    "word_count": requirement.get("word_count"), "metric": requirement["metric"],
                    "statistic": requirement["statistic"], "operator": requirement["operator"],
                    "limit": requirement["limit"], "observed": observed, "status": status, "notes": notes,
                })
    automatic_failures = sorted({failure for result in results for failure in result.get("automatic_failures", [])})
    statuses = {item["status"] for item in checks}
    overall = "fail" if automatic_failures or "fail" in statuses else ("uncertified" if "uncertified" in statuses else "s")
    certification = {
        "schema_version": 1,
        "standard": standard["standard"],
        "thresholds_sha256": sha256_file(THRESHOLDS_PATH),
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "overall": overall,
        "automatic_failures": automatic_failures,
        "summary": {
            "pass": sum(item["status"] == "pass" for item in checks),
            "fail": sum(item["status"] == "fail" for item in checks),
            "uncertified": sum(item["status"] == "uncertified" for item in checks),
        },
        "checks": checks,
    }
    write_json(args.output.resolve(), certification)
    print(json.dumps({"overall": overall, **certification["summary"]}, indent=2))
    return 0 if overall == "s" else (1 if args.require_s else 0)


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    commands = result.add_subparsers(dest="command", required=True)
    verify = commands.add_parser("verify", help="validate and hash-check the benchmark contract")
    verify.set_defaults(func=command_verify)
    fixtures = commands.add_parser("fixtures", help="hash or materialize deterministic corpora")
    fixtures.add_argument("--output", type=Path, required=True)
    fixtures.add_argument("--materialize", action="store_true")
    fixtures.set_defaults(func=command_fixtures)
    run = commands.add_parser("run-engine", help="measure the existing deterministic engine")
    run.add_argument("--suite", choices=("smoke", "certification"), default="smoke")
    run.add_argument("--output", type=Path, required=True)
    run.add_argument("--corpus", action="append", choices=tuple(TEMPLATES))
    run.add_argument("--word-count", action="append", type=int, choices=(10000, 100000, 500000))
    run.add_argument("--trials", type=int)
    run.add_argument("--build-artifact", type=Path)
    run.set_defaults(func=command_run_engine)
    aggregate = commands.add_parser("aggregate", help="aggregate conforming raw result files")
    aggregate.add_argument("paths", type=Path, nargs="+")
    aggregate.add_argument("--output", type=Path, required=True)
    aggregate.set_defaults(func=command_aggregate)
    evaluate = commands.add_parser("evaluate", help="evaluate evidence against immutable S thresholds")
    evaluate.add_argument("paths", type=Path, nargs="+")
    evaluate.add_argument("--output", type=Path, required=True)
    evaluate.add_argument("--require-s", action="store_true")
    evaluate.set_defaults(func=command_evaluate)
    return result


def main() -> int:
    args = parser().parse_args()
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
