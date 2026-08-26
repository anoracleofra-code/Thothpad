from __future__ import annotations

import json
import re
from functools import lru_cache
from typing import Any

from backend import config
from backend.models import AnalyzerResult, Flag
from backend.text_utils import excerpt

from .dialogue import dialogue_spans, inside_dialogue

DATA_DIR = config.BACKEND_DIR / "data" / "slopless"
CATEGORIES = {
    "general": "cliches.json",
    "genre": "genre-cliches.json",
    "self_help": "self-help-cliches.json",
    "corporate": "corporate-speak.json",
    "wordiness": "wordiness-patterns.json",
    "redundancy": "redundancy-patterns.json",
    "novel": "novel-cliches.json",
}
@lru_cache(maxsize=1)
def _load_rule_pack() -> dict[str, list[str]]:
    pack: dict[str, list[str]] = {}
    for category, filename in CATEGORIES.items():
        path = DATA_DIR / filename
        if not path.exists():
            pack[category] = []
            continue
        values = json.loads(path.read_text(encoding="utf-8"))
        pack[category] = [
            str(value).strip()
            for value in values
            if isinstance(value, str) and value.strip()
        ]
    return pack


@lru_cache(maxsize=64)
def _compiled_rule_pack(active_categories: tuple[str, ...]) -> re.Pattern[str] | None:
    groups: list[str] = []
    for category in active_categories:
        phrases = _load_rule_pack().get(category, [])
        alternatives = [
            r"\s+".join(re.escape(part) for part in phrase.split())
            for phrase in sorted(phrases, key=len, reverse=True)
        ]
        if alternatives:
            groups.append(f"(?P<{category}>" + "|".join(alternatives) + ")")
    if not groups:
        return None
    return re.compile(r"(?<!\w)(?:" + "|".join(groups) + r")(?!\w)", re.I)


class ClicheAnalyzer:
    name = "cliches"

    def analyze(self, text: str, profile: dict[str, Any] | None = None) -> AnalyzerResult:
        profile = profile or {}
        categories = profile.get("cliche_categories", {}) or {}
        settings = profile.get("cliches", {}) or {}
        excluded_dialogue = dialogue_spans(text) if settings.get("ignore_dialogue", True) else []
        flags: list[Flag] = []
        category_counts: dict[str, int] = {}
        total_matches = 0
        active = tuple(category for category in CATEGORIES if categories.get(category, True) is not False)
        pattern = _compiled_rule_pack(active)
        if pattern:
            for match in pattern.finditer(text):
                if inside_dialogue(match.start(), match.end(), excluded_dialogue):
                    continue
                category = match.lastgroup or "general"
                category_counts[category] = category_counts.get(category, 0) + 1
                total_matches += 1
                flags.append(
                    Flag(
                        type=f"{category}_cliche",
                        severity="context_flag" if category in {"wordiness", "redundancy"} else "taste_flag",
                        start=match.start(),
                        end=match.end(),
                        excerpt=excerpt(text, match.start(), match.end()),
                        suggestion="Replace the stock phrase with wording specific to this speaker, scene, claim, or object.",
                    )
                )

        flags.sort(key=lambda flag: (flag.start, flag.end))
        return AnalyzerResult(
            name=self.name,
            score=float(total_matches),
            flags=flags,
            metrics={
                "category_counts": category_counts,
                "rule_count": sum(len(values) for values in _load_rule_pack().values()),
                "total_matches": total_matches,
                "findings_truncated": False,
                "ignored_dialogue": bool(excluded_dialogue),
                "source": "Slopless MIT rule data, contextualized by ThothPad",
            },
        )
