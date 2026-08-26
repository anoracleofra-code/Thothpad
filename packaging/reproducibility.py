#!/usr/bin/env python3
"""Create and compare normalized staged-package manifests."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path


SIGNATURE_PATH_PARTS = {"_CodeSignature", "CodeResources"}


def validate_commit(value: str) -> str:
    normalized = value.lower()
    if len(normalized) != 40 or any(
        character not in "0123456789abcdef" for character in normalized
    ):
        raise SystemExit("source commit must be a full 40-character Git commit")
    return normalized


def normalized_generated_json(path: Path, value):
    if not isinstance(value, dict):
        return value
    result = dict(value)
    lower_name = path.name.lower()
    if lower_name.endswith(".cdx.json"):
        result.pop("serialNumber", None)
        metadata = result.get("metadata")
        if isinstance(metadata, dict):
            result["metadata"] = dict(metadata)
            result["metadata"].pop("timestamp", None)
    elif lower_name.endswith(".package.json"):
        result.pop("created_utc", None)
    return result


def normalized_bytes(path: Path) -> bytes:
    if path.suffix.lower() == ".json":
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            pass
        else:
            normalized = normalized_generated_json(path, value)
            return json.dumps(normalized, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return path.read_bytes()


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def manifest(root: Path) -> dict[str, dict[str, int | str]]:
    result = {}
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        relative = path.relative_to(root)
        if any(part in SIGNATURE_PATH_PARTS for part in relative.parts):
            continue
        content = normalized_bytes(path)
        result[relative.as_posix()] = {
            "sha256": hashlib.sha256(content).hexdigest(),
            "size": len(content),
        }
    return result


def command_snapshot(args: argparse.Namespace) -> int:
    if not args.root.is_dir():
        raise SystemExit(f"stage does not exist: {args.root}")
    if not args.toolchain_lock.is_file():
        raise SystemExit(f"toolchain lock does not exist: {args.toolchain_lock}")
    runner_name = os.environ.get("RUNNER_NAME")
    if runner_name and runner_name != args.builder_id:
        raise SystemExit(
            f"builder id {args.builder_id!r} does not match the executing "
            f"runner {runner_name!r}"
        )
    document = {
        "schema_version": 1,
        "normalization": "structured-json-v1;macos-signature-files-excluded",
        "provenance": {
            "candidate": args.candidate,
            "builder_id": args.builder_id,
            "runner_pool": args.runner_pool,
            "source_commit": validate_commit(args.source_commit),
            "toolchain_lock_sha256": file_sha256(args.toolchain_lock),
        },
        "files": manifest(args.root.resolve()),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")
    return 0


def command_compare(args: argparse.Namespace) -> int:
    if not args.toolchain_lock.is_file():
        raise SystemExit(f"toolchain lock does not exist: {args.toolchain_lock}")
    source_commit = validate_commit(args.source_commit)
    toolchain_hash = file_sha256(args.toolchain_lock)
    left = json.loads(args.left.read_text(encoding="utf-8"))
    right = json.loads(args.right.read_text(encoding="utf-8"))
    left_provenance = left.get("provenance", {})
    right_provenance = right.get("provenance", {})
    if left_provenance.get("candidate") != "a" or right_provenance.get("candidate") != "b":
        raise SystemExit("arbiter requires candidate A on the left and candidate B on the right")
    if not left_provenance.get("builder_id") or not right_provenance.get("builder_id"):
        raise SystemExit("candidate manifests must identify their builders")
    if left_provenance["builder_id"] == right_provenance["builder_id"]:
        raise SystemExit("candidate A and B must be produced by different runners")
    left_pool = left_provenance.get("runner_pool")
    right_pool = right_provenance.get("runner_pool")
    if not left_pool or not right_pool:
        raise SystemExit("candidate manifests must record their runner pools")
    if left_pool != args.left_pool:
        raise SystemExit(
            f"candidate A was built on runner pool {left_pool!r}, "
            f"expected {args.left_pool!r}"
        )
    if right_pool != args.right_pool:
        raise SystemExit(
            f"candidate B was built on runner pool {right_pool!r}, "
            f"expected {args.right_pool!r}"
        )
    if left_pool == right_pool:
        raise SystemExit("candidate A and B must come from distinct runner pools")
    for candidate in (left_provenance, right_provenance):
        if candidate.get("source_commit") != source_commit:
            raise SystemExit("candidate source commit does not match arbiter input")
        if candidate.get("toolchain_lock_sha256") != toolchain_hash:
            raise SystemExit("candidate toolchain lock does not match arbiter input")
    provenance = {
        "source_commit": source_commit,
        "toolchain_lock_sha256": toolchain_hash,
        "builders": {
            "left": left_provenance["builder_id"],
            "right": right_provenance["builder_id"],
        },
        "runner_pools": {
            "left": left_pool,
            "right": right_pool,
        },
        "candidate_manifests": {
            "left": file_sha256(args.left),
            "right": file_sha256(args.right),
        },
    }
    left_comparable = {key: left.get(key) for key in ("schema_version", "normalization", "files")}
    right_comparable = {key: right.get(key) for key in ("schema_version", "normalization", "files")}
    if left_comparable != right_comparable:
        left_files = left.get("files", {})
        right_files = right.get("files", {})
        changed = sorted(
            key for key in set(left_files) | set(right_files)
            if left_files.get(key) != right_files.get(key)
        )
        print("Normalized staged package trees differ:")
        for path in changed[:100]:
            print(f"  {path}")
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(json.dumps({
                "schema_version": 1,
                "matched": False,
                "changed_paths": changed,
                **provenance,
            }, indent=2) + "\n", encoding="utf-8")
        return 2
    file_count = len(left.get("files", {}))
    if args.output:
        canonical = json.dumps(
            left_comparable, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps({
            "schema_version": 1,
            "matched": True,
            "file_count": file_count,
            "normalized_tree_sha256": hashlib.sha256(canonical).hexdigest(),
            **provenance,
        }, indent=2) + "\n", encoding="utf-8")
    print(f"Reproducible normalized tree: {file_count} files")
    return 0


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    commands = result.add_subparsers(dest="command", required=True)
    snapshot = commands.add_parser("snapshot")
    snapshot.add_argument("--root", type=Path, required=True)
    snapshot.add_argument("--output", type=Path, required=True)
    snapshot.add_argument("--candidate", choices=("a", "b"), required=True)
    snapshot.add_argument("--builder-id", required=True)
    snapshot.add_argument("--runner-pool", required=True)
    snapshot.add_argument("--source-commit", required=True)
    snapshot.add_argument("--toolchain-lock", type=Path, required=True)
    snapshot.set_defaults(func=command_snapshot)
    compare = commands.add_parser("compare")
    compare.add_argument("--left", type=Path, required=True)
    compare.add_argument("--right", type=Path, required=True)
    compare.add_argument("--output", type=Path)
    compare.add_argument("--toolchain-lock", type=Path, required=True)
    compare.add_argument("--source-commit", required=True)
    compare.add_argument("--left-pool", default="repro-a")
    compare.add_argument("--right-pool", default="repro-b")
    compare.set_defaults(func=command_compare)
    return result


if __name__ == "__main__":
    args = parser().parse_args()
    raise SystemExit(args.func(args))
