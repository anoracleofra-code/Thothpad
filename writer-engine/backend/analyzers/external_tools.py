from __future__ import annotations

import importlib.util
import json
import shutil
import subprocess
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

from backend import config
from backend.analyzers.possible_adverbs import spacy_model_status
from backend.llm_clients import _validated_url
from backend.models import AnalyzerResult, Flag
from backend.text_utils import Utf16Index, excerpt


class _ApprovedRedirectHandler(urllib.request.HTTPRedirectHandler):
    def __init__(self, approved_endpoints: set[str]):
        super().__init__()
        self.approved_endpoints = approved_endpoints

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        target = _validated_url(newurl)
        if target not in self.approved_endpoints:
            raise urllib.error.HTTPError(
                req.full_url,
                code,
                "LanguageTool redirect target is not approved",
                headers,
                fp,
            )
        return super().redirect_request(req, fp, code, msg, headers, target)


def _sanitized_env() -> dict[str, str]:
    import os

    allowed = ("SystemRoot", "WINDIR", "TEMP", "TMP", "LANG", "LC_ALL")
    return {name: os.environ[name] for name in allowed if name in os.environ}


def integration_status() -> dict[str, dict[str, Any]]:
    harper_command = shutil.which("harper-cli") or shutil.which("harper")
    return {
        "proselint": {
            "available": importlib.util.find_spec("proselint") is not None,
            "kind": "python",
            "purpose": "Traditional editorial linting, cliches, redundancy, usage, and mixed metaphors.",
        },
        "spacy": {
            "available": spacy_model_status()["available"],
            "model": spacy_model_status()["model"],
            "kind": "python",
            "purpose": "Advanced lemmatization and named-entity-aware manuscript analysis.",
        },
        "lexicalrichness": {
            "available": importlib.util.find_spec("lexicalrichness") is not None,
            "kind": "python",
            "purpose": "Reference implementation for lexical-diversity metrics; native equivalents remain available.",
        },
        "vale": {
            "available": shutil.which("vale") is not None,
            "kind": "command",
            "purpose": "Optional markup-aware style packs.",
        },
        "harper": {
            "available": harper_command is not None,
            "kind": "command",
            "purpose": "Optional offline grammar and spelling pass.",
        },
        "languagetool": {
            "available": shutil.which("languagetool-commandline") is not None,
            "kind": "command_or_http",
            "purpose": "Optional multilingual grammar and style service.",
        },
    }


def _line_col_offset(text: str, line: int, column: int) -> int:
    lines = text.splitlines(keepends=True)
    line_index = max(0, min(line - 1, len(lines) - 1))
    return sum(len(value) for value in lines[:line_index]) + max(0, column - 1)


class ExternalToolsAnalyzer:
    name = "external_tools"

    def analyze(self, text: str, profile: dict[str, Any] | None = None) -> AnalyzerResult:
        profile = profile or {}
        enabled = profile.get("_approved_external_tools", {}) or {}
        flags: list[Flag] = []
        errors: list[str] = []

        if enabled.get("proselint"):
            try:
                from proselint.checks import __register__
                from proselint.registry import CheckRegistry
                from proselint.tools import LintFile

                registry = CheckRegistry()
                registry.register_many(__register__)
                for result in LintFile("<thothpad>", text).lint():
                    check = result.check_result
                    start, end = check.span
                    flags.append(
                        Flag(
                            type=f"proselint:{check.check_path}",
                            severity="taste_flag",
                            start=start,
                            end=end,
                            excerpt=excerpt(text, start, end),
                            suggestion=check.message,
                            source="external",
                        )
                    )
            except (ImportError, AttributeError, ValueError) as exc:
                errors.append(f"proselint: {exc}")

        vale_path = enabled.get("vale_path")
        if vale_path:
            path: Path | None = None
            try:
                with tempfile.NamedTemporaryFile(
                    mode="w",
                    suffix=".md",
                    encoding="utf-8",
                    delete=False,
                ) as handle:
                    handle.write(text)
                    path = Path(handle.name)
                process = subprocess.run(
                    [vale_path, "--output=JSON", "--no-exit", str(path)],
                    capture_output=True,
                    text=True,
                    timeout=30,
                    check=False,
                    env=_sanitized_env(),
                )
                payload = json.loads(process.stdout or "{}")
                alerts: list[Any] = next(iter(payload.values()), []) if isinstance(payload, dict) else []
                for alert in alerts:
                    span = alert.get("Span", [1, 1])
                    start = _line_col_offset(text, int(alert.get("Line", 1)), int(span[0]))
                    end = _line_col_offset(text, int(alert.get("Line", 1)), int(span[-1]) + 1)
                    flags.append(
                        Flag(
                            type=f"vale:{alert.get('Check', 'style')}",
                            severity="taste_flag",
                            start=start,
                            end=max(start + 1, end),
                            excerpt=excerpt(text, start, max(start + 1, end)),
                            suggestion=str(alert.get("Message", "Vale style finding.")),
                            source="external",
                        )
                    )
            except (OSError, subprocess.SubprocessError, json.JSONDecodeError, ValueError) as exc:
                errors.append(f"vale: {exc}")
            finally:
                if path:
                    path.unlink(missing_ok=True)

        harper_command = enabled.get("harper_path")
        if harper_command:
            path = None
            try:
                with tempfile.NamedTemporaryFile(
                    mode="w",
                    suffix=".md",
                    encoding="utf-8",
                    delete=False,
                ) as handle:
                    handle.write(text)
                    path = Path(handle.name)
                process = subprocess.run(
                    [
                        harper_command,
                        "--no-color",
                        "lint",
                        "--format",
                        "json",
                        str(path),
                    ],
                    capture_output=True,
                    text=True,
                    timeout=30,
                    check=False,
                    env=_sanitized_env(),
                )
                payload = json.loads(process.stdout or "[]")
                for file_result in payload if isinstance(payload, list) else []:
                    for lint in file_result.get("lints", []):
                        span = lint.get("span", {})
                        start = int(span.get("char_start", 0))
                        end = int(span.get("char_end", start + 1))
                        suggestions = lint.get("suggestions", [])
                        suggestion = str(lint.get("message", "Harper grammar finding."))
                        if suggestions:
                            suggestion += f" Suggestions: {', '.join(map(str, suggestions[:4]))}."
                        flags.append(
                            Flag(
                                type=f"harper:{lint.get('rule', 'grammar')}",
                                severity="taste_flag",
                                start=start,
                                end=max(start + 1, end),
                                excerpt=excerpt(text, start, max(start + 1, end)),
                                suggestion=suggestion,
                                source="external",
                            )
                        )
            except (OSError, subprocess.SubprocessError, json.JSONDecodeError, ValueError) as exc:
                errors.append(f"harper: {exc}")
            finally:
                if path:
                    path.unlink(missing_ok=True)

        language_tool = enabled.get("languagetool")
        if language_tool:
            settings = language_tool if isinstance(language_tool, dict) else {}
            url = _validated_url(settings.get("url"))
            language = str(settings.get("language", "en-US"))
            approved_endpoints = {
                _validated_url(value)
                for value in settings.get("approved_endpoints", [url])
            }
            try:
                data = urllib.parse.urlencode({"text": text, "language": language}).encode("utf-8")
                request = urllib.request.Request(url, data=data, method="POST")
                opener = urllib.request.build_opener(
                    _ApprovedRedirectHandler(approved_endpoints)
                )
                with opener.open(request, timeout=20) as response:
                    raw = response.read(config.MAX_LLM_RESPONSE_BYTES + 1)
                if len(raw) > config.MAX_LLM_RESPONSE_BYTES:
                    raise ValueError("LanguageTool response is too large")
                payload = json.loads(raw.decode("utf-8"))
                index = Utf16Index(text)
                for match in payload.get("matches", []):
                    start_utf16 = int(match.get("offset", 0))
                    end_utf16 = start_utf16 + int(match.get("length", 1))
                    start = index.codepoint_offset(start_utf16)
                    end = index.codepoint_offset(end_utf16)
                    rule_id = match.get("rule", {}).get("id", "style")
                    flags.append(
                        Flag(
                            type=f"languagetool:{rule_id}",
                            severity="taste_flag",
                            start=start,
                            end=end,
                            excerpt=excerpt(text, start, end),
                            suggestion=str(match.get("message", "LanguageTool finding.")),
                            source="external",
                        )
                    )
            except (OSError, ValueError, json.JSONDecodeError) as exc:
                errors.append(f"languagetool: {exc}")

        return AnalyzerResult(
            name=self.name,
            score=float(len(flags)),
            flags=flags,
            metrics={"status": integration_status(), "errors": errors},
        )
