from __future__ import annotations

import hashlib
from dataclasses import asdict, dataclass, field
from typing import Any, Literal

from backend.text_utils import Utf16Index

Severity = Literal["hard_fail", "strong_flag", "context_flag", "taste_flag"]


@dataclass
class Flag:
    type: str
    severity: Severity
    start: int
    end: int
    excerpt: str
    suggestion: str
    analyzer: str = ""
    rule_id: str = ""
    confidence: float = 1.0
    source: str = "deterministic"
    explanation: str = ""
    replacements: list[str] = field(default_factory=list)
    # Codepoint spans of related occurrences (e.g., the earlier half of a
    # close-repeat pair). Emitted as extra_spans_utf16 only when non-empty so
    # envelopes stay byte-compatible with older consumers.
    extra_spans: list[tuple[int, int]] = field(default_factory=list)

    def to_dict(
        self,
        text: str | None = None,
        base_offset_utf16: int = 0,
        utf16_index: Utf16Index | None = None,
    ) -> dict[str, Any]:
        data = asdict(self)
        data["rule_id"] = self.rule_id or f"{self.analyzer}.{self.type}".strip(".")
        data["level"] = self.severity
        data["explanation"] = self.explanation or self.suggestion
        if text is not None:
            index = utf16_index or Utf16Index(text)
            data["start_utf16"] = base_offset_utf16 + index[self.start]
            data["end_utf16"] = base_offset_utf16 + index[self.end]
            if self.extra_spans:
                data["extra_spans_utf16"] = [
                    [base_offset_utf16 + index[span_start], base_offset_utf16 + index[span_end]]
                    for span_start, span_end in self.extra_spans
                ]
            identity = f"{data['rule_id']}:{data['start_utf16']}:{data['end_utf16']}:{self.excerpt}"
            data["id"] = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:20]
        return data


@dataclass
class AnalyzerResult:
    name: str
    score: float
    flags: list[Flag] = field(default_factory=list)
    metrics: dict[str, Any] = field(default_factory=dict)

    def to_dict(
        self,
        text: str | None = None,
        base_offset_utf16: int = 0,
        utf16_index: Utf16Index | None = None,
    ) -> dict[str, Any]:
        return {
            "name": self.name,
            "score": self.score,
            "flags": [flag.to_dict(text, base_offset_utf16, utf16_index) for flag in self.flags],
            "metrics": self.metrics,
        }


@dataclass
class RunRequest:
    text: str = ""
    profile: str = "creative-default"
    profile_snapshot: dict[str, Any] | None = None
    mode: str = "diagnose"
    passes: int = 1
    provider: dict[str, Any] | None = None
    overrides: dict[str, Any] | None = None
    preserve: list[str] | None = None
    genre: str | None = None
    aggressiveness: str = "medium"
    persist: bool = False


@dataclass
class Derivation:
    readerProfile: str
    registerTarget: str
    sceneAnchor: str
    textureConstraint: str
    antiPattern: str = ""

    def to_dict(self) -> dict[str, str]:
        return asdict(self)
