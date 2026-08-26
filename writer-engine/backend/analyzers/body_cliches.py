from __future__ import annotations

from typing import Any

from backend.models import AnalyzerResult

from .pattern_helpers import regex_flags


class BodyClicheAnalyzer:
    name = "body_cliches"

    def analyze(self, text: str, profile: dict[str, Any] | None = None) -> AnalyzerResult:
        return regex_flags(name=self.name, text=text, patterns=PATTERNS)  # type: ignore[arg-type]


_SUGGESTION = "Replace stock body language with an action specific to the scene and character."
PATTERNS: list[tuple[str, str, str, str, str | tuple[str, ...]]] = [
    ("breath_caught", r"\b(?:his|her|their|my)?\s*breath\s+caught(?:\s+in\s+(?:his|her|their|my)\s+throat)?\b", "context_flag", _SUGGESTION, "breath"),
    ("jaw_clenched", r"\b(?:his|her|their|my)?\s*jaw\s+(?:clenched|tightened|worked)\b", "context_flag", _SUGGESTION, "jaw"),
    ("heart_pounded", r"\b(?:his|her|their|my)?\s*heart\s+(?:pounded|raced|hammered|skipped\s+a\s+beat)\b", "context_flag", _SUGGESTION, "heart"),
    ("eyes_widened", r"\b(?:his|her|their|my)?\s*eyes\s+(?:widened|narrowed|filled|flashed)\b", "context_flag", _SUGGESTION, "eyes"),
    ("fists_balled", r"\b(?:his|her|their|my)?\s*fists?\s+(?:balled|clenched)\b", "context_flag", _SUGGESTION, "fist"),
    ("shiver_spine", r"\b(?:shiver|chill)\s+(?:ran|went|traveled)\s+(?:down|up|along)\s+(?:his|her|their|my)?\s*spine\b", "context_flag", _SUGGESTION, "spine"),
    ("blood_ran_cold", r"\b(?:his|her|their|my)?\s*blood\s+ran\s+cold\b", "context_flag", _SUGGESTION, "blood"),
    # Gesture-crutch beats mined from an 84-book corpus (see
    # benchmark-results/novel-refinement/mined-cliche-candidates-corpus2.json):
    # each appeared 40-445 times across 10-30 published novels.
    # The 5th tuple element is a mandatory literal (or set of alternatives);
    # regex_flags skips the full-text scan when none is present.
    ("deep_breath", r"\b(?:took|drew|let\s+out)\s+(?:a\s+)?(?:deep|long|slow|steadying)\s+breath\b", "context_flag", _SUGGESTION, "breath"),
    ("leaned_back_chair", r"\bleaned\s+back\s+in\s+(?:his|her|their|my)\s+chair\b", "context_flag", _SUGGESTION, "chair"),
    ("shook_head_slowly", r"\bshook\s+(?:his|her|their|my)\s+head\s+slowly\b", "context_flag", _SUGGESTION, "shook"),
    ("crossed_arms", r"\bcrossed\s+(?:his|her|their|my)\s+arms\b", "context_flag", _SUGGESTION, "crossed"),
    ("leaned_against_wall", r"\bleaned\s+against\s+the\s+wall\b", "context_flag", _SUGGESTION, "wall"),
    ("step_toward_back", r"\btook\s+a\s+step\s+(?:back|forward|toward)\b", "context_flag", _SUGGESTION, "step"),
    ("looked_at_each_other", r"\b(?:looked|stared)\s+at\s+(?:each\s+other|one\s+another)\b", "context_flag", _SUGGESTION, ("looked", "stared")),
    ("head_to_one_side", r"\bhead\s+(?:to|on)\s+one\s+side\b", "context_flag", _SUGGESTION, "side"),
    ("down_on_one_knee", r"\b(?:went|dropped)\s+(?:down\s+)?on\s+one\s+knee\b", "context_flag", _SUGGESTION, "knee"),
    ("said_in_low_voice", r"\bsaid\s+in\s+a\s+low\s+voice\b", "context_flag", _SUGGESTION, "voice"),
    ("after_a_moment", r"\bafter\s+a\s+(?:long\s+|few\s+)?moments?\b", "context_flag", _SUGGESTION, "moment"),
    ("turned_walked_away", r"\bturned\s+and\s+walked\s+away\b", "context_flag", _SUGGESTION, "walked"),
]
