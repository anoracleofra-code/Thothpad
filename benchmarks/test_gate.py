#!/usr/bin/env python3
"""Dependency-free regression checks for the benchmark contract."""

from __future__ import annotations

import tempfile
from argparse import Namespace
from pathlib import Path
import sys

import gate

sys.path.insert(0, str(gate.ROOT / "packaging"))
import package_profile  # type: ignore  # local packaging module


def main() -> int:
    assert len(gate.corpus_text("clean", 10_000).split()) == 10_000
    assert len(gate.corpus_text("unicode", 500_000).split()) == 500_000
    assert gate.percentile([1, 2, 3, 4, 100], 0.95) == 100
    summary = gate.summarize([1, 2, 3, 4])
    assert summary["p50"] == 2 and summary["p95"] == 4
    assert gate.compare(16.7, "<=", 16.7)
    assert not gate.compare(16.8, "<=", 16.7)
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        output = root / "aggregate.json"
        aggregate = gate.aggregate_results([])
        gate.write_json(output, aggregate)
        assert gate.read_json(output)["result_count"] == 0
        full = root / "full"
        core = root / "core"
        for stage, variant, policy in ((full, "Full", "included"), (core, "Core", "omitted")):
            declaration = stage / "share" / "thothpad" / "thothpad-package-profile.json"
            declaration.parent.mkdir(parents=True)
            gate.write_json(declaration, {"variant": variant, "webengine_policy": policy})
        (full / "Qt6WebEngineCore.dll").write_bytes(b"fixture")
        arguments = lambda stage, variant: Namespace(
            root=stage, output=root / f"{variant}.json", repo=gate.ROOT,
            version="0.1.2", variant=variant,
        )
        assert package_profile.command_write(arguments(full, "Full")) == 0
        assert package_profile.command_write(arguments(core, "Core")) == 0
        (core / "Qt6WebEngineCore.dll").write_bytes(b"fixture")
        assert package_profile.command_write(arguments(core, "Core")) == 2
    print("Benchmark gate self-tests passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
