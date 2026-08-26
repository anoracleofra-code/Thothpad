#!/usr/bin/env python3
"""Build and run ThothPad's headless native Qt efficiency collector through CTest."""

from __future__ import annotations

import argparse
import json
import math
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Iterable

import gate


NATIVE_CORPORA = ("clean", "dense", "unicode")
WORD_COUNTS = (10_000, 100_000, 500_000)
METRICS = {
    "window_visible_ms": "cold",
    "input_to_paint_ms": "warm",
    "gui_edit_ms": "warm",
    "ui_stall_ms": "warm",
    "scroll_frame_ms": "warm",
    "tooltip_lookup_ms": "warm",
    "open_to_editable_ms": "cold",
    "hydration_first_spans_ms": "warm",
    "hydration_complete_ms": "warm",
}


def native_corpus_text(corpus: str, word_count: int) -> str:
    words = gate.corpus_text(corpus, word_count).split()
    return "\n\n".join(
        " ".join(words[start:start + 100]) for start in range(0, len(words), 100)
    )


def parse_workload(value: str) -> tuple[str, int]:
    try:
        corpus, count = value.rsplit("-", 1)
        word_count = int(count)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"invalid workload {value!r}") from exc
    if corpus not in NATIVE_CORPORA or word_count not in WORD_COUNTS:
        raise argparse.ArgumentTypeError(
            f"workload must use {', '.join(NATIVE_CORPORA)} and {', '.join(map(str, WORD_COUNTS))}"
        )
    return corpus, word_count


def selected_workloads(values: Iterable[tuple[str, int]] | None) -> list[tuple[str, int]]:
    if values:
        return list(dict.fromkeys(values))
    return [(corpus, count) for count in WORD_COUNTS for corpus in NATIVE_CORPORA]


def find_collector(build_dir: Path) -> Path:
    names = {"efficiencycollector", "efficiencycollector.exe"}
    candidates = [
        path for path in build_dir.rglob("efficiencycollector*")
        if path.is_file() and path.name.lower() in names and "cmakefiles" not in {
            part.lower() for part in path.parts
        }
    ]
    if not candidates:
        raise FileNotFoundError(f"efficiencycollector executable was not found below {build_dir}")
    return max(candidates, key=lambda path: path.stat().st_mtime_ns)


def build_environment(build_dir: Path) -> dict[str, str]:
    environment = os.environ.copy()
    if os.name != "nt" or environment.get("INCLUDE"):
        return environment
    cache_text = (build_dir / "CMakeCache.txt").read_text(encoding="utf-8", errors="replace")
    compiler_line = next(
        (line for line in cache_text.splitlines() if line.startswith("CMAKE_CXX_COMPILER:")), ""
    )
    compiler = compiler_line.partition("=")[2].replace("\\", "/")
    marker = "/VC/Tools/MSVC/"
    if marker not in compiler:
        raise RuntimeError("CMakeCache.txt does not identify an MSVC toolset")
    installation_text, toolset_tail = compiler.split(marker, 1)
    toolset_version = toolset_tail.split("/", 1)[0]
    installation = Path(installation_text)
    developer_shell = installation / "Common7/Tools/VsDevCmd.bat"
    if not developer_shell.is_file():
        raise FileNotFoundError(f"the configured Visual Studio environment was not found: {developer_shell}")
    with tempfile.TemporaryDirectory() as directory:
        script = Path(directory) / "thothpad-msvc-environment.cmd"
        script.write_text(
            f'@call "{developer_shell}" -no_logo -arch=x64 -host_arch=x64 -vcvars_ver={toolset_version} >nul\n'
            "@if errorlevel 1 exit /b %errorlevel%\n"
            "@set\n",
            encoding="utf-8",
        )
        initialized = subprocess.run(
            ["cmd.exe", "/d", "/c", str(script)],
            check=False,
            capture_output=True,
            text=True,
        )
    if initialized.returncode:
        raise RuntimeError(initialized.stderr.strip() or "Visual Studio environment initialization failed")
    for line in initialized.stdout.splitlines():
        name, separator, value = line.partition("=")
        if separator and name:
            environment[name] = value
    if not environment.get("INCLUDE"):
        raise RuntimeError("Visual Studio environment did not provide the C++ include path")
    return environment


def build_identity(collector: Path, build_dir: Path) -> dict[str, Any]:
    identity = gate.build_metadata(None)
    cache = build_dir / "CMakeCache.txt"
    identity.update({
        "collector_artifact": str(collector.resolve()),
        "collector_sha256": gate.sha256_file(collector),
        "cmake_cache_sha256": gate.sha256_file(cache) if cache.is_file() else "unavailable",
    })
    return identity


def make_config(
    output: Path,
    suite: str,
    workloads: Iterable[tuple[str, int]],
    collector: Path,
    build_dir: Path,
) -> tuple[Path, dict[str, Any]]:
    warm_trials = 1 if suite == "smoke" else 30
    cold_trials = 1 if suite == "smoke" else 20
    fixture_dir = output / "corpora"
    raw_dir = output / "raw"
    fixture_dir.mkdir(parents=True, exist_ok=True)
    raw_dir.mkdir(parents=True, exist_ok=True)

    fixtures: list[dict[str, Any]] = []
    for corpus, word_count in workloads:
        text = native_corpus_text(corpus, word_count)
        encoded = text.encode("utf-8")
        path = fixture_dir / f"{corpus}-{word_count}.md"
        path.write_bytes(encoded)
        fixtures.append({
            "id": f"{corpus}-{word_count}",
            "corpus": corpus,
            "word_count": word_count,
            "path": str(path.resolve()),
            "corpus_sha256": gate.sha256_bytes(encoded),
        })

    runners = gate.read_json(gate.RUNNERS_PATH)
    config = {
        "schema_version": 1,
        "suite": suite,
        "warm_trials": warm_trials,
        "cold_trials": cold_trials,
        "output_directory": str(raw_dir.resolve()),
        "platform": gate.platform_id(),
        "hardware": gate.hardware_metadata(),
        "build": build_identity(collector, build_dir),
        "target": {
            "id": runners["target"]["id"],
            "attested": os.environ.get("THOTHPAD_BENCHMARK_TARGET_ATTESTED") == "1",
        },
        "fixtures": fixtures,
    }
    config_path = output / "native-collector-config.json"
    gate.write_json(config_path, config)
    return config_path, config


def validate_result(result: dict[str, Any], config: dict[str, Any], fixture: dict[str, Any]) -> None:
    required = {
        "schema_version", "run_id", "created_utc", "suite", "platform", "hardware",
        "build", "target", "workload", "metrics",
    }
    missing = required.difference(result)
    if missing:
        raise ValueError(f"result is missing fields: {', '.join(sorted(missing))}")
    if result["schema_version"] != 1 or result["suite"] != config["suite"]:
        raise ValueError("result schema or suite does not match the collector configuration")
    for key in ("platform", "hardware", "build", "target"):
        if result[key] != config[key]:
            raise ValueError(f"result {key} identity does not match the collector configuration")
    expected_workload = {
        "id": fixture["id"],
        "corpus": fixture["corpus"],
        "word_count": fixture["word_count"],
        "corpus_sha256": fixture["corpus_sha256"],
    }
    if result["workload"] != expected_workload:
        raise ValueError("result workload identity does not match its fixture")

    metrics = result["metrics"]
    if not isinstance(metrics, list):
        raise ValueError("result metrics must be an array")
    by_name = {item.get("metric"): item for item in metrics if isinstance(item, dict)}
    if set(by_name) != set(METRICS):
        raise ValueError(f"native metric set differs: expected {sorted(METRICS)}, got {sorted(by_name)}")
    expected_counts = {"warm": config["warm_trials"], "cold": config["cold_trials"]}
    for name, trial_kind in METRICS.items():
        item = by_name[name]
        samples = item.get("samples")
        if (
            item.get("unit") != "ms"
            or item.get("status") != "measured"
            or item.get("collector") != "qt-native-efficiency-v1"
            or item.get("trial_kind") != trial_kind
            or not isinstance(samples, list)
            or len(samples) != expected_counts[trial_kind]
            or any(not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0 for value in samples)
        ):
            raise ValueError(f"invalid samples or metadata for {name}")

    try:
        import jsonschema  # type: ignore
    except ImportError:
        return
    jsonschema.validate(result, gate.read_json(gate.SCHEMA_PATH))


def validate_outputs(config: dict[str, Any]) -> list[Path]:
    raw_dir = Path(config["output_directory"])
    outputs: list[Path] = []
    for fixture in config["fixtures"]:
        path = raw_dir / f"{fixture['id']}.native-ui.json"
        if not path.is_file():
            raise FileNotFoundError(f"collector did not produce {path}")
        result = json.loads(path.read_text(encoding="utf-8"))
        validate_result(result, config, fixture)
        outputs.append(path)
    return outputs


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("legacy_output", nargs="?", type=Path, help=argparse.SUPPRESS)
    result.add_argument("--output", type=Path, help="evidence root (default: benchmark-results/native)")
    result.add_argument("--build-dir", type=Path, required=True, help="configured CMake build directory")
    result.add_argument("--suite", choices=("smoke", "certification"), default="smoke")
    result.add_argument("--config", default="Release", help="CMake/CTest configuration")
    result.add_argument("--no-build", action="store_true", help="use an existing collector build")
    result.add_argument(
        "--workload", action="append", type=parse_workload,
        help="run one clean/dense/unicode workload; repeat as needed",
    )
    return result


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    output = (args.output or args.legacy_output or Path("benchmark-results/native")).resolve()
    build_dir = args.build_dir.resolve()
    if not (build_dir / "CMakeCache.txt").is_file():
        print(f"Configured CMake build directory not found: {build_dir}", file=sys.stderr)
        return 2
    try:
        environment = build_environment(build_dir)
    except (OSError, RuntimeError) as error:
        print(str(error), file=sys.stderr)
        return 2
    if not args.no_build:
        completed = subprocess.run(
            ["cmake", "--build", str(build_dir), "--config", args.config, "--target", "efficiencycollector"],
            cwd=gate.ROOT,
            env=environment,
            check=False,
        )
        if completed.returncode:
            return completed.returncode
    try:
        collector = find_collector(build_dir)
        config_path, config = make_config(
            output, args.suite, selected_workloads(args.workload), collector, build_dir
        )
    except (OSError, ValueError) as error:
        print(str(error), file=sys.stderr)
        return 2

    ctest = shutil.which("ctest")
    if not ctest:
        print("ctest was not found on PATH", file=sys.stderr)
        return 2
    environment["THOTHPAD_EFFICIENCY_CONFIG"] = str(config_path)
    environment["QT_QPA_PLATFORM"] = "offscreen"
    completed = subprocess.run(
        [ctest, "--test-dir", str(build_dir), "-C", args.config, "--output-on-failure", "-R", "^efficiencycollector$"],
        cwd=gate.ROOT,
        env=environment,
        check=False,
    )
    if completed.returncode:
        return completed.returncode
    try:
        outputs = validate_outputs(config)
    except (OSError, ValueError, json.JSONDecodeError) as error:
        print(f"Collector evidence validation failed: {error}", file=sys.stderr)
        return 2
    print(
        f"Validated {len(outputs)} native Qt result files from {args.suite} mode. "
        "These measurements are evidence inputs, not an S certification."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
