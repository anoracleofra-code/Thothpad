#!/usr/bin/env python3
"""Write and verify Core/Full package metadata without guessing capabilities."""

from __future__ import annotations

import argparse
import ctypes
import hashlib
import json
import os
import platform
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path


WEBENGINE_MARKERS = (
    "qt6webengine", "qtwebengineprocess", "webenginecore.framework",
    "qtwebengine_resources", "qtwebengine_dictionaries",
)


def digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(chunk)
    return value.hexdigest()


def tree_inventory(root: Path) -> tuple[int, int, list[str]]:
    files = sorted(item for item in root.rglob("*") if item.is_file())
    return len(files), sum(item.stat().st_size for item in files), [item.relative_to(root).as_posix() for item in files]


def webengine_files(paths: list[str]) -> list[str]:
    return [path for path in paths if any(marker in path.lower() for marker in WEBENGINE_MARKERS)]


def total_memory_bytes() -> int:
    if sys.platform == "win32":
        class MemoryStatus(ctypes.Structure):
            _fields_ = [
                ("length", ctypes.c_ulong), ("memory_load", ctypes.c_ulong),
                ("total_physical", ctypes.c_ulonglong), ("available_physical", ctypes.c_ulonglong),
                ("total_page_file", ctypes.c_ulonglong), ("available_page_file", ctypes.c_ulonglong),
                ("total_virtual", ctypes.c_ulonglong), ("available_virtual", ctypes.c_ulonglong),
                ("available_extended_virtual", ctypes.c_ulonglong),
            ]
        status = MemoryStatus()
        status.length = ctypes.sizeof(status)
        if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
            return int(status.total_physical)
    if hasattr(os, "sysconf"):
        try:
            return int(os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES"))
        except (OSError, ValueError):
            pass
    return 1


def git_value(root: Path, *args: str) -> str:
    result = subprocess.run(["git", *args], cwd=root, capture_output=True, text=True, check=False)
    return result.stdout.strip() if result.returncode == 0 else "unknown"


def created_utc() -> str:
    epoch = os.getenv("SOURCE_DATE_EPOCH")
    if epoch:
        try:
            return datetime.fromtimestamp(int(epoch), tz=timezone.utc).isoformat()
        except (ValueError, OSError, OverflowError) as error:
            raise SystemExit("SOURCE_DATE_EPOCH must be a valid Unix timestamp") from error
    return datetime.now(timezone.utc).isoformat()


def command_write(args: argparse.Namespace) -> int:
    root = args.root.resolve()
    if not root.is_dir():
        raise SystemExit(f"staged package root does not exist: {root}")
    count, size, paths = tree_inventory(root)
    markers = webengine_files(paths)
    variant = args.variant.capitalize()
    declarations = list(root.rglob("thothpad-package-profile.json"))
    declared_webengine = False
    for declaration in declarations:
        declared = json.loads(declaration.read_text(encoding="utf-8"))
        if declared.get("variant") != variant:
            print(
                f"Package profile mismatch: CMake declared {declared.get('variant')}, packager requested {variant}.",
                file=sys.stderr,
            )
            return 2
        declared_webengine = declared.get("webengine_policy") == "included"
    if variant == "Core" and markers:
        print("Core package rejected: Qt WebEngine files are present:", file=sys.stderr)
        print("\n".join(f"  {path}" for path in markers[:20]), file=sys.stderr)
        return 2
    if variant == "Full" and not markers and not declared_webengine:
        print("Full package rejected: no Qt WebEngine runtime was found.", file=sys.stderr)
        return 2
    repo = args.repo.resolve()
    manifest = {
        "schema_version": 1,
        "application": "ThothPad",
        "version": args.version,
        "variant": variant,
        "webengine": "omitted" if variant == "Core" else "included",
        "created_utc": created_utc(),
        "platform": {"system": platform.system(), "machine": platform.machine()},
        "build": {
            "git_commit": git_value(repo, "rev-parse", "HEAD"),
            "dirty": bool(git_value(repo, "status", "--porcelain")),
        },
        "inventory": {
            "file_count": count,
            "extracted_bytes": size,
            "webengine_file_count": len(markers),
        },
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(f"Verified {variant} package profile: {count} files, {size} bytes")
    return 0


def command_measure(args: argparse.Namespace) -> int:
    root = args.root.resolve()
    archive = args.archive.resolve()
    if not root.is_dir() or not archive.is_file():
        raise SystemExit("both --root and --archive must exist")
    _, extracted, paths = tree_inventory(root)
    variant = args.variant.capitalize()
    markers = webengine_files(paths)
    status = "measured"
    notes = ""
    if variant == "Core" and markers:
        status = "error"
        notes = "Core stage contains Qt WebEngine"
    prefix = variant.lower()
    result = {
        "schema_version": 1,
        "run_id": f"package-{platform.system().lower()}-{digest(archive)[:16]}",
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "suite": args.suite,
        "platform": {"Darwin": "macos", "Windows": "windows", "Linux": "linux"}.get(platform.system(), platform.system().lower()),
        "hardware": {
            "logical_cpus": os.cpu_count() or 1,
            "memory_bytes": total_memory_bytes(),
            "machine": platform.machine() or "unknown",
            "processor": platform.processor() or "unknown"
        },
        "build": {"git_commit": args.commit, "dirty": args.dirty, "source_sha256": digest(archive)},
        "target": {"id": "2vcpu-8gb-integrated-ssd", "attested": os.getenv("THOTHPAD_BENCHMARK_TARGET_ATTESTED") == "1"},
        "workload": {"id": "global", "corpus": "global", "word_count": 0, "corpus_sha256": hashlib.sha256(b"").hexdigest()},
        "metrics": [
            {"metric": f"{prefix}_compressed_bytes", "unit": "bytes", "status": status, "collector": "package-profile", "trial_kind": "single", "samples": [float(archive.stat().st_size)] if status == "measured" else [], "notes": notes},
            {"metric": f"{prefix}_extracted_bytes", "unit": "bytes", "status": status, "collector": "package-profile", "trial_kind": "single", "samples": [float(extracted)] if status == "measured" else [], "notes": notes}
        ],
        "automatic_failures": [],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    return 0 if status == "measured" else 2


def parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    write = commands.add_parser("write")
    write.add_argument("--root", type=Path, required=True)
    write.add_argument("--output", type=Path, required=True)
    write.add_argument("--repo", type=Path, required=True)
    write.add_argument("--version", required=True)
    write.add_argument("--variant", choices=("Core", "Full"), required=True)
    write.set_defaults(func=command_write)
    measure = commands.add_parser("measure")
    measure.add_argument("--root", type=Path, required=True)
    measure.add_argument("--archive", type=Path, required=True)
    measure.add_argument("--output", type=Path, required=True)
    measure.add_argument("--variant", choices=("Core", "Full"), required=True)
    measure.add_argument("--suite", choices=("smoke", "certification"), default="smoke")
    measure.add_argument("--commit", default="unknown")
    measure.add_argument("--dirty", action="store_true")
    measure.set_defaults(func=command_measure)
    return parser


if __name__ == "__main__":
    args = parser().parse_args()
    raise SystemExit(args.func(args))
