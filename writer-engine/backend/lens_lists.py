"""Literal, shareable phrase-list overlays for the existing analysis lenses."""
from __future__ import annotations

import hashlib
import re
from typing import Any

from backend.analyzers.dialogue import dialogue_spans, inside_dialogue
from backend.models import AnalyzerResult, Flag
from backend.text_utils import cancellation_checkpoint, excerpt

LENS_ANALYZERS = {
    "general_rules": ("profile_patterns",),
    "possible_adverbs": ("possible_adverbs",),
    "possible_adjectives": ("possible_adjectives",),
    "possible_verbs": ("possible_verbs",),
    "filter_words": ("filter_words",),
    "cliches": ("cliches", "rules_library"),
    "formulaic_patterns": ("slop_score", "binary_contrast", "negative_listing", "triad_cadence"),
    "repetition_rhythm": ("rhythm", "stylometry", "calibration"),
    "repetition": ("repetition",),
    "body_cinematic": ("body_cliches", "cinematic_fog"),
    "abstraction_agency": ("false_agency", "vague_abstracts"),
    "metaphor_texture": ("metaphor_density", "concrete_anchor"),
}


def validate_lens_lists(lists: Any) -> None:
    if not isinstance(lists, dict):
        raise ValueError("lens_lists must be an object")
    for lens, settings in lists.items():
        if lens not in LENS_ANALYZERS or not isinstance(settings, dict):
            raise ValueError("lens_lists contains an unknown lens or invalid list")
        if set(settings) - {"name", "include", "exclude", "use_builtin", "ignore_dialogue"}:
            raise ValueError("unknown lens list setting")
        name = settings.get("name", "")
        if not isinstance(name, str) or len(name) > 128 or any(c in name for c in "\r\n\x00"):
            raise ValueError("lens list name must be a single line of at most 128 characters")
        for key in ("use_builtin", "ignore_dialogue"):
            if key in settings and type(settings[key]) is not bool:
                raise ValueError(f"lens list {key} must be a boolean")
        for key in ("include", "exclude"):
            values = settings.get(key, [])
            if not isinstance(values, list) or len(values) > 500:
                raise ValueError(f"lens list {key} must contain at most 500 phrases")
            if any(not isinstance(v, str) or not v.strip() or len(v) > 256
                   or any(c in v for c in "\r\n\x00") for v in values):
                raise ValueError("lens list phrases must be nonempty single lines of at most 256 characters")


def _normalized(text: str) -> str:
    return " ".join(text.split()).casefold()


def apply_lens_lists(results: list[AnalyzerResult], text: str, lists: dict[str, Any]) -> None:
    validate_lens_lists(lists)
    by_name = {result.name: result for result in results}
    for lens, settings in lists.items():
        cancellation_checkpoint()
        targets = [by_name[name] for name in LENS_ANALYZERS[lens] if name in by_name]
        if not targets:
            continue  # Honor requested analyzer/lane scope.
        excluded = {_normalized(phrase) for phrase in settings.get("exclude", [])}
        spans = dialogue_spans(text) if settings.get("ignore_dialogue", False) else []
        for result in targets:
            before = len(result.flags)
            result.flags = [flag for flag in result.flags
                            if settings.get("use_builtin", True)
                            and _normalized(text[flag.start:flag.end]) not in excluded
                            and not inside_dialogue(flag.start, flag.end, spans)
                            and not any(_normalized(text[start:end]) in excluded
                                        for start, end in flag.extra_spans)]
            if result.score_semantics == "count" and before:
                result.score *= len(result.flags) / before
        target = targets[0]
        occupied = {(flag.start, flag.end) for result in targets for flag in result.flags}
        # Escaped literals only: imported lists cannot inject regular expressions.
        unique = {_normalized(p): " ".join(p.split()) for p in settings.get("include", [])}
        phrases = sorted((phrase for key, phrase in unique.items() if key not in excluded),
                         key=lambda p: (-len(p), p))
        if phrases:
            pattern = re.compile(r"(?<!\w)(?:" + "|".join(
                re.escape(p).replace(r"\ ", r"\s+") for p in phrases) + r")(?!\w)", re.I)
            for match in pattern.finditer(text):
                cancellation_checkpoint()
                start, end = match.span()
                if (start, end) in occupied or inside_dialogue(start, end, spans):
                    continue
                phrase_id = hashlib.sha256(_normalized(match.group()).encode()).hexdigest()[:16]
                target.flags.append(Flag(
                    type="custom_phrase", severity="taste_flag", start=start, end=end,
                    excerpt=excerpt(text, start, end), source="profile",
                    rule_id=f"lens_list.{lens}.{phrase_id}",
                    explanation=f"Matched your {settings.get('name') or lens} phrase list.",
                    suggestion="Review this phrase according to your custom list.",
                ))
                if target.score_semantics == "count":
                    target.score += 1
        for result in targets:
            result.flags.sort(key=lambda flag: (flag.start, flag.end))
            result.metrics["total_findings"] = len(result.flags)
            result.metrics["custom_lens_list"] = True
