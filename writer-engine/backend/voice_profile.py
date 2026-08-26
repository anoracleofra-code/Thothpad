from __future__ import annotations

import json
import re
from collections import Counter
from typing import Any

from backend import config
from backend.atomic_io import atomic_write_text
from backend.metrics import STOPWORDS, paragraph_endings, sentence_openings, text_statistics
from backend.text_utils import words
from backend.validation import validate_profile_name


def build_voice_profile(samples: list[str], name: str) -> dict[str, Any]:
    name = validate_profile_name(name)
    text = "\n\n".join(samples)
    toks = words(text)
    stats = text_statistics(text)
    common = [
        w for w, _ in Counter(toks).most_common(100)
        if len(w) > 3 and w not in STOPWORDS
    ]
    openings = [
        {"opening": opening, "count": count}
        for opening, count in sentence_openings(text, 2).most_common(20)
    ]
    endings = [
        {"ending": ending, "count": count}
        for ending, count in paragraph_endings(text, 3).most_common(20)
    ]
    dialogue_words = sum(len(words(match)) for match in re.findall(r'"[^"]+"|“[^”]+”', text))
    adverb_candidates = [token for token in toks if token.endswith("ly") and len(token) > 4]
    verb_candidates = [
        token for token in toks
        if token.endswith(("ed", "ing")) and len(token) > 4
    ]
    profile = {
        "name": name,
        "register_target": f"{name} voice profile",
        "default_mode": "rewrite",
        "preserve": ["meaning", "facts", "POV", "sequence of events"],
        "prefer": ["specific action", "concrete nouns", "register fidelity"],
        "avoid": ["generic AI cadence", "fake contrast", "overexplained emotion"],
        "hard_bans": [],
        "soft_flags": [],
        "filter_words": {
            "enabled": True,
            "ignore_dialogue": True,
            "severity": "taste_flag",
            "custom_severity": "hard_fail",
        },
        "voice_stats": {
            **stats,
            "common_terms": common[:30],
            "dialogue_marks": len(re.findall(r'["\u201c]', text)),
            "dialogue_word_ratio": round(dialogue_words / max(len(toks), 1), 4),
            "common_sentence_openings": openings,
            "common_paragraph_endings": endings,
            "common_adverbs": Counter(adverb_candidates).most_common(20),
            "common_inflected_verbs": Counter(verb_candidates).most_common(30),
        },
        "voice_fingerprint": {
            "sentence_length_target": stats["avg_sentence_length"],
            "sentence_length_variation": stats["sentence_length_cv"],
            "paragraph_length_target": stats["avg_paragraph_length"],
            "lexical_diversity": {
                "mattr_500": stats["mattr_500"],
                "mtld": stats["mtld"],
                "hdd_42": stats["hdd_42"],
            },
            "dialogue_word_ratio": round(dialogue_words / max(len(toks), 1), 4),
        },
        "cliche_categories": {
            "general": True,
            "genre": True,
            "self_help": True,
            "corporate": True,
            "wordiness": True,
            "redundancy": True,
        },
        "external_tools": {"proselint": False},
        "manuscript": {"use_spacy": False},
        "analyzer_weights": {"stylometry": 0.45, "cliches": 0.8, "filter_words": 1.0},
    }
    config.PROFILES_DIR.mkdir(parents=True, exist_ok=True)
    path = config.PROFILES_DIR / f"{name}.json"
    atomic_write_text(path, json.dumps(profile, indent=2, ensure_ascii=False))
    return {"profile": profile, "path": str(path)}
