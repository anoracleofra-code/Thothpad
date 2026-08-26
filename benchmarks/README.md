# ThothPad Benchmark Gate

This directory contains the sealed S-grade benchmark contract. It deliberately
separates workload definitions, immutable thresholds, raw measurements, and
certification. Missing measurements are `uncertified`; they are never inferred
from adjacent metrics.

## Commands

```text
python benchmarks/gate.py verify
python benchmarks/gate.py fixtures --output benchmark-results/corpora
python benchmarks/gate.py run-engine --suite smoke --output benchmark-results
python benchmarks/run_external_collector.py --build-dir build-windows-craft --suite smoke --output benchmark-results/native
python benchmarks/gate.py aggregate benchmark-results/raw --output benchmark-results/aggregate.json
python benchmarks/gate.py evaluate benchmark-results --output benchmark-results/certification.json
python benchmarks/test_gate.py
```

`run-engine` measures the existing deterministic engine without changing its
configuration. `smoke` uses one trial and is useful for validating collectors;
it cannot certify a release. `certification` requires 20 cold and 30 warm
trials as declared in `runner-definitions.json`.

`run_external_collector.py` builds and invokes `efficiencycollector` through
CTest's hardened offscreen Qt environment. It records native input-to-paint,
GUI edit, event-loop stall, scrolling, tooltip lookup, document-open, and
overlay hydration measurements for clean, dense, and Unicode fixtures at 10k,
100k, and 500k words. Smoke mode records one warm and one document-open cold
trial. Certification mode records 30 warm and 20 document-open cold trials.
Both modes retain every raw sample and exact source, binary, CMake, hardware,
and corpus identity. Native fixtures preserve the sealed corpus word sequence
and insert a deterministic paragraph boundary every 100 words so the collector
exercises manuscript block indexing instead of one pathological mega-block.
Smoke evidence cannot satisfy certification sample counts.

Process cold-start/window-visible timing, process-tree memory and handles,
disk I/O, cancellation process tracing, idle CPU/wakeups, network capture, and
power above baseline still require OS-owned collectors. Power certification
must run on the specified physical integrated-graphics laptops. The native Qt
collector deliberately does not invent substitutes for those measurements.

The intended certification runners are dedicated 2-vCPU, 8 GiB, SSD machines.
Setting `THOTHPAD_BENCHMARK_TARGET_ATTESTED=1` asserts that the runner is
resource-constrained to that target; it does not rewrite discovered hardware.

## Threshold integrity

`thresholds/s-grade-v1.json` is append-only and pinned by
`thresholds/s-grade-v1.sha256`. CI rejects a hash mismatch. A new standard must
be introduced as a new version; the v1 S limits must not be loosened.
