"""ThothPad memory/power baseline: drives the frozen writer-engine sidecar
through four scenarios while a process-tree sampler records private bytes,
handles, CPU time and context-switch deltas.

Scenarios (benchmarks/thresholds/s-grade-v1.json gates):
  1. cold initialize -> 60 s lean idle            (idle-memory, idle-cpu)
  2. open 500k-word document -> full analyze      (report-memory)
  3. post-report 120 s steady state               (steady-memory-500k)
  4. dispose -> 60 s post-dispose idle            (retention evidence)

Scope honesty: this run measures the ENGINE process tree only (sidecar
supervisor + report worker). The packaged desktop shell (thothpad.exe) is
probed for existence but is NEVER launched here: harness rules forbid raw
Qt/GUI launches outside autotest/run-tests.ps1. App-side idle memory/CPU is
therefore recorded as out of scope, not zero.

Usage (pinned python):
  writer-engine/.venv/Scripts/python.exe benchmarks/memory/run_scenarios.py

Outputs:
  benchmark-results/memory-baseline-2026-08-21/scenario-<n>-*.json
  benchmark-results/memory-baseline-2026-08-21.json   (combined + gate table)
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import queue
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from sampler import (  # noqa: E402
    ProcessTreeSampler,
    dump_json,
    hardware_identity,
    stats_block,
)

REPO_ROOT = Path(os.environ.get("THOTHPAD_REPO", HERE.parents[1]))
ENGINE_EXE_DEFAULT = (
    REPO_ROOT / "writer-engine" / "dist" / "writer-engine" / "writer-engine.exe"
)
DESKTOP_APP_CANDIDATES = [
    REPO_ROOT / "release" / "windows-core-candidate" / "stage" / "bin" / "thothpad.exe",
    REPO_ROOT / "release" / "windows-full-candidate" / "stage" / "bin" / "thothpad.exe",
]
THRESHOLDS_PATH = REPO_ROOT / "benchmarks" / "thresholds" / "s-grade-v1.json"
RESULTS_DIR_DEFAULT = REPO_ROOT / "benchmark-results"

PROTOCOL_MAJOR = 1
RUN_DATE = "2026-08-21"
WORD_COUNT = 500_000
LEAN_IDLE_S = 60.0
STEADY_IDLE_S = 120.0
POST_DISPOSE_IDLE_S = 60.0
REQUEST_TIMEOUT_S = 900.0


# --------------------------------------------------------------- sidecar client


class SidecarClient:
    """Minimal stdio frame client for the frozen sidecar executable."""

    def __init__(self, exe: Path):
        if not exe.exists():
            raise FileNotFoundError(f"frozen sidecar not found: {exe}")
        self.process = subprocess.Popen(
            [str(exe)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            cwd=str(exe.parent.parent),
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        self._responses: "queue.Queue[dict[str, Any]]" = queue.Queue()
        self._reader = threading.Thread(
            target=self._read_loop, daemon=True, name="sidecar-reader"
        )
        self._reader.start()
        self.pid = self.process.pid
        self.request_counter = 0

    def _read_loop(self) -> None:
        stdout = self.process.stdout
        assert stdout is not None
        try:
            while True:
                headers: dict[str, str] = {}
                while True:
                    line = stdout.readline()
                    if not line:
                        return
                    if line in (b"\r\n", b"\n"):
                        break
                    text = line.decode("ascii", "replace").strip()
                    if ":" in text:
                        key, value = text.split(":", 1)
                        headers[key.strip().lower()] = value.strip()
                length = int(headers.get("content-length", "0"))
                body = stdout.read(length)
                self._responses.put(json.loads(body.decode("utf-8")))
        except Exception as exc:  # reader dies with the pipe
            self._responses.put({"ok": False, "reader_error": f"{type(exc).__name__}: {exc}"})

    def request(self, operation: str, **params: Any) -> dict[str, Any]:
        self.request_counter += 1
        message = {
            "protocol_major": PROTOCOL_MAJOR,
            "request_id": f"mem-{RUN_DATE}-{self.request_counter}",
            "operation": operation,
            "params": params,
        }
        body = json.dumps(message, separators=(",", ":")).encode("utf-8")
        stdin = self.process.stdin
        assert stdin is not None
        stdin.write(f"Content-Length: {len(body)}\r\n\r\n".encode("ascii") + body)
        stdin.flush()
        deadline = time.monotonic() + REQUEST_TIMEOUT_S
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError(f"sidecar did not answer {operation!r} in time")
            try:
                response = self._responses.get(timeout=remaining)
            except queue.Empty:
                raise TimeoutError(f"sidecar did not answer {operation!r} in time") from None
            if response.get("request_id") == message["request_id"]:
                if response.get("ok") is True:
                    return response["result"]
                error = response.get("error") or response
                raise RuntimeError(f"{operation} failed: {error}")
            # ignore stale/duplicate frames targeted at other request ids

    def close(self) -> None:
        try:
            self.request("shutdown")
        except Exception:
            pass
        try:
            if self.process.stdin is not None:
                self.process.stdin.close()
        except Exception:
            pass
        try:
            self.process.wait(10)
        except Exception:
            self.process.kill()


# ------------------------------------------------------------------- corpus


_SENTENCES = [
    "Mara noticed the clock and moved quickly through the quiet room.",
    "The harbor lights blinked twice before the fog closed over them.",
    "He said the letter arrived on Tuesday, though nobody remembered mailing it.",
    "Rain gathered along the gutter and dropped in slow, uneven beats.",
    "Her argument was simple: the map had been drawn by someone who never sailed.",
    "They walked past the bakery where the owner argued with a delivery driver.",
    "A single lamp burned in the upstairs window of the corner house.",
    "The committee agreed that the proposal required further study next spring.",
    "Somewhere below decks a pump rattled and then went still.",
    "She folded the newspaper carefully and set it beside the untouched cup.",
]


def build_corpus(words: int = WORD_COUNT) -> tuple[str, str]:
    """Deterministic prose corpus with an exact word count; returns (text, sha256).

    Words cycle through the sentence pool; every 200 words starts a new
    paragraph so the analyzer sees realistic block structure.
    """
    pool = [word for sentence in _SENTENCES for word in sentence.split()]
    paragraphs: list[str] = []
    remaining = words
    while remaining > 0:
        take = min(200, remaining)
        start = words - remaining  # deterministic offset per paragraph
        chunk = [pool[(start + i) % len(pool)] for i in range(take)]
        paragraphs.append(" ".join(chunk))
        remaining -= take
    text = "\n\n".join(paragraphs)
    if len(text.split()) != words:
        raise ValueError(f"corpus word count {len(text.split())} != {words}")
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
    return text, digest


# ------------------------------------------------------------------ scenarios


def probe_desktop_app() -> dict[str, Any]:
    """Existence-only probe. NEVER launches the Qt shell (harness hard rule)."""
    found = [str(path) for path in DESKTOP_APP_CANDIDATES if path.exists()]
    return {
        "app_binary_present": bool(found),
        "binaries": found,
        "launched": False,
        "scope": "engine-only",
        "reason": (
            "Harness rules prohibit raw Qt/GUI launches outside autotest/"
            "run-tests.ps1 (modal-dialog incidents); no offscreen ctest path "
            "covers desktop-shell idle in this loop, so app-side idle memory "
            "and CPU are OUT OF SCOPE for this baseline rather than measured."
        ),
    }


def role_breakdown(samples: list[dict[str, Any]], phase: str) -> dict[str, Any]:
    rows = [s for s in samples if s.get("phase") == phase and "tree" in s]
    if not rows:
        return {}
    latest = rows[-1]
    by_role: dict[str, dict[str, int]] = {}
    for row in latest["pids"]:
        bucket = by_role.setdefault(row["role"], {"private_bytes": 0, "rss_bytes": 0, "pids": 0})
        bucket["private_bytes"] += row["private_bytes"]
        bucket["rss_bytes"] += row["rss_bytes"]
        bucket["pids"] += 1
    return {"at_offset_s": latest["t_s"], "roles": by_role}


def window(samples: list[dict[str, Any]], phases: list[str]) -> list[dict[str, Any]]:
    wanted = set(phases)
    return [s for s in samples if s.get("phase") in wanted and "tree" in s]


def p95_of(rows: list[dict[str, Any]], getter) -> float | None:
    values = [getter(s) for s in rows]
    values = [v for v in values if v is not None]
    if not values:
        return None
    from sampler import percentile

    return percentile([float(v) for v in values], 0.95)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--engine", type=Path, default=ENGINE_EXE_DEFAULT)
    parser.add_argument("--results-dir", type=Path, default=RESULTS_DIR_DEFAULT)
    parser.add_argument("--words", type=int, default=WORD_COUNT)
    parser.add_argument("--lean-idle-s", type=float, default=LEAN_IDLE_S)
    parser.add_argument("--steady-idle-s", type=float, default=STEADY_IDLE_S)
    parser.add_argument("--post-dispose-idle-s", type=float, default=POST_DISPOSE_IDLE_S)
    parser.add_argument(
        "--interval-ms", type=int, default=250, help="sampler period (spec: 250 ms)"
    )
    args = parser.parse_args()

    thresholds = json.loads(THRESHOLDS_PATH.read_text(encoding="utf-8"))
    limits = {req["id"]: req for req in thresholds["requirements"]}
    scenario_dir = args.results_dir / f"memory-baseline-{RUN_DATE}"
    scenario_dir.mkdir(parents=True, exist_ok=True)

    corpus, corpus_sha256 = build_corpus(args.words)
    print(f"[corpus] words>={args.words} chars={len(corpus)} sha256={corpus_sha256[:16]}…")

    client = SidecarClient(args.engine)
    sampler = ProcessTreeSampler(client.pid, interval_ms=args.interval_ms)
    sampler.set_phase("cold_init")
    sampler.start()
    run_started_utc = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    timings: dict[str, Any] = {}

    try:
        t0 = time.perf_counter()
        caps = client.request("initialize")
        timings["initialize_ms"] = round((time.perf_counter() - t0) * 1000, 1)
        print(f"[init] engine {caps['engine']['name']} {caps['engine']['version']} "
              f"protocol {caps['protocol']['major']}.{caps['protocol']['minor']} "
              f"in {timings['initialize_ms']} ms")

        sampler.set_phase("lean_idle")
        time.sleep(args.lean_idle_s)

        sampler.set_phase("open_document")
        doc_id = "bench-500k"
        t0 = time.perf_counter()
        client.request(
            "open_document",
            document_id=doc_id,
            revision=1,
            text=corpus,
            language="en",
        )
        timings["open_document_ms"] = round((time.perf_counter() - t0) * 1000, 1)
        print(f"[open] 500k-word document opened in {timings['open_document_ms']} ms")

        sampler.set_phase("analyze")
        t0 = time.perf_counter()
        analysis = client.request(
            "analyze_document",
            document_id=doc_id,
            document_revision=1,
            preset="full",
            confirm_adverbs=True,
        )
        timings["analyze_document_ms"] = round((time.perf_counter() - t0) * 1000, 1)
        analysis_id = analysis.get("analysis_id")
        print(f"[analyze] full preset done in {timings['analyze_document_ms']} ms "
              f"(analysis_id={str(analysis_id)[:24]}…, findings={analysis.get('total_findings')})")

        sampler.set_phase("steady_idle")
        time.sleep(args.steady_idle_s)

        sampler.set_phase("dispose")
        t0 = time.perf_counter()
        disposed_doc = client.request("dispose_document", document_id=doc_id)
        disposed_analysis = (
            client.request("dispose_analysis", analysis_id=analysis_id)
            if analysis_id else {"disposed": False}
        )
        timings["dispose_ms"] = round((time.perf_counter() - t0) * 1000, 1)
        print(f"[dispose] document={disposed_doc} analysis={disposed_analysis.get('disposed')} "
              f"in {timings['dispose_ms']} ms")

        sampler.set_phase("post_dispose_idle")
        time.sleep(args.post_dispose_idle_s)
    finally:
        samples = sampler.stop()
        client.close()

    finished_utc = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

    hardware = hardware_identity()
    scope = probe_desktop_app()
    notes = (
        f"Engine tree sampled every {args.interval_ms} ms via psutil; metric = private "
        "commit bytes per PID summed over the descendant tree (supervisor + lazily "
        "spawned --thothpad-report-worker). Idle CPU% normalized to the 2-vCPU s-grade "
        "target. Wakeups recorded as context-switch PROXY only (ETW required to certify)."
    )

    common = {
        "schema_version": 1,
        "kind": "memory-power-scenario",
        "run_date": RUN_DATE,
        "started_utc": run_started_utc,
        "finished_utc": finished_utc,
        "target_id": thresholds["target_id"],
        "engine_exe": str(args.engine),
        "hardware": hardware,
        "workload": {
            "id": "500k-prose",
            "word_count_target": args.words,
            "chars": len(corpus),
            "corpus_sha256": corpus_sha256,
        },
        "scope": scope,
        "timings_ms": timings,
        "metric_notes": notes,
    }

    scenario_defs = [
        ("scenario-1-cold-init-lean-idle", ["cold_init", "lean_idle"]),
        ("scenario-2-open-analyze-report-peak", ["open_document", "analyze"]),
        ("scenario-3-post-report-steady-idle", ["steady_idle"]),
        ("scenario-4-dispose-post-dispose-idle", ["dispose", "post_dispose_idle"]),
    ]
    for name, phases in scenario_defs:
        rows = window(samples, phases)
        doc = dict(common)
        doc["scenario"] = name
        doc["phases"] = phases
        doc["phase_summary"] = _summarize_phases(samples, phases)
        doc["samples"] = rows
        doc["peak_tree_private_bytes"] = max((s["tree"]["private_bytes"] for s in rows), default=None)
        path = scenario_dir / f"{name}.json"
        dump_json(str(path), doc)
        try:
            written = os.path.getsize(path)
        except OSError as exc:
            written = f"MISSING({type(exc).__name__})"
        print(f"[write] {path} bytes={written}")
    # ---------------- gate evaluation (thresholds immutable; read-only use)
    lean_rows = window(samples, ["lean_idle"])
    steady_rows = window(samples, ["steady_idle"])
    post_rows = window(samples, ["post_dispose_idle"])
    analyze_rows = window(samples, ["open_document", "analyze"])
    all_idle_rows = lean_rows + post_rows

    idle_private_p95 = p95_of(lean_rows, lambda s: s["tree"]["private_bytes"])
    report_peak = max((s["tree"]["private_bytes"] for s in analyze_rows), default=None)
    overall_peak = max((s["tree"]["private_bytes"] for s in samples if "tree" in s), default=None)
    steady_p95 = p95_of(steady_rows, lambda s: s["tree"]["private_bytes"])
    idle_cpu_p95 = p95_of(all_idle_rows, lambda s: s["delta"]["idle_cpu_percent_2vcpu"])
    ctx_rate_p95 = p95_of(all_idle_rows, lambda s: s["delta"]["ctx_switches_per_second_proxy"])
    post_dispose_tail = (
        min((s["tree"]["private_bytes"] for s in post_rows[len(post_rows)//2:]), default=None)
    )
    lean_tail = min((s["tree"]["private_bytes"] for s in lean_rows[len(lean_rows)//2:]), default=None)

    def verdict(gate_id: str, measured: float | None, statistic_note: str) -> dict[str, Any]:
        req = limits[gate_id]
        result: dict[str, Any] = {
            "gate_id": gate_id,
            "domain": req["domain"],
            "metric": req["metric"],
            "limit_bytes_or_units": req["limit"],
            "operator": req["operator"],
            "measured": measured,
            "statistic_note": statistic_note,
            "verdict": "UNCERTIFIED",
        }
        if measured is None:
            result["verdict_reason"] = "no samples collected for this window"
            return result
        if req["operator"] == "<=":
            result["verdict"] = "PASS" if measured <= req["limit"] else "FAIL"
        elif req["operator"] == "<":
            result["verdict"] = "PASS" if measured < req["limit"] else "FAIL"
        return result

    gates = [
        verdict("idle-memory", idle_private_p95,
                "p95 of lean-idle window tree private commit bytes"),
        verdict("report-memory", float(report_peak) if report_peak is not None else None,
                "max tree private bytes observed during open+analyze"),
        verdict("steady-memory-500k", steady_p95,
                "p95 of 120 s post-report steady window tree private bytes"),
        verdict("idle-cpu", idle_cpu_p95,
                "p95 idle CPU%% over both idle windows, normalized to the 2-vCPU target"),
    ]
    wake_req = limits["idle-wakeups"]
    gates.append({
        "gate_id": "idle-wakeups",
        "domain": wake_req["domain"],
        "metric": wake_req["metric"],
        "limit_bytes_or_units": wake_req["limit"],
        "operator": wake_req["operator"],
        "measured": None,
        "proxy_measured_ctx_switches_per_second_p95": ctx_rate_p95,
        "verdict": "UNCERTIFIED",
        "verdict_reason": (
            "Per-process wakeup counts on Windows require ETW CSWITCH/READYTHREAD "
            "kernel tracing (admin + tracing session); no clean user-mode API exists. "
            "Recorded best-effort proxy: voluntary+involuntary context-switch rate. "
            "Proxy is NOT accepted as the certified metric."
        ),
    })

    combined = dict(common)
    combined["kind"] = "memory-power-baseline-combined"
    combined["scenarios"] = {name: {"phases": phases, "file": str(scenario_dir / f"{name}.json")}
                             for name, phases in scenario_defs}
    combined["gates"] = gates
    combined["observations"] = observations(samples)
    combined["run_scope_disclaimer"] = (
        "All memory/CPU numbers cover ONLY the writer-engine process tree driven "
        "through the sidecar protocol. The ThothPad desktop shell adds GUI, editor, "
        "and plugin overhead not included here."
    )
    combined_path = args.results_dir / f"memory-baseline-{RUN_DATE}.json"
    dump_json(str(combined_path), combined)
    print(f"[write] {combined_path}")

    expected_outputs = [scenario_dir / f"{name}.json" for name, _ in scenario_defs]
    expected_outputs.append(combined_path)

    def verify_outputs(stage: str) -> None:
        for path in expected_outputs:
            try:
                size = os.path.getsize(path)
            except OSError as exc:
                size = f"MISSING ({type(exc).__name__})"
            print(f"[verify:{stage}] {path.name} bytes={size}")

    verify_outputs("t0")
    time.sleep(10)
    verify_outputs("t10s")

    print("\n=== s-grade-v1 memory/power gates ===")
    for gate in gates:
        measured = gate.get("measured")
        pretty = (
            f"{measured/1024**2:.1f} MiB" if isinstance(measured, (int, float)) and gate["metric"].endswith("_bytes")
            else f"{measured:.4f}" if isinstance(measured, (int, float)) else str(measured)
        )
        limit = gate["limit_bytes_or_units"]
        limit_pretty = f"{limit/1024**2:.0f} MiB" if gate["metric"].endswith("_bytes") else str(limit)
        extra = ""
        if gate["gate_id"] == "idle-wakeups":
            extra = f" (ctx-switch proxy p95 = {ctx_rate_p95:.1f}/s)"
        print(f"{gate['verdict']:12} {gate['gate_id']:20} measured={pretty} limit={gate['operator']} {limit_pretty}{extra}")

    print("\n=== observations ===")
    for line in observations_text(combined, lean_tail, post_dispose_tail, overall_peak):
        print(line)
    return 0


def _summarize_phases(samples: list[dict[str, Any]], phases: list[str]) -> dict[str, Any]:
    from sampler import phase_summary

    full = phase_summary(samples)
    return {p: full[p] for p in phases if p in full}


def observations(samples: list[dict[str, Any]]) -> dict[str, Any]:
    """Data hooks for the optimization-rank analysis."""
    interesting: dict[str, Any] = {}
    for marker in ("cold_init", "lean_idle", "analyze", "steady_idle", "dispose", "post_dispose_idle"):
        rows = [s for s in samples if s.get("phase") == marker and "tree" in s]
        if not rows:
            continue
        first = rows[0]["tree"]["private_bytes"]
        last = rows[-1]["tree"]["private_bytes"]
        peak = max(s["tree"]["private_bytes"] for s in rows)
        interesting[marker] = {
            "first_tree_private_bytes": first,
            "last_tree_private_bytes": last,
            "peak_tree_private_bytes": peak,
            "growth_in_window": last - first,
        }
    roles: dict[str, Any] = {}
    for marker in ("lean_idle", "steady_idle", "post_dispose_idle"):
        breakdown = role_breakdown(samples, marker)
        if breakdown:
            roles[marker] = breakdown["roles"]
    interesting["role_memory_at_idle_markers"] = roles
    cpu = [
        s["delta"]["idle_cpu_percent_one_core"]
        for s in samples
        if s.get("phase") in ("lean_idle", "steady_idle", "post_dispose_idle") and "delta" in s
    ]
    interesting["idle_cpu_percent_one_core_all_windows"] = stats_block(cpu or [0.0])
    return interesting


def observations_text(combined: dict[str, Any], lean_tail, post_tail, overall_peak) -> list[str]:
    obs = combined["observations"]
    lines = []
    markers = obs.get("role_memory_at_idle_markers", {})
    for phase, roles in markers.items():
        parts = ", ".join(
            f"{role}:{bucket['private_bytes']/1024**2:.1f} MiB"
            for role, bucket in sorted(roles.items())
        )
        lines.append(f"{phase}: {parts}")
    if lean_tail is not None and post_tail is not None:
        retained = post_tail - lean_tail
        lines.append(
            f"retained after dispose vs pre-open lean tail: "
            f"{retained/1024**2:+.1f} MiB (post {post_tail/1024**2:.1f} vs lean {lean_tail/1024**2:.1f})"
        )
    if overall_peak:
        lines.append(f"overall tree peak this run: {overall_peak/1024**2:.1f} MiB")
    return lines


if __name__ == "__main__":
    raise SystemExit(main())
