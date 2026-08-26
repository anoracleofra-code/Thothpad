"""Length-prefixed JSON frame protocol shared by the engine sidecar.

This module is the first slice of the sidecar split: the wire-format
constants, frame encode/decode helpers, and protocol error type live here so
the process supervisor and operation dispatch can be extracted next without
dragging transport details along.
"""

from __future__ import annotations

import json
import re
from typing import Any, BinaryIO

from backend import config
from backend.validation import reject_json_constant as _reject_json_constant_impl

PROTOCOL_MAJOR = 1
PROTOCOL_MINOR = 2

MAX_HEADER_COUNT = 16
MAX_HEADER_BYTES = 16_384


MAX_REQUEST_ID_LENGTH = 128
_REQUEST_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}")
PROCESS_OPERATIONS = {
    "analyze_document", "query_findings", "query_overlay_spans",
    "dispose_analysis", "analyze_manuscript", "rewrite", "compare",
}
_INTERNAL_OPERATIONS = {"dispose_document_snapshots"}
_STORE_ONLY_OPERATIONS = {"dispose_analysis", "query_findings", "query_overlay_spans"}
OPERATIONS = (
    "initialize", "capabilities", "list_profiles", "get_profile", "save_profile",
    "import_profile", "export_profile", "open_document", "patch_document",
    "dispose_document", "analyze_region", "analyze_document", "query_findings",
    "query_overlay_spans", "dispose_analysis", "analyze_manuscript", "rewrite",
    "compare", "cancel", "shutdown", "quality_timeline", "lens_baselines",
)



def request_id(value: Any, name: str = "request_id") -> str:
    if not isinstance(value, str) or not _REQUEST_ID.fullmatch(value):
        raise ProtocolError(
            f"{name} must be 1-{MAX_REQUEST_ID_LENGTH} ASCII identifier characters"
        )
    return value


class ProtocolError(ValueError):
    pass


def reject_json_constant(value: str) -> None:
    _reject_json_constant_impl(value, ProtocolError)


def read_frame(stream: BinaryIO) -> dict[str, Any] | None:
    headers: dict[str, str] = {}
    header_bytes = 0
    header_count = 0
    while True:
        line = stream.readline(8193)
        if not line:
            if not headers:
                return None
            raise ProtocolError("unexpected EOF in headers")
        header_bytes += len(line)
        if header_bytes > MAX_HEADER_BYTES:
            raise ProtocolError("frame headers are too large")
        if len(line) > 8192:
            raise ProtocolError("header line too long")
        if not line.endswith(b"\n"):
            raise ProtocolError("unterminated frame header")
        if line in {b"\r\n", b"\n"}:
            break
        header_count += 1
        if header_count > MAX_HEADER_COUNT:
            raise ProtocolError("too many frame headers")
        try:
            name, value = line.decode("ascii").split(":", 1)
        except (UnicodeDecodeError, ValueError) as exc:
            raise ProtocolError("invalid frame header") from exc
        key = name.strip().lower()
        if not key:
            raise ProtocolError("frame header name must not be empty")
        if key in headers:
            raise ProtocolError(f"duplicate frame header: {key}")
        headers[key] = value.strip()
    try:
        length = int(headers["content-length"])
    except (KeyError, ValueError) as exc:
        raise ProtocolError("missing or invalid Content-Length") from exc
    if length < 0 or length > config.MAX_FRAME_BYTES:
        raise ProtocolError(f"Content-Length must be between 0 and {config.MAX_FRAME_BYTES}")
    body = _read_exact(stream, length)
    try:
        value = json.loads(body.decode("utf-8"), parse_constant=reject_json_constant)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ProtocolError("frame body must be a finite UTF-8 JSON object") from exc
    if not isinstance(value, dict):
        raise ProtocolError("frame body must be a JSON object")
    return value


def _read_exact(stream: BinaryIO, length: int) -> bytes:
    chunks: list[bytes] = []
    remaining = length
    while remaining:
        chunk = stream.read(remaining)
        if not chunk:
            raise ProtocolError("unexpected EOF in frame body")
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def encode_frame(message: dict[str, Any]) -> bytes:
    try:
        body = json.dumps(
            message,
            ensure_ascii=False,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except ValueError as exc:
        raise ProtocolError("response contains a non-finite JSON value") from exc
    if len(body) > config.MAX_RESPONSE_BYTES:
        raise ProtocolError(f"response exceeds the {config.MAX_RESPONSE_BYTES}-byte limit")
    return f"Content-Length: {len(body)}\r\n\r\n".encode("ascii") + body


def params_of(message: dict[str, Any]) -> dict[str, Any]:
    params = message.get("params")
    if params is None:
        return message
    if not isinstance(params, dict):
        raise ValueError("params must be an object")
    return params
