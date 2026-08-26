from __future__ import annotations

import json
import re
from collections import Counter
from pathlib import Path
from typing import Any

from backend import config
from backend.models import AnalyzerResult, Flag
from backend.text_utils import excerpt, words
from backend.validation import validate_profile_name


def _calibration_path(value: str) -> Path:
    candidate = Path(value)
    if candidate.is_absolute() or candidate.name != value:
        raise ValueError("calibration_profile must be a calibration name, not a path")
    filename = value if value.endswith(".json") else f"{value}.json"
    validate_profile_name(Path(filename).stem)
    return config.PROFILES_DIR / "calibrations" / filename


class CalibrationAnalyzer:
    name = "calibration"

    def analyze(self, text: str, profile: dict[str, Any] | None = None) -> AnalyzerResult:
        profile = profile or {}
        value = profile.get("calibration_profile")
        if not value:
            return AnalyzerResult(
                name=self.name,
                score=0.0,
                metrics={"active": False},
            )
        try:
            path = _calibration_path(str(value))
        except ValueError as exc:
            return AnalyzerResult(name=self.name, score=0.0, metrics={"active": False, "error": str(exc)})
        if not path.exists():
            return AnalyzerResult(
                name=self.name,
                score=0.0,
                metrics={"active": False, "error": f"Calibration not found: {path}"},
            )

        data = json.loads(path.read_text(encoding="utf-8"))
        token_counts = Counter(words(text))
        flags: list[Flag] = []
        hits: list[dict[str, Any]] = []

        for item in data.get("top_overrepresented_words", [])[:250]:
            word = str(item.get("word", ""))
            ratio = float(item.get("overrepresentation_ratio", 0))
            count = token_counts[word]
            if not word or ratio < 5 or count < 2:
                continue
            match = re.search(rf"\b{re.escape(word)}\b", text, re.I)
            if not match:
                continue
            flags.append(
                Flag(
                    type="model_specific_word",
                    severity="context_flag",
                    start=match.start(),
                    end=match.end(),
                    excerpt=excerpt(text, match.start(), match.end()),
                    suggestion=f"'{word}' appears {count} times and was {ratio:.1f}x over-represented in calibration samples.",
                    source="heuristic",
                )
            )
            hits.append({"value": word, "count": count, "ratio": ratio, "kind": "word"})

        for size, items in (data.get("top_ngrams", {}) or {}).items():
            for item in items[:150]:
                phrase = str(item.get("phrase", ""))
                ratio = float(item.get("overrepresentation_ratio", 0))
                if not phrase or ratio < 5:
                    continue
                pattern = r"\b" + r"\s+".join(map(re.escape, phrase.split())) + r"\b"
                matches = list(re.finditer(pattern, text, re.I))
                if len(matches) < 2:
                    continue
                match = matches[0]
                flags.append(
                    Flag(
                        type="model_specific_phrase",
                        severity="context_flag",
                        start=match.start(),
                        end=match.end(),
                        excerpt=excerpt(text, match.start(), match.end()),
                        suggestion=f"This calibrated {size}-gram appears {len(matches)} times and was {ratio:.1f}x over-represented.",
                        source="heuristic",
                    )
                )
                hits.append(
                    {
                        "value": phrase,
                        "count": len(matches),
                        "ratio": ratio,
                        "kind": f"{size}-gram",
                    }
                )

        return AnalyzerResult(
            name=self.name,
            score=float(len(flags)),
            flags=flags,
            metrics={"active": True, "profile": data.get("name"), "path": str(path), "hits": hits},
        )
