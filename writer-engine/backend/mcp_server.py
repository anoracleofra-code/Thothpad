from __future__ import annotations

import json
import sys
from typing import Any

from backend import config
from backend.manuscript import (
    analyze_manuscript,
    calibrate_corpus,
    load_lens_baselines,
    read_project_timeline,
)
from backend.models import RunRequest
from backend.pipeline import compare_texts, run_pipeline
from backend.profiles import list_profiles
from backend.storage import load_run
from backend.validation import reject_json_constant as _reject_json_constant
from backend.validation import strict_bool_arg as _strict_bool
from backend.voice_profile import build_voice_profile

TOOLS = [
    {"name": "prose_diagnose", "description": "Analyze formulaic prose patterns; findings do not determine authorship.", "inputSchema": {"type": "object", "properties": {"text": {"type": "string"}, "profile": {"type": "string"}, "persist": {"type": "boolean"}, "overrides": {"type": "object", "description": "Per-analyzer profile overrides"}}, "required": ["text"]}},
    {"name": "prose_rewrite", "description": "Rewrite prose using ThothPad routing.", "inputSchema": {"type": "object", "properties": {"text": {"type": "string"}, "profile": {"type": "string"}, "mode": {"type": "string"}, "passes": {"type": "integer"}, "persist": {"type": "boolean"}}, "required": ["text"]}},
    {"name": "prose_deslop", "description": "Aggressively reduce AI prose tells.", "inputSchema": {"type": "object", "properties": {"text": {"type": "string"}, "profile": {"type": "string"}, "aggressiveness": {"type": "string"}, "persist": {"type": "boolean"}}, "required": ["text"]}},
    {"name": "prose_compare", "description": "Compare two drafts.", "inputSchema": {"type": "object", "properties": {"before": {"type": "string"}, "after": {"type": "string"}, "profile": {"type": "string"}, "persist": {"type": "boolean"}}, "required": ["before", "after"]}},
    {"name": "prose_build_voice_profile", "description": "Build a voice profile from samples.", "inputSchema": {"type": "object", "properties": {"samples": {"type": "array", "items": {"type": "string"}}, "name": {"type": "string"}}, "required": ["samples", "name"]}},
    {"name": "prose_list_profiles", "description": "List ThothPad profiles.", "inputSchema": {"type": "object", "properties": {}}},
    {"name": "prose_get_run", "description": "Load a saved ThothPad run report.", "inputSchema": {"type": "object", "properties": {"run_id": {"type": "string"}}, "required": ["run_id"]}},
    {"name": "prose_analyze_manuscript", "description": "Analyze multiple files as one manuscript for cross-file repetition, cliches, rhythm, and pattern hotspots.", "inputSchema": {"type": "object", "properties": {"documents": {"type": "array", "items": {"type": "object", "properties": {"name": {"type": "string"}, "text": {"type": "string"}}, "required": ["name", "text"]}}, "profile": {"type": "string"}, "project": {"type": "string"}, "persist": {"type": "boolean"}}, "required": ["documents"]}},
    {"name": "prose_calibrate_corpus", "description": "Build a model- or genre-specific overrepresentation profile from prose samples and optional human reference samples.", "inputSchema": {"type": "object", "properties": {"samples": {"type": "array", "items": {"type": "string"}}, "reference_samples": {"type": "array", "items": {"type": "string"}}, "name": {"type": "string"}}, "required": ["samples", "name"]}},
    {"name": "prose_quality_timeline", "description": "Return the ordered quality-ledger runs recorded for a project.", "inputSchema": {"type": "object", "properties": {"project": {"type": "string"}}, "required": ["project"]}},
    {"name": "prose_lens_baselines", "description": "Return stored genre lens-density baselines for a calibration name.", "inputSchema": {"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"]}},
]


def tool_call(name: str, args: dict[str, Any]) -> dict[str, Any]:
    if name == "prose_diagnose":
        overrides = args.get("overrides") if isinstance(args.get("overrides"), dict) else None
        return run_pipeline(RunRequest(text=args["text"], profile=args.get("profile", config.DEFAULT_PROFILE), mode="diagnose", persist=_strict_bool(args, "persist"), overrides=overrides))
    if name == "prose_rewrite":
        return run_pipeline(RunRequest(text=args["text"], profile=args.get("profile", config.DEFAULT_PROFILE), mode=args.get("mode", "rewrite"), passes=int(args.get("passes", 1)), persist=_strict_bool(args, "persist")))
    if name == "prose_deslop":
        return run_pipeline(RunRequest(text=args["text"], profile=args.get("profile", config.DEFAULT_PROFILE), mode="deslop", aggressiveness=args.get("aggressiveness", "medium"), persist=_strict_bool(args, "persist")))
    if name == "prose_compare":
        return compare_texts(args["before"], args["after"], args.get("profile", config.DEFAULT_PROFILE), persist=_strict_bool(args, "persist"))
    if name == "prose_build_voice_profile":
        return build_voice_profile(args["samples"], args["name"])
    if name == "prose_quality_timeline":
        return read_project_timeline(args["project"])
    if name == "prose_lens_baselines":
        return {"name": args["name"], "baselines": load_lens_baselines(args["name"])}
    if name == "prose_list_profiles":
        return {"profiles": list_profiles()}
    if name == "prose_get_run":
        return load_run(args["run_id"])
    if name == "prose_analyze_manuscript":
        return analyze_manuscript(
            args["documents"],
            args.get("profile", config.DEFAULT_PROFILE),
            project=args.get("project"),
            persist=_strict_bool(args, "persist"),
        )
    if name == "prose_calibrate_corpus":
        return calibrate_corpus(
            args["samples"],
            args["name"],
            args.get("reference_samples", []),
        )
    raise ValueError(f"unknown tool: {name}")


def respond(msg_id: Any, result: Any = None, error: Any = None) -> None:
    payload = {"jsonrpc": "2.0", "id": msg_id}
    if error is not None:
        payload["error"] = {"code": -32000, "message": str(error)}
    else:
        payload["result"] = result
    body = json.dumps(payload, ensure_ascii=False, allow_nan=False)
    if len(body.encode("utf-8")) > config.MAX_RESPONSE_BYTES:
        body = json.dumps({
            "jsonrpc": "2.0",
            "id": msg_id,
            "error": {"code": -32001, "message": "response exceeds configured limit"},
        })
    print(body, flush=True)


def main() -> int:
    reader = getattr(sys.stdin, "buffer", sys.stdin)
    while True:
        raw = reader.readline(config.MAX_FRAME_BYTES + 1)
        if not raw:
            break
        raw_size = len(raw.encode("utf-8")) if isinstance(raw, str) else len(raw)
        if raw_size > config.MAX_FRAME_BYTES:
            respond(None, error="request exceeds configured limit")
            return 1
        if isinstance(raw, str):
            line = raw
        else:
            try:
                line = raw.decode("utf-8")
            except UnicodeDecodeError as exc:
                respond(None, error=exc)
                continue
        if not line.strip():
            continue
        msg_id: Any = None
        try:
            msg = json.loads(line, parse_constant=_reject_json_constant)
            if not isinstance(msg, dict):
                raise ValueError("JSON-RPC message must be an object")
            method = msg.get("method")
            msg_id = msg.get("id")
            if method == "initialize":
                respond(msg_id, {"protocolVersion": "2024-11-05", "capabilities": {"tools": {}}, "serverInfo": {"name": "thothpad", "version": config.ENGINE_VERSION}})
            elif method == "tools/list":
                respond(msg_id, {"tools": TOOLS})
            elif method == "tools/call":
                params = msg.get("params", {})
                result = tool_call(params.get("name"), params.get("arguments", {}))
                respond(msg_id, {"content": [{
                    "type": "text",
                    "text": json.dumps(result, ensure_ascii=False, allow_nan=False),
                }]})
            elif method == "notifications/initialized":
                continue
            else:
                respond(msg_id, error=f"unsupported method: {method}")
        except Exception as exc:
            respond(msg_id, error=exc)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
