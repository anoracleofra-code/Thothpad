from __future__ import annotations

import argparse
import json
from pathlib import Path

import uvicorn

from backend import config
from backend.manuscript import analyze_manuscript, calibrate_corpus
from backend.models import RunRequest
from backend.pipeline import compare_texts, run_pipeline


def read_text(path: str) -> str:
    return Path(path).read_text(encoding="utf-8")


def collect_documents(paths: list[str]) -> list[dict[str, str]]:
    accepted = {".md", ".markdown", ".txt"}
    documents: list[dict[str, str]] = []
    for value in paths:
        path = Path(value)
        candidates = (
            sorted(item for item in path.rglob("*") if item.is_file() and item.suffix.lower() in accepted)
            if path.is_dir()
            else [path]
        )
        for item in candidates:
            if item.suffix.lower() in accepted:
                documents.append({"name": str(item), "text": item.read_text(encoding="utf-8")})
    return documents


def print_report(report: dict) -> None:
    print(json.dumps({
        "run_id": report.get("run_id"),
        "run_dir": report.get("run_dir"),
        "mode": report.get("mode"),
        "profile": report.get("profile"),
        "score_before": report.get("score_before"),
        "score_after": report.get("score_after"),
        "llm_errors": report.get("llm_errors", []),
    }, indent=2))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="writer", description="ThothPad prose harness")
    sub = parser.add_subparsers(dest="cmd", required=True)

    for cmd in ("diagnose", "rewrite", "deslop", "line-edit"):
        p = sub.add_parser(cmd)
        p.add_argument("file")
        p.add_argument("--profile", default=config.DEFAULT_PROFILE)
        p.add_argument("--passes", type=int, default=1)
        p.add_argument("--aggressiveness", default="medium")
        p.add_argument("--save-run", action="store_true")

    p_cmp = sub.add_parser("compare")
    p_cmp.add_argument("before")
    p_cmp.add_argument("after")
    p_cmp.add_argument("--profile", default=config.DEFAULT_PROFILE)
    p_cmp.add_argument("--save-run", action="store_true")

    p_serve = sub.add_parser("serve")
    p_serve.add_argument("--host", default=config.DEFAULT_HOST)
    p_serve.add_argument("--port", type=int, default=config.DEFAULT_PORT)

    p_manuscript = sub.add_parser("manuscript")
    p_manuscript.add_argument("paths", nargs="+")
    p_manuscript.add_argument("--profile", default=config.DEFAULT_PROFILE)
    p_manuscript.add_argument("--project")
    p_manuscript.add_argument("--save-run", action="store_true")

    p_calibrate = sub.add_parser("calibrate")
    p_calibrate.add_argument("paths", nargs="+")
    p_calibrate.add_argument("--name", required=True)
    p_calibrate.add_argument("--reference", nargs="*", default=[])

    args = parser.parse_args(argv)
    if args.cmd == "serve":
        uvicorn.run("backend.main:app", host=args.host, port=args.port, reload=False)
        return 0
    if args.cmd == "compare":
        print_report(compare_texts(read_text(args.before), read_text(args.after), args.profile, persist=args.save_run))
        return 0
    if args.cmd == "manuscript":
        print_report(
            analyze_manuscript(
                collect_documents(args.paths),
                args.profile,
                project=args.project,
                persist=args.save_run,
            )
        )
        return 0
    if args.cmd == "calibrate":
        result = calibrate_corpus(
            [document["text"] for document in collect_documents(args.paths)],
            args.name,
            [document["text"] for document in collect_documents(args.reference)],
        )
        print(json.dumps(result, indent=2))
        return 0

    mode = "line_edit" if args.cmd == "line-edit" else args.cmd
    report = run_pipeline(
        RunRequest(
            text=read_text(args.file),
            profile=args.profile,
            mode=mode,
            passes=args.passes,
            aggressiveness=args.aggressiveness,
            persist=args.save_run,
        )
    )
    print_report(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
