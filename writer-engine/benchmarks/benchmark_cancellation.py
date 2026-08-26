"""Cancellation race probe for the ThothPad desktop sidecar protocol.

Runs sequential cancel races against a live SidecarServer driven through the
real framing/dispatch path (encode_frame -> serve() -> _accept -> worker thread
-> _cancel_request -> response envelopes), then reports:

- cancellation_ack_ms:   cancel-request frame written -> cancel ack observed.
- cancellation_stop_ms:  cancel ack observed -> cooperative workload stopped.
- stale successful responses emitted for a cancelled request (must be 0).

Usage:
    python benchmarks/benchmark_cancellation.py [--trials N] [--output PATH]

Gates measured against benchmarks/thresholds/s-grade-v1.json:
    cancel-ack     p95 <= 50 ms
    cancel-stop    p95 <= 250 ms
    stale-results  max == 0
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import statistics
import sys
import threading
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.sidecar import (  # noqa: E402
    ENGINE_VERSION,
    PROTOCOL_MAJOR,
    PROTOCOL_MINOR,
    SidecarServer,
    encode_frame,
    read_frame,
)
from backend.text_utils import cancellation_checkpoint  # noqa: E402

GATES = {
    "cancel_ack_p95_ms_max": 50.0,
    "cancel_stop_p95_ms_max": 250.0,
    "stale_successful_responses_max": 0,
}

RACE_TIMEOUT_S = 30.0
WARMUP_RACES = 25


def request(operation: str, request_id: str, **params) -> dict:
    return {
        "protocol_major": PROTOCOL_MAJOR,
        "protocol_minor": PROTOCOL_MINOR,
        "request_id": request_id,
        "document_id": f"doc-{uuid.uuid4().hex[:12]}",
        "document_revision": 1,
        "operation": operation,
        "params": params,
    }


class MemoryPipe:
    """Blocking in-memory byte stream compatible with sidecar.read_frame."""

    def __init__(self) -> None:
        self._buffer = bytearray()
        self._condition = threading.Condition()
        self._closed = False

    def write(self, data) -> int:
        with self._condition:
            self._buffer.extend(data)
            self._condition.notify_all()
        return len(data)

    def flush(self) -> None:
        pass

    def close(self) -> None:
        with self._condition:
            self._closed = True
            self._condition.notify_all()

    def readline(self, limit: int = -1) -> bytes:
        with self._condition:
            while True:
                index = self._buffer.find(b"\n")
                if index >= 0:
                    size = index + 1
                    if 0 <= limit < size:
                        size = limit
                    chunk = bytes(self._buffer[:size])
                    del self._buffer[:size]
                    return chunk
                if self._closed:
                    chunk = bytes(self._buffer[:limit]) if limit and limit > 0 else bytes(self._buffer)
                    del self._buffer[:]
                    return chunk
                self._condition.wait()

    def read(self, size: int | None = -1) -> bytes:
        with self._condition:
            while not self._buffer and not self._closed:
                self._condition.wait()
            if not self._buffer:
                return b""
            if size is None or size < 0:
                take = len(self._buffer)
            else:
                take = min(size, len(self._buffer))
            chunk = bytes(self._buffer[:take])
            del self._buffer[:take]
            return chunk


class RaceProbeAnalyzer:
    """Cooperative analyzer that spins on the shared cancellation checkpoint."""

    name = "race_probe"

    def __init__(self) -> None:
        self.started = threading.Event()
        self.stopped = threading.Event()

    def analyze(self, text: str, _profile=None):
        self.started.set()
        try:
            cursor = 0
            while True:
                cancellation_checkpoint()
                cursor = text.find("the", cursor)
                if cursor < 0:
                    cursor = 0
                # Matches the cooperative pattern in tests/test_sidecar.py:
                # checkpoint plus a bounded sleep so the GIL is released.
                time.sleep(0.001)
        finally:
            self.stopped.set()


class RaceSession:
    """One SidecarServer driven over in-memory pipes with an observer thread."""

    def __init__(self) -> None:
        self.requests = MemoryPipe()
        self.responses = MemoryPipe()
        self.server = SidecarServer(self.requests, self.responses)
        self.server_thread = threading.Thread(target=self.server.serve, daemon=True)
        self.frames: dict[str, list[tuple[float, dict]]] = {}
        self.frames_lock = threading.Condition()
        self.observer_errors: list[Exception] = []
        self.probe: RaceProbeAnalyzer | None = None
        install_probe_registry_once()
        self.observer = threading.Thread(target=self._observe, daemon=True)
        self.observer.start()
        self.server_thread.start()

    def send(self, message: dict) -> None:
        self.requests.write(encode_frame(message))

    def _observe(self) -> None:
        try:
            while True:
                frame = read_frame(self.responses)
                if frame is None:
                    return
                stamp = time.perf_counter()
                with self.frames_lock:
                    self.frames.setdefault(str(frame.get("request_id")), []).append((stamp, frame))
                    self.frames_lock.notify_all()
        except Exception as exc:  # pragma: no cover - observer diagnostics
            self.observer_errors.append(exc)

    def wait_frames(self, request_ids: set[str], timeout: float) -> dict[str, tuple[float, dict]]:
        deadline = time.monotonic() + timeout
        with self.frames_lock:
            while not request_ids <= set(self.frames):
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError(f"no response for {sorted(request_ids - set(self.frames))}")
                self.frames_lock.wait(remaining)
            return {rid: self.frames[rid][-1] for rid in request_ids}

    def run_race(self, index: int, region_text: str) -> dict:
        analyze_id = f"race-{index}"
        cancel_id = f"cancel-{index}"
        self.probe = RaceProbeAnalyzer()
        _patched_registry["probe"] = self.probe

        self.send(request(
            "analyze_region", analyze_id,
            text=region_text, analyzers=[RaceProbeAnalyzer.name],
        ))
        if not self.probe.started.wait(RACE_TIMEOUT_S):
            raise TimeoutError(f"race {index}: analyzer never started")

        started_at = time.perf_counter()
        self.send(request("cancel", cancel_id, target_request_id=analyze_id))

        seen = self.wait_frames({analyze_id, cancel_id}, RACE_TIMEOUT_S)
        ack_stamp = seen[cancel_id][0]
        ack_frame = seen[cancel_id][1]
        analysis_frame = seen[analyze_id][1]
        self.probe.stopped.wait(RACE_TIMEOUT_S)
        stopped_at = time.perf_counter()

        accepted = bool(ack_frame.get("ok") is True and ack_frame.get("result", {}).get("cancelled") is True)
        stale_success = analysis_frame.get("ok") is True
        sample = {
            "trial": index,
            "accepted": accepted,
            "cancel_written_to_ack_ms": (ack_stamp - started_at) * 1000.0,
            "ack_to_worker_stopped_ms": (stopped_at - ack_stamp) * 1000.0,
            "cancel_written_to_stopped_ms": (stopped_at - started_at) * 1000.0,
            "stale_success": bool(stale_success),
            "analysis_error_code": (analysis_frame.get("error") or {}).get("code"),
        }
        with self.frames_lock:
            self.frames.pop(analyze_id, None)
            self.frames.pop(cancel_id, None)
        return sample

    def close(self) -> None:
        self.send(request("shutdown", "shutdown-final"))
        self.wait_frames({"shutdown-final"}, RACE_TIMEOUT_S)
        self.server_thread.join(RACE_TIMEOUT_S)


_patched_registry: dict[str, RaceProbeAnalyzer] = {}
_probe_installed = False


def install_probe_registry_once() -> None:
    global _probe_installed
    if _probe_installed:
        return
    _probe_installed = True
    import backend.analyzers.base as base

    real_analyzers = base._analyzers

    def registry():
        merged = real_analyzers()
        return {**merged, RaceProbeAnalyzer.name: _patched_registry.get("probe") or RaceProbeAnalyzer()}

    base._analyzers = registry


def percentile(samples: list[float], fraction: float) -> float:
    ordered = sorted(samples)
    if not ordered:
        return 0.0
    position = (len(ordered) - 1) * fraction
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    weight = position - lower
    return ordered[lower] * (1.0 - weight) + ordered[upper] * weight


def stats_block(samples: list[float]) -> dict:
    if not samples:
        return {"count": 0}
    return {
        "count": len(samples),
        "min_ms": round(min(samples), 4),
        "p50_ms": round(percentile(samples, 0.50), 4),
        "p95_ms": round(percentile(samples, 0.95), 4),
        "p99_ms": round(percentile(samples, 0.99), 4),
        "max_ms": round(max(samples), 4),
        "mean_ms": round(statistics.fmean(samples), 4),
    }


def hardware_identity() -> dict:
    identity = {
        "system": platform.system(),
        "release": platform.release(),
        "machine": platform.machine(),
        "processor": platform.processor(),
        "logical_cpus": os.cpu_count(),
        "python": platform.python_version(),
    }
    identifier = os.environ.get("PROCESSOR_IDENTIFIER")
    if identifier:
        identity["processor_identifier"] = identifier
    try:
        import ctypes

        class MemoryStatus(ctypes.Structure):
            _fields_ = [
                ("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
            ]

        status = MemoryStatus()
        status.dwLength = ctypes.sizeof(MemoryStatus)
        if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
            identity["total_memory_gb"] = round(status.ullTotalPhys / 1024**3, 1)
    except Exception:
        pass
    return identity


def region_text(characters: int) -> str:
    unit = "Mara noticed the clock and moved quickly through the quiet room. "
    text = (unit * (characters // len(unit) + 1))[:characters]
    return text.rstrip() + "."


def evaluate_gates(payload: dict) -> list[dict]:
    ack_p95 = payload["cancellation_ack_ms"].get("p95_ms")
    stop_p95 = payload["cancellation_stop_ms"].get("p95_ms")
    stale = payload["stale_successful_responses"]
    return [
        {
            "gate": "cancel-ack",
            "requirement": "p95 <= 50 ms",
            "measured": ack_p95,
            "pass": bool(ack_p95 is not None and ack_p95 <= GATES["cancel_ack_p95_ms_max"]),
        },
        {
            "gate": "cancel-stop",
            "requirement": "p95 <= 250 ms",
            "measured": stop_p95,
            "pass": bool(stop_p95 is not None and stop_p95 <= GATES["cancel_stop_p95_ms_max"]),
        },
        {
            "gate": "stale-results",
            "requirement": "max == 0",
            "measured": stale,
            "pass": stale == GATES["stale_successful_responses_max"],
        },
    ]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--trials", type=int, default=1000)
    parser.add_argument("--region-chars", type=int, default=8000)
    parser.add_argument("--output", type=Path, default=None)
    arguments = parser.parse_args()

    output = arguments.output or (
        Path(__file__).resolve().parents[2]
        / "benchmark-results"
        / f"cancellation-probe-{datetime.now(UTC).date().isoformat()}.json"
    )

    session = RaceSession()
    samples: list[dict] = []
    status = 0
    try:
        session.send(request("initialize", "initialize-bench"))
        session.wait_frames({"initialize-bench"}, RACE_TIMEOUT_S)
        session.send(request("capabilities", "capabilities-bench"))
        session.wait_frames({"capabilities-bench"}, RACE_TIMEOUT_S)

        text = region_text(arguments.region_chars)
        run_started = time.perf_counter()
        for index in range(WARMUP_RACES + arguments.trials):
            sample = session.run_race(index, text)
            if index >= WARMUP_RACES:
                samples.append(sample)
        elapsed_s = time.perf_counter() - run_started
    finally:
        try:
            session.close()
        except Exception:
            pass

    ack_samples = [s["cancel_written_to_ack_ms"] for s in samples]
    stop_samples = [s["ack_to_worker_stopped_ms"] for s in samples]
    payload = {
        "benchmark": "writer-engine-cancellation-race",
        "generated_utc": datetime.now(UTC).isoformat(),
        "engine_version": ENGINE_VERSION,
        "protocol": {"major": PROTOCOL_MAJOR, "minor": PROTOCOL_MINOR},
        "hardware": hardware_identity(),
        "workload": {
            "mode": "in-process SidecarServer via real frame protocol",
            "operation": "analyze_region (persist=false)",
            "region_chars": arguments.region_chars,
            "analyzer": RaceProbeAnalyzer.name,
            "analyzer_kind": "cooperative checkpoint spin (matches recorded 1000-race probe)",
            "warmup_races": WARMUP_RACES,
            "percentile_method": "linear-interpolation",
            "definitions": {
                "cancel_written_to_ack_ms": "perf_counter before cancel frame written -> ack envelope observed",
                "ack_to_worker_stopped_ms": "ack observed -> analyzer finally-block ran (may be near-zero or negative if the worker stopped first)",
            },
        },
        "trials": len(samples),
        "accepted_cancellations": sum(1 for s in samples if s["accepted"]),
        "stale_successful_responses": sum(1 for s in samples if s["stale_success"]),
        "cancelled_error_responses": sum(1 for s in samples if s["analysis_error_code"] == "cancelled"),
        "elapsed_seconds": round(elapsed_s, 3),
        "races_per_second": round(len(samples) / max(elapsed_s, 1e-9), 1),
        "cancellation_ack_ms": stats_block(ack_samples),
        "cancellation_stop_ms": stats_block(stop_samples),
        "raw_samples": samples,
    }
    payload["gates"] = evaluate_gates(payload)

    serialized = json.dumps(payload, indent=2)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(serialized + "\n", encoding="utf-8")

    print(f"Cancellation race probe — engine {ENGINE_VERSION}, trials={payload['trials']}")
    print(f"Accepted cancellations : {payload['accepted_cancellations']}/{payload['trials']}")
    print(f"Stale successes        : {payload['stale_successful_responses']}")
    print(f"Elapsed                : {payload['elapsed_seconds']} s ({payload['races_per_second']} races/s)")
    print()
    header = f"{'metric':<26}{'count':>7}{'p50':>10}{'p95':>10}{'p99':>10}{'max':>10}"
    print(header)
    print("-" * len(header))
    for label, block in (("cancel_written_to_ack", payload["cancellation_ack_ms"]), ("ack_to_stopped", payload["cancellation_stop_ms"])):
        print(
            f"{label + ' (ms)':<26}{block['count']:>7}"
            f"{block['p50_ms']:>10.4f}{block['p95_ms']:>10.4f}{block['p99_ms']:>10.4f}{block['max_ms']:>10.4f}"
        )
    print()
    print(f"{'gate':<16}{'requirement':<18}{'measured':>12}  verdict")
    for gate in payload["gates"]:
        print(f"{gate['gate']:<16}{gate['requirement']:<18}{str(gate['measured']):>12}  {'PASS' if gate['pass'] else 'FAIL'}")
    print(f"\nWrote {output}")
    if not all(gate["pass"] for gate in payload["gates"]):
        status = 3
    return status


if __name__ == "__main__":
    raise SystemExit(main())
