"""Process-tree resource sampler for ThothPad memory/power certification gates.

Given a root PID, samples the whole descendant process tree every N ms
(default 250 ms) and records, per sample:

- tree member PIDs (BFS over live descendants, precedent:
  packaging/windows/verify-package.ps1 Get-ProcessDescendants)
- per-PID private bytes (commit charge, equivalent of PowerShell
  ``Get-Process`` PrivateMemorySize64) and resident set bytes
- tree totals for private bytes, RSS and handle count
- cumulative CPU time per PID -> wall-clock deltas -> idle CPU %
- context-switch counters (best-effort wakeups PROXY; true wakeup counts
  on Windows require ETW CSWITCH tracing and are reported UNCERTIFIED)

Output is a plain JSON-serializable dict tree written with sort_keys so the
schema is byte-stable across runs; measured values are of course timing-
dependent.

Run directly for a smoke test::

    python benchmarks/memory/sampler.py --self-test
"""

from __future__ import annotations

import argparse
import ctypes
import json
import os
import platform
import statistics
import threading
import time
from typing import Any

try:
    import psutil
except ImportError as _exc:  # pragma: no cover - pinned venv ships psutil
    raise SystemExit(
        "benchmarks/memory requires psutil in the active interpreter "
        f"(pip install psutil); import failed: {_exc}"
    ) from _exc

SCHEMA_VERSION = 1
DEFAULT_INTERVAL_MS = 250

TARGET_HARDWARE_VCPUS = 2  # s-grade-v1 target_id "2vcpu-8gb-integrated-ssd"


def percentile(values: list[float], fraction: float) -> float:
    """Linear-interpolated percentile (matches benchmark_cancellation.py)."""
    if not values:
        raise ValueError("percentile of empty sample list")
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    position = (len(ordered) - 1) * fraction
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    weight = position - lower
    return ordered[lower] * (1.0 - weight) + ordered[upper] * weight


def stats_block(samples: list[float]) -> dict[str, Any]:
    """min/p50/p95/max/n summary for one metric."""
    if not samples:
        return {"n": 0}
    return {
        "n": len(samples),
        "min": min(samples),
        "p50": percentile(samples, 0.50),
        "p95": percentile(samples, 0.95),
        "max": max(samples),
        "mean": statistics.fmean(samples),
    }


def hardware_identity() -> dict[str, Any]:
    """Stable hardware/software identity block for evidence files."""
    identity: dict[str, Any] = {
        "system": platform.system(),
        "release": platform.release(),
        "version": platform.version(),
        "machine": platform.machine(),
        "processor": platform.processor(),
        "logical_cpus": os.cpu_count() or 1,
        "python": platform.python_version(),
        "psutil": psutil.__version__,
    }
    identifier = os.environ.get("PROCESSOR_IDENTIFIER")
    if identifier:
        identity["processor_identifier"] = identifier
    try:
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
            identity["total_memory_bytes"] = int(status.ullTotalPhys)
    except Exception:
        pass
    return identity


class ProcessTreeSampler:
    """Polls a root process and all live descendants at a fixed interval."""

    def __init__(self, root_pid: int, interval_ms: int = DEFAULT_INTERVAL_MS):
        if interval_ms < 10:
            raise ValueError("interval_ms must be >= 10")
        self.root_pid = int(root_pid)
        self.interval_s = interval_ms / 1000.0
        self._samples: list[dict[str, Any]] = []
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._t0: float | None = None
        self._phase = "unphased"
        # pid -> (create_time, role) so roles stay stable across ticks
        self._roles: dict[int, tuple[float, str]] = {}
        # pid -> cumulative cpu seconds seen last tick (for deltas)
        self._last_cpu: dict[int, tuple[float, float]] = {}
        self._last_ctx: dict[int, tuple[int, int]] = {}

    # ------------------------------------------------------------------ api

    @property
    def phase(self) -> str:
        return self._phase

    def set_phase(self, label: str) -> None:
        """Label subsequent samples (scenario stage marker)."""
        self._phase = str(label)

    def start(self) -> None:
        if self._thread is not None:
            raise RuntimeError("sampler already started")
        self._t0 = time.perf_counter()
        self._stop.clear()

        def _run() -> None:
            next_tick = time.perf_counter()
            while not self._stop.is_set():
                started = time.perf_counter()
                try:
                    sample = self._tick(started - (self._t0 or started))
                except psutil.NoSuchProcess:
                    break
                except Exception as exc:  # keep sampling; record the hiccup
                    sample = {
                        "error": f"{type(exc).__name__}: {exc}",
                        "t_s": round(started - (self._t0 or started), 4),
                        "phase": self._phase,
                    }
                with self._lock:
                    self._samples.append(sample)
                next_tick += self.interval_s
                delay = next_tick - time.perf_counter()
                if delay > 0:
                    self._stop.wait(delay)
                else:  # fell behind; resynchronize instead of bursting
                    next_tick = time.perf_counter()

        self._thread = threading.Thread(target=_run, daemon=True, name="mem-sampler")
        self._thread.start()

    def stop(self) -> list[dict[str, Any]]:
        if self._thread is None:
            return []
        self._stop.set()
        self._thread.join(5.0)
        self._thread = None
        with self._lock:
            samples = list(self._samples)
        self._samples = []
        return samples

    def samples_so_far(self) -> list[dict[str, Any]]:
        with self._lock:
            return list(self._samples)

    def document(
        self, notes: str | None = None, samples: list[dict[str, Any]] | None = None
    ) -> dict[str, Any]:
        """Deterministic-schema evidence document for this sampler run.

        ``samples`` defaults to everything recorded so far (call before
        ``stop()``, or pass the list returned by ``stop()``).
        """
        if samples is None:
            with self._lock:
                samples = list(self._samples)
        doc: dict[str, Any] = {
            "schema_version": SCHEMA_VERSION,
            "kind": "process-tree-sample-series",
            "interval_ms": round(self.interval_s * 1000),
            "root_pid": self.root_pid,
            "hardware": hardware_identity(),
            "metric_notes": {
                "private_bytes": (
                    "per-PID private commit charge (psutil memory_info().private; "
                    "equivalent to Get-Process PrivateMemorySize64)"
                ),
                "rss_bytes": "resident set (shared pages counted per-process; tree sum may double-count)",
                "idle_cpu_percent_one_core": "100 * tree_cpu_delta / wall_delta (one core == 100)",
                "idle_cpu_percent_2vcpu": "same fraction normalized to the 2-vCPU s-grade target",
                "ctx_switches": (
                    "voluntary+involuntary context-switch counter delta; wakeups PROXY only, "
                    "not certified idle_wakeups_per_second"
                ),
            },
        }
        if notes:
            doc["notes"] = notes
        doc["samples"] = samples
        doc["phase_summary"] = phase_summary(samples)
        return doc

    # -------------------------------------------------------------- internals

    def _tick(self, offset_s: float) -> dict[str, Any]:
        root = psutil.Process(self.root_pid)
        members = [root]
        try:
            members.extend(root.children(recursive=True))
        except psutil.NoSuchProcess:
            pass

        now_wall = time.perf_counter()
        rows: list[dict[str, Any]] = []
        cpu_now: dict[int, tuple[float, float]] = {}
        ctx_now: dict[int, tuple[int, int]] = {}
        tree_private = 0
        tree_rss = 0
        tree_handles = 0
        tree_cpu = 0.0
        tree_ctx = 0

        for proc in members:
            try:
                info = proc.memory_info()
                handles = proc.num_handles()
                cpu = proc.cpu_times()
                row_ppid = proc.ppid()
                row_name = proc.name()
                create_time = proc.create_time()
            except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                continue
            role = _role_for(proc, create_time)
            private = int(getattr(info, "private", info.rss))
            rss = int(info.rss)
            ctx_vol, ctx_invol = _ctx_switches(proc)
            cpu_total = float(cpu.user + cpu.system)
            row = {
                "pid": proc.pid,
                "ppid": row_ppid,
                "name": row_name,
                "role": role,
                "private_bytes": private,
                "rss_bytes": rss,
                "handles": int(handles),
                "cpu_time_s": round(cpu_total, 4),
                "ctx_switches": int(ctx_vol + ctx_invol),
            }
            rows.append(row)
            tree_private += private
            tree_rss += rss
            tree_handles += int(handles)
            tree_cpu += cpu_total
            tree_ctx += int(ctx_vol + ctx_invol)
            cpu_now[proc.pid] = (cpu_total, now_wall)
            ctx_now[proc.pid] = (int(ctx_vol + ctx_invol), now_wall)

        prev_cpu = self._last_cpu
        prev_ctx = self._last_ctx
        self._last_cpu = cpu_now
        self._last_ctx = ctx_now

        wall_delta = now_wall - (self._prev_wall if hasattr(self, "_prev_wall") else now_wall)
        self._prev_wall = now_wall

        cpu_seconds = 0.0
        ctx_delta = 0
        for pid, (cpu_total, _) in cpu_now.items():
            if pid in prev_cpu:
                delta = cpu_total - prev_cpu[pid][0]
                if 0.0 <= delta < 3600.0:
                    cpu_seconds += delta
            prior = prev_ctx.get(pid)
            if prior is not None:
                delta_ctx = ctx_now[pid][0] - prior[0]
                if 0 <= delta_ctx < 5_000_000:
                    ctx_delta += delta_ctx

        if wall_delta > 1e-4:
            fraction = cpu_seconds / wall_delta  # fraction of ONE core
            one_core = fraction * 100.0
        else:
            fraction = 0.0
            one_core = 0.0
        logical = max(1, os.cpu_count() or 1)

        sample: dict[str, Any] = {
            "t_s": round(offset_s, 4),
            "phase": self._phase,
            "tree": {
                "private_bytes": tree_private,
                "rss_bytes": tree_rss,
                "handles": tree_handles,
                "cpu_time_s": round(tree_cpu, 4),
                "pids": len(rows),
            },
            "pids": rows,
            "delta": {
                "wall_s": round(max(wall_delta, 0.0), 4),
                "tree_cpu_time_s": round(cpu_seconds, 4),
                "idle_cpu_percent_one_core": round(one_core, 4),
                "idle_cpu_percent_machine": round(fraction / logical * 100.0, 4),
                "idle_cpu_percent_2vcpu": round(fraction / TARGET_HARDWARE_VCPUS * 100.0, 4),
                "ctx_switches_delta": ctx_delta,
                "ctx_switches_per_second_proxy": round(ctx_delta / wall_delta, 2)
                if wall_delta > 1e-4
                else 0.0,
            },
        }
        return sample


def _ctx_switches(proc: psutil.Process) -> tuple[int, int]:
    try:
        counts = proc.num_ctx_switches()
        return int(counts.voluntary), int(counts.involuntary)
    except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
        return 0, 0


def _role_for(proc: psutil.Process, create_time: float) -> str:
    key = (proc.pid, round(create_time, 6))
    cached = ProcessTreeSampler._roles_cache.get(key)
    if cached is not None:
        return cached
    role = "unknown"
    try:
        name = proc.name().lower()
        if "--thothpad-report-worker" in " ".join(proc.cmdline()):
            role = "report-worker"
        elif "--thothpad-worker" in " ".join(proc.cmdline()):
            role = "worker"
        elif "harper" in name:
            role = "grammar-harper"
        elif name in ("conhost.exe", "openconsole.exe"):
            role = "conhost"
        else:
            role = "primary"
    except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
        role = "exiting"
    ProcessTreeSampler._roles_cache[key] = role
    return role


ProcessTreeSampler._roles_cache = {}


# --------------------------------------------------------------------- windows


def phase_summary(samples: list[dict[str, Any]]) -> dict[str, Any]:
    """Per-phase aggregates used directly for gate evaluation."""
    phases: dict[str, list[dict[str, Any]]] = {}
    for sample in samples:
        if "error" in sample:
            continue
        phases.setdefault(sample["phase"], []).append(sample)
    summary: dict[str, Any] = {}
    for phase, rows in phases.items():
        private = [float(r["tree"]["private_bytes"]) for r in rows]
        rss = [float(r["tree"]["rss_bytes"]) for r in rows]
        handles = [float(r["tree"]["handles"]) for r in rows]
        cpu_one = [float(r["delta"]["idle_cpu_percent_one_core"]) for r in rows]
        cpu_2v = [float(r["delta"]["idle_cpu_percent_2vcpu"]) for r in rows]
        ctx_rate = [
            float(r["delta"]["ctx_switches_per_second_proxy"])
            for r in rows[1:]  # first tick has no delta baseline
        ]
        summary[phase] = {
            "samples": len(rows),
            "duration_s": round(rows[-1]["t_s"] - rows[0]["t_s"], 3) if len(rows) > 1 else 0.0,
            "tree_private_bytes": stats_block(private),
            "tree_rss_bytes": stats_block(rss),
            "tree_handles": stats_block(handles),
            "idle_cpu_percent_one_core": stats_block(cpu_one),
            "idle_cpu_percent_2vcpu": stats_block(cpu_2v),
            "ctx_switches_per_second_proxy": stats_block(ctx_rate),
        }
    return summary


def evaluate_gate(measured_p95: float, limit: float, operator: str = "<=") -> str:
    if operator == "<=":
        return "PASS" if measured_p95 <= limit else "FAIL"
    if operator == "<":
        return "PASS" if measured_p95 < limit else "FAIL"
    raise ValueError(f"unsupported operator {operator!r}")


def dump_json(path: str, payload: dict[str, Any]) -> None:
    """Canonical deterministic JSON output (sorted keys, compact separators)."""
    with open(path, "w", encoding="utf-8", newline="\n") as handle:
        json.dump(payload, handle, sort_keys=True, separators=(",", ":"))
        handle.write("\n")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--self-test", action="store_true", help="sample our own process tree for ~3 s"
    )
    parser.add_argument("--interval-ms", type=int, default=100)
    args = parser.parse_args()
    if not args.self_test:
        parser.print_help()
        return 2
    me = psutil.Process()
    sampler = ProcessTreeSampler(me.pid, interval_ms=args.interval_ms)
    sampler.set_phase("selftest")
    sampler.start()
    churn: list[int] = []
    deadline = time.perf_counter() + 3.0
    while time.perf_counter() < deadline:
        churn.extend(range(10_000))  # burn a little CPU so deltas are visible
        time.sleep(0.05)
    samples = sampler.stop()
    doc = sampler.document(notes="self-test", samples=samples)
    print(json.dumps(doc["phase_summary"], indent=2, sort_keys=True)[:2000])
    print(f"samples={len(samples)} ok={all('error' not in s for s in samples)}")
    return 0 if samples and all("error" not in s for s in samples) else 1


if __name__ == "__main__":
    raise SystemExit(main())
