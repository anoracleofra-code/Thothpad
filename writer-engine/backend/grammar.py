from __future__ import annotations

import hashlib
import json
import logging
import os
import queue
import re
import subprocess
import sys
import threading
import time
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

from backend import config  # noqa: E402
from backend.llm_clients import SafeRedirectHandler  # noqa: E402
from backend.models import AnalyzerResult, Flag, Severity  # noqa: E402
from backend.text_utils import Utf16Index, excerpt  # noqa: E402

ANALYZER_NAME = "grammar_mechanics"
HARPER_VERSION = "2.5.0"
PWA_ENDPOINT = "https://api.prowritingaid.com"
OBJECTIVE_KINDS = {
    "agreement", "boundaryerror", "capitalization", "grammar", "malapropism",
    "punctuation", "typo", "wordorder",
}


class _HarperSession:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._process: subprocess.Popen[str] | None = None
        self._responses: queue.Queue[str | None] = queue.Queue(maxsize=1)

    def _read_responses(self, process: subprocess.Popen[str]) -> None:
        assert process.stdout is not None
        try:
            for line in process.stdout:
                self._responses.put(line)
        finally:
            try:
                self._responses.put_nowait(None)
            except queue.Full:
                pass

    def _start(self) -> None:
        path = harper_path()
        if not path.is_file():
            raise FileNotFoundError(path)
        flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        self._responses = queue.Queue(maxsize=1)
        self._process = subprocess.Popen(
            [str(path)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            errors="strict",
            bufsize=1,
            env=_sanitized_env(),
            creationflags=flags,
        )
        threading.Thread(
            target=self._read_responses,
            args=(self._process,),
            name="thothpad-harper-reader",
            daemon=True,
        ).start()

    def is_running(self) -> bool:
        return self._process is not None and self._process.poll() is None

    def stop(self) -> None:
        process = self._process
        self._process = None
        if process is None:
            return
        if process.stdin:
            try:
                process.stdin.close()
            except OSError:
                pass
        try:
            process.wait(timeout=1)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=2)

    def request(self, payload: str, timeout: int) -> dict[str, Any]:
        with self._lock:
            if self._process is None or self._process.poll() is not None:
                self.stop()
                self._start()
            assert self._process is not None and self._process.stdin is not None
            try:
                self._process.stdin.write(payload.replace("\n", "") + "\n")
                self._process.stdin.flush()
                response = self._responses.get(timeout=timeout)
                if response is None:
                    raise ValueError("Harper exited before returning a response")
                if len(response.encode("utf-8")) > config.MAX_LLM_RESPONSE_BYTES:
                    raise ValueError("Harper response is too large")
                value = json.loads(response)
                if not isinstance(value, dict):
                    raise ValueError("Harper response must be an object")
                return value
            except (BrokenPipeError, OSError, queue.Empty):
                self.stop()
                raise ValueError("Harper did not return a response before the timeout") from None


_HARPER_SESSION = _HarperSession()


def _harper_name() -> str:
    return "thothpad-harper.exe" if os.name == "nt" else "thothpad-harper"


def harper_path() -> Path:
    if getattr(sys, "frozen", False):
        bundle_root = Path(getattr(sys, "_MEIPASS", Path(sys.executable).resolve().parent))
        return bundle_root / "grammar" / _harper_name()
    return Path(__file__).resolve().parents[1] / "harper-bridge" / "target" / "release" / _harper_name()


def grammar_status() -> dict[str, Any]:
    path = harper_path()
    return {
        "default": "harper",
        "harper": {
            "available": path.is_file(),
            "version": HARPER_VERSION,
            "offline": True,
        },
        "languagetool": {"available": True, "configured": False, "offline_capable": True},
        "prowritingaid": {"available": True, "configured": False, "remote": True},
    }


def warm_harper() -> bool:
    if not harper_path().is_file():
        return False
    try:
        _HARPER_SESSION.request(json.dumps({"text": ""}), 10)
        return True
    except (OSError, ValueError, json.JSONDecodeError):
        return False


def release_harper() -> bool:
    """Terminate the persistent Harper process and report whether one was live.

    The session restarts lazily on the next request, so this is a safe idle
    release: callers invoke it when the last owning document is disposed.
    """
    was_running = _HARPER_SESSION.is_running()
    _HARPER_SESSION.stop()
    return was_running


def _sanitized_env() -> dict[str, str]:
    allowed = ("SystemRoot", "WINDIR", "TEMP", "TMP", "LANG", "LC_ALL")
    return {name: os.environ[name] for name in allowed if name in os.environ}


def _rule_id(provider: str, identity: str) -> str:
    normalized = " ".join(identity.casefold().split())
    digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:12]
    return f"grammar.{provider}.{digest}"


def _severity(kind: str, *, provider: str) -> Severity:
    normalized = kind.casefold().replace("_", "").replace("-", "")
    if provider == "harper" and normalized in OBJECTIVE_KINDS:
        return "strong_flag"
    if any(value in normalized for value in ("grammar", "spell", "typo", "punct", "agreement")):
        return "strong_flag"
    if any(value in normalized for value in ("style", "read", "repeat", "diction", "cliche")):
        return "taste_flag"
    return "context_flag"


def _replacements(values: Any) -> list[str]:
    if not isinstance(values, list):
        return []
    result: list[str] = []
    for value in values[:20]:
        replacement = value.get("value", "") if isinstance(value, dict) else value
        if isinstance(replacement, str) and len(replacement) <= 500 and replacement not in result:
            result.append(replacement)
    return result


def _flag(
    text: str,
    *,
    provider: str,
    identity: str,
    kind: str,
    start: int,
    end: int,
    message: str,
    replacements: list[str],
    source: str,
    confidence: float,
) -> Flag | None:
    if start < 0 or end <= start or end > len(text):
        return None
    return Flag(
        type=f"{provider}:{kind}",
        severity=_severity(kind, provider=provider),
        start=start,
        end=end,
        excerpt=excerpt(text, start, end),
        suggestion=message or "Review this grammar or mechanics finding.",
        analyzer=ANALYZER_NAME,
        rule_id=_rule_id(provider, identity),
        confidence=confidence,
        source=source,
        explanation=message,
        replacements=replacements,
    )


def _harper(text: str, settings: dict[str, Any], *, format_name: str) -> AnalyzerResult:
    path = harper_path()
    if not path.is_file():
        logger.warning("Harper grammar provider unavailable: bundled engine missing")
        return AnalyzerResult(
            name=ANALYZER_NAME,
            score=0,
            metrics={
                "provider": "harper",
                "available": False, "error": "Bundled Harper engine is unavailable."},
        )
    try:
        ranges = _harper_ranges(text)

        def request_range(item: tuple[int, int]) -> tuple[int, dict[str, Any]]:
            start, end = item
            payload = json.dumps(
                {
                    "text": text[start:end],
                    "format": format_name,
                    "dialect": settings["dialect"],
                    "include_spelling": settings["include_spelling"],
                    "max_findings": settings["max_findings"],
                },
                ensure_ascii=False,
                separators=(",", ":"),
            )
            return start, _HARPER_SESSION.request(payload, settings["timeout"])

        # One persistent Harper process serves every segment of every request;
        # segments are processed sequentially to keep the fleet at a single
        # instance for the whole engine session.
        responses = [request_range(item) for item in ranges]
        findings = []
        version = HARPER_VERSION
        for base, payload_out in responses:
            version = str(payload_out.get("version", version))
            raw_findings = payload_out.get("findings", [])
            if not isinstance(raw_findings, list):
                raise ValueError("Harper response did not contain findings")
            for item in raw_findings:
                if not isinstance(item, dict):
                    continue
                kind = str(item.get("kind", "grammar"))
                message = str(item.get("message", "Harper grammar finding."))[:1000]
                finding = _flag(
                    text,
                    provider="harper",
                    identity=f"{kind}:{message}",
                    kind=kind,
                    start=base + int(item.get("start", -1)),
                    end=base + int(item.get("end", -1)),
                    message=message,
                    replacements=_replacements(item.get("replacements")),
                    source="harper-local",
                    confidence=min(0.99, max(0.5, int(item.get("priority", 63)) / 127)),
                )
                if finding:
                    findings.append(finding)
                    if len(findings) >= settings["max_findings"]:
                        break
            if len(findings) >= settings["max_findings"]:
                break
        return AnalyzerResult(
            name=ANALYZER_NAME,
            score=float(len(findings)),
            flags=findings,
            metrics={"provider": "harper", "available": True, "version": version, "segments": len(ranges)},
        )
    except (OSError, subprocess.SubprocessError, UnicodeError, ValueError, json.JSONDecodeError) as exc:
        return AnalyzerResult(
            name=ANALYZER_NAME,
            score=0,
            metrics={"provider": "harper", "available": True, "error": str(exc)[:500]},
        )


def _harper_ranges(text: str) -> list[tuple[int, int]]:
    if len(text) <= 100_000:
        return [(0, len(text))]
    boundaries = [0]
    for part in range(1, 4):
        target = len(text) * part // 4
        match = re.search(r"[.!?](?:\s|$)|\n", text[target : target + 4_000])
        boundary = target + match.end() if match else target
        if boundary > boundaries[-1]:
            boundaries.append(boundary)
    boundaries.append(len(text))
    return [
        (start, end)
        for start, end in zip(boundaries, boundaries[1:], strict=False)
        if end > start
    ]
def _read_json(request: urllib.request.Request, timeout: int) -> dict[str, Any]:
    opener = urllib.request.build_opener(SafeRedirectHandler())
    with opener.open(request, timeout=timeout) as response:
        raw = response.read(config.MAX_LLM_RESPONSE_BYTES + 1)
    if len(raw) > config.MAX_LLM_RESPONSE_BYTES:
        raise ValueError("grammar provider response is too large")
    value = json.loads(raw.decode("utf-8"))
    if not isinstance(value, dict):
        raise ValueError("grammar provider response must be an object")
    return value


def _languagetool(text: str, settings: dict[str, Any]) -> AnalyzerResult:
    values = {"text": text, "language": settings["language"]}
    if settings.get("username"):
        values.update({"username": settings["username"], "apiKey": settings["api_key"]})
    request = urllib.request.Request(
        settings["url"],
        data=urllib.parse.urlencode(values).encode("utf-8"),
        method="POST",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    try:
        payload = _read_json(request, settings["timeout"])
        index = Utf16Index(text)
        findings = []
        matches = payload.get("matches", [])
        for item in matches[: settings["max_findings"]] if isinstance(matches, list) else []:
            if not isinstance(item, dict):
                continue
            try:
                start_utf16 = int(item.get("offset", -1))
                start = index.codepoint_offset(start_utf16)
                end = index.codepoint_offset(start_utf16 + int(item.get("length", 0)))
            except (TypeError, ValueError):
                continue
            raw_rule = item.get("rule")
            rule = raw_rule if isinstance(raw_rule, dict) else {}
            kind = str(rule.get("id") or rule.get("category", {}).get("id") or "grammar")
            message = str(item.get("message", "LanguageTool grammar finding."))[:1000]
            finding = _flag(
                text,
                provider="languagetool",
                identity=kind,
                kind=kind,
                start=start,
                end=end,
                message=message,
                replacements=_replacements(item.get("replacements")),
                source="languagetool-cloud" if settings.get("remote") else "languagetool-local",
                confidence=0.9,
            )
            if finding:
                findings.append(finding)
        return AnalyzerResult(
            name=ANALYZER_NAME,
            score=float(len(findings)),
            flags=findings,
            metrics={"provider": "languagetool", "remote": settings.get("remote", False)},
        )
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        return AnalyzerResult(name=ANALYZER_NAME, score=0, metrics={"provider": "languagetool", "error": str(exc)[:500]})  # noqa: E501


def _pwa_payload(value: dict[str, Any]) -> tuple[str | None, dict[str, Any] | None]:
    task_id = value.get("taskId") or value.get("task_id")
    result = value.get("result")
    if isinstance(result, dict):
        return str(task_id) if task_id else None, result
    if isinstance(value.get("tags"), list):
        return str(task_id) if task_id else None, value
    return str(task_id) if task_id else None, None


def _prowritingaid(text: str, settings: dict[str, Any]) -> AnalyzerResult:
    url = f"{PWA_ENDPOINT}/api/async/text"
    request = urllib.request.Request(
        url,
        data=json.dumps(
            {
                "text": text,
                "language": settings["language"],
                "style": settings["style"],
                "reports": settings["reports"],
            }
        ).encode("utf-8"),
        method="POST",
        headers={"Content-Type": "application/json", "licenseCode": settings["api_key"]},
    )
    try:
        payload = _read_json(request, settings["timeout"])
        task_id, result = _pwa_payload(payload)
        deadline = time.monotonic() + settings["timeout"]
        while result is None and task_id and time.monotonic() < deadline:
            time.sleep(0.35)
            poll = urllib.request.Request(
                f"{PWA_ENDPOINT}/api/async/text/result/{urllib.parse.quote(task_id, safe='')}",
                method="GET",
                headers={"licenseCode": settings["api_key"]},
            )
            _, result = _pwa_payload(_read_json(poll, max(1, int(deadline - time.monotonic()))))
        if result is None:
            raise ValueError("ProWritingAid review timed out")

        index = Utf16Index(text)
        tags = result.get("tags", [])
        findings = []
        for item in tags[: settings["max_findings"]] if isinstance(tags, list) else []:
            if not isinstance(item, dict) or item.get("visible") is False:
                continue
            try:
                start = index.codepoint_offset(int(item.get("startPos", -1)))
                end = index.codepoint_offset(int(item.get("endPos", -1)))
            except (TypeError, ValueError):
                continue
            kind = str(item.get("category") or item.get("report") or item.get("id") or "style")
            identity = str(item.get("id") or item.get("hashId") or f"{kind}:{item.get('subCategory', '')}")
            message = str(item.get("hint") or item.get("message") or "ProWritingAid finding.")[:1000]
            finding = _flag(
                text,
                provider="prowritingaid",
                identity=identity,
                kind=kind,
                start=start,
                end=end,
                message=message,
                replacements=_replacements(item.get("suggestions")),
                source="prowritingaid-cloud",
                confidence=0.85,
            )
            if finding:
                findings.append(finding)
        return AnalyzerResult(
            name=ANALYZER_NAME,
            score=float(len(findings)),
            flags=findings,
            metrics={"provider": "prowritingaid", "remote": True},
        )
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        return AnalyzerResult(name=ANALYZER_NAME, score=0, metrics={"provider": "prowritingaid", "remote": True, "error": str(exc)[:500]})  # noqa: E501


def analyze_grammar(
    text: str,
    settings: dict[str, Any],
    *,
    format_name: str = "markdown",
) -> AnalyzerResult:
    provider = settings["provider"]
    if provider == "harper":
        return _harper(text, settings, format_name=format_name)
    if provider == "languagetool":
        return _languagetool(text, settings)
    return _prowritingaid(text, settings)

