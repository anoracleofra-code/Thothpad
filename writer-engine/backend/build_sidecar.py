from __future__ import annotations

import argparse
import ast
import importlib.util
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

from backend import config
from backend.analyzers.possible_adverbs import spacy_model_status

ROOT = Path(__file__).resolve().parents[1]
SPEC = ROOT / "writer-engine.spec"
HARPER_DIR = ROOT / "harper-bridge"
HARPER_BINARY = HARPER_DIR / "target" / "release" / (
    "thothpad-harper.exe" if os.name == "nt" else "thothpad-harper"
)
FROZEN_BINARY = ROOT / "dist" / "writer-engine" / (
    "writer-engine.exe" if os.name == "nt" else "writer-engine"
)


def build_harper() -> Path:
    cargo = shutil.which("cargo")
    if not cargo:
        raise RuntimeError("Rust/Cargo is required to build the bundled Harper grammar engine")
    subprocess.run(
        [cargo, "build", "--release", "--locked"],
        cwd=HARPER_DIR,
        check=True,
    )
    if not HARPER_BINARY.is_file():
        raise RuntimeError(f"Harper build did not produce {HARPER_BINARY}")
    return HARPER_BINARY


def smoke_check(*, require_spacy_model: bool = True) -> dict[str, object]:
    required = [
        SPEC,
        config.BUILTIN_PROFILES_DIR,
        config.BACKEND_DIR / "data" / "slopless",
        config.BACKEND_DIR / "data" / "wordnet" / "index.adv",
        config.BACKEND_DIR / "data" / "wordnet" / "index.adj",
        config.BACKEND_DIR / "data" / "wordnet" / "index.verb",
        config.BACKEND_DIR / "data" / "wordnet" / "adv.exc",
        config.BACKEND_DIR / "data" / "wordnet" / "adj.exc",
        config.BACKEND_DIR / "data" / "wordnet" / "verb.exc",
        config.BACKEND_DIR / "data" / "wordnet" / "LICENSE",
        HARPER_DIR / "Cargo.toml",
        HARPER_DIR / "Cargo.lock",
        HARPER_DIR / "rust-toolchain.toml",
        HARPER_DIR / "src" / "main.rs",
    ]
    missing = [str(path) for path in required if not path.exists()]
    if missing:
        raise RuntimeError(f"missing sidecar build inputs: {', '.join(missing)}")
    if importlib.util.find_spec("backend.sidecar") is None:
        raise RuntimeError("backend.sidecar is not importable")
    spec_source = SPEC.read_text(encoding="utf-8")
    ast.parse(spec_source, filename=str(SPEC))
    for required_input in (
        "backend/sidecar.py", "en_core_web_sm", "backend/data/slopless", "backend/data/wordnet",
        "thothpad-harper", "pyinstaller-hooks",
        "profiles", "THIRD_PARTY.md", "THIRD_PARTY_NOTICES.md", "COPYING",
    ):
        if required_input not in spec_source:
            raise RuntimeError(f"sidecar spec does not collect required input: {required_input}")
    status = spacy_model_status()
    if require_spacy_model and not status["available"]:
        raise RuntimeError("en_core_web_sm must be installed for the production sidecar build")
    return {"spec": str(SPEC), "spacy": status, "ready": True}


def frozen_smoke_check() -> None:
    request = {
        "protocol_major": 1,
        "protocol_minor": 2,
        "request_id": "frozen-smoke",
        "document_id": "frozen-smoke",
        "document_revision": 1,
        "operation": "analyze_region",
        "params": {
            "text": "She moved quickly through the narrow hall.",
            "profile": "creative-default",
            "confirm_adverbs": True,
        },
    }
    completed = subprocess.run(
        [str(FROZEN_BINARY), "--thothpad-worker"],
        input=json.dumps(request).encode("utf-8"),
        capture_output=True,
        timeout=30,
        check=False,
    )
    if completed.returncode:
        raise RuntimeError(
            completed.stderr.decode("utf-8", errors="replace")
            or f"frozen sidecar exited with {completed.returncode}"
        )
    try:
        response = json.loads(completed.stdout)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeError("frozen sidecar returned invalid JSON") from exc
    diagnostics = response.get("result", {}).get("diagnostics", [])
    if response.get("ok") is not True or not any(
        item.get("analyzer") == "possible_adverbs" and item.get("excerpt") == "quickly"
        for item in diagnostics
    ):
        raise RuntimeError("frozen sidecar failed its contextual POS smoke check")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build or smoke-test the ThothPad Engine sidecar.")
    parser.add_argument("--smoke", action="store_true", help="validate build inputs without invoking PyInstaller")
    parser.add_argument("--allow-missing-spacy", action="store_true", help="development-only smoke mode")
    args = parser.parse_args(argv)
    smoke_check(require_spacy_model=not args.allow_missing_spacy)
    if args.smoke:
        return 0
    build_harper()
    result = subprocess.call(
        [sys.executable, "-m", "PyInstaller", "--clean", "--noconfirm", str(SPEC)],
        cwd=ROOT,
    )
    if result == 0:
        frozen_smoke_check()
    return result


if __name__ == "__main__":
    raise SystemExit(main())
