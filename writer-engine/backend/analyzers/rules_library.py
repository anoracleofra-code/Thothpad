from __future__ import annotations

from typing import Any

from backend.models import AnalyzerResult

from .pattern_helpers import regex_flags


class RulesLibraryAnalyzer:
    name = "rules_library"

    def analyze(self, text: str, profile: dict[str, Any] | None = None) -> AnalyzerResult:
        patterns = [
            ("therapy_speak", r"\b(?:holding space|showing up|do the work|inner child|safe space|healing journey|your truth|lived experience)\b", "context_flag", "Replace therapy-register phrasing with the character's specific action, thought, or consequence."),
            ("linkedin_voice", r"\b(?:humbled to|excited to announce|delighted to share|game changer|thought leader|unlock value|move the needle|drive impact)\b", "context_flag", "Use plain business or narrative language instead of public-performance phrasing."),
            ("fantasy_slop", r"\b(?:ancient prophecy|chosen one|arcane energy|eldritch|aether|sigil|shadow realm|forgotten magic|glowing runes)\b", "context_flag", "Use world-specific objects and terms instead of generic fantasy residue."),
            ("ya_over_emotion", r"\b(?:tears pricked|chest tightened|heart shattered|she felt seen|he saw her for who she was|everything changed)\b", "context_flag", "Let the scene carry the emotion through behavior and consequence."),
            ("ai_noir", r"\b(?:neon-soaked|rain-slicked|smoke-filled|city swallowed|cheap whiskey|the case had teeth)\b", "context_flag", "Make the noir detail specific to this place instead of leaning on genre wallpaper."),
            ("fake_profundity", r"\b(?:that was the truth|that was the choice|that was the wound|that was the hunger|that was the silence)\b", "context_flag", "End with an image, decision, or cost rather than an abstract reveal."),
            ("overexplained_interiority", r"\b(?:he realized that|she realized that|he understood then|she understood then|for the first time,? (?:he|she|they) understood)\b", "context_flag", "Show the changed understanding through the next action or line of dialogue."),
            ("generic_sensory_mush", r"\b(?:the scent of [^.!?]{1,40} filled the air|a wave of [^.!?]{1,40} washed over|the sound of [^.!?]{1,40} echoed)\b", "context_flag", "Use one exact sensory detail anchored to a source in the room."),
        ]
        return regex_flags(name=self.name, text=text, patterns=patterns)  # type: ignore[arg-type]
