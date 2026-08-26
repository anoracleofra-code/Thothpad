from __future__ import annotations

import importlib.util
import re
from dataclasses import dataclass
from functools import lru_cache
from typing import Any

from backend import config
from backend.models import AnalyzerResult, Flag
from backend.text_utils import active_document_features, excerpt

from .dialogue import dialogue_spans, inside_dialogue

WORD_RE = re.compile(r"\b[A-Za-z][A-Za-z'-]*ly\b")
TOKEN_RE = re.compile(r"\b[A-Za-z][A-Za-z'-]*\b")
WORDNET_DIR = config.BACKEND_DIR / "data" / "wordnet"
CONTEXT_CHUNK_CHARS = 50000
CONTEXT_OVERLAP_CHARS = 512
DEFAULT_ADVERB_EXCEPTIONS = {
    "ally", "apply", "ashley", "assembly", "belly", "beastly", "berkeley",
    "beverly", "billy", "bodily", "bristly", "brotherly", "bubbly",
    "burly", "butterfly", "carly", "comely", "comply", "costly", "courtly",
    "cowardly", "crinkly", "cuddly", "curly", "daily", "dastardly", "deadly",
    "disorderly", "dragonfly", "early", "earthly", "easterly", "elderly",
    "emily", "family", "fatherly", "fly", "friendly", "frilly", "ghastly",
    "ghostly", "gentlemanly", "gravelly", "grisly", "heavenly", "holly", "holy",
    "homely", "hourly", "imply", "italy", "jelly", "jolly", "july", "kimberly",
    "kindly", "kingly", "likely", "lily", "lively", "lonely", "lovely", "manly",
    "leisurely", "leslie", "lowly", "maidenly", "masterly", "mealy", "melancholy",
    "measly", "miserly", "molly", "monthly", "motherly", "multiply", "neighborly",
    "nightly", "oily", "only", "orderly", "pearly", "pebbly", "portly", "prickly",
    "quarterly", "rally", "rascally", "reply", "riley", "saintly", "scaly",
    "scholarly", "shapely", "shelly", "shirley", "sickly", "sicily", "silly",
    "sisterly", "slovenly", "sly", "smelly", "sparkly", "spindly", "sprightly",
    "squiggly", "stanley", "stately", "steely", "supply", "surly", "tally",
    "timely", "ugly", "unlikely", "unruly", "unsightly", "weekly", "westerly",
    "wifely", "wily", "wobbly", "womanly", "woolly", "worldly", "wrinkly", "yearly",
}
DETERMINERS = {
    "a", "an", "the", "this", "that", "these", "those", "my", "your", "his",
    "her", "its", "our", "their",
}
COMMON_NON_LY_ADVERBS = {
    "again", "almost", "already", "also", "always", "away", "ever", "here",
    "however", "instead", "just", "later", "maybe", "never", "now", "often",
    "perhaps", "quite", "rather", "really", "seldom", "sometimes", "soon",
    "still", "then", "there", "therefore", "today", "tomorrow", "too", "very",
    "yesterday", "yet",
}
POST_VERB_NON_LY_ADVERBS = {"far", "fast", "hard", "high", "late", "long", "low", "near", "right", "straight"}
PENN_ADVERB_TAGS = frozenset({"RB", "RBR", "RBS"})
PENN_ADJECTIVE_TAGS = frozenset({"JJ", "JJR", "JJS"})
PENN_VERB_TAGS = frozenset({"VB", "VBD", "VBG", "VBN", "VBP", "VBZ"})
PENN_PROPER_NOUN_TAGS = frozenset({"NNP", "NNPS"})
PENN_NOUN_TAGS = frozenset({"NN", "NNS", "NNP", "NNPS"})
AUXILIARY_FORMS = frozenset({
    "am", "is", "are", "was", "were", "be", "been", "being",
    "have", "has", "had", "having", "do", "does", "did", "doing",
    "can", "could", "may", "might", "must", "shall", "should", "will", "would",
})
HAVE_FORMS = frozenset({"have", "has", "had", "having"})
HAVE_OBJECT_TAGS = frozenset({"DT", "PRP$", "NN", "NNS", "NNP", "NNPS", "CD", "JJ"})
NOMINAL_GERUND_LEFT_TAGS = frozenset({"DT", "CD", "JJ", "JJS", "RBS", "PRP$"})
NOMINAL_GERUND_RIGHT_TAGS = frozenset({"EOS", "IN", "MD", "VBZ", "VBP", "VBD", "JJ", "JJR", "RB", "RBR", "WRB", "RP"})
RELATIONAL_ADJECTIVE_SUFFIXES = ("ic", "ive", "ory", "ish")


@dataclass(frozen=True, slots=True)
class TaggedToken:
    text: str
    lemma: str
    pos: str
    start: int
    end: int
    tag: str = ""


@lru_cache(maxsize=1)
def _spacy_model() -> tuple[Any | None, str]:
    try:
        import spacy

        model = spacy.load("en_core_web_sm", enable=["tok2vec", "tagger"])
        model.max_length = max(model.max_length, config.MAX_TEXT_CHARS + 100)
        return model, "en_core_web_sm"
    except (ImportError, OSError, ValueError):
        return None, "unavailable"


def spacy_model_status() -> dict[str, Any]:
    available = (
        importlib.util.find_spec("spacy") is not None
        and importlib.util.find_spec("en_core_web_sm") is not None
    )
    return {"available": available, "model": "en_core_web_sm" if available else None}


def _tagged_tokens(text: str) -> tuple[tuple[TaggedToken, ...], str]:
    features = active_document_features(text)
    if features is not None:
        return features.cached("pos_tagged_tokens", lambda: _build_tagged_tokens(text))
    return _build_tagged_tokens(text)


def _build_tagged_tokens(text: str) -> tuple[tuple[TaggedToken, ...], str]:
    model, model_name = _spacy_model()
    if model is None:
        return (), "unavailable"

    ranges = _context_chunk_ranges(text)
    contextual_ranges: list[tuple[int, int, int, int]] = []
    for emit_start, emit_end in ranges:
        context_start = max(0, emit_start - CONTEXT_OVERLAP_CHARS)
        while context_start > 0 and not text[context_start - 1].isspace():
            context_start -= 1
        context_end = min(len(text), emit_end + CONTEXT_OVERLAP_CHARS)
        while context_end < len(text) and not text[context_end].isspace():
            context_end += 1
        contextual_ranges.append((context_start, context_end, emit_start, emit_end))
    context_texts = [text[context_start:context_end] for context_start, context_end, _, _ in contextual_ranges]
    unique_contexts = tuple(dict.fromkeys(context_texts))
    documents_by_context = dict(zip(unique_contexts, model.pipe(unique_contexts, batch_size=8), strict=False))
    raw_tokens: list[tuple[str, int, int, str, str, str, bool]] = []
    lexical_prior_candidates: set[str] = set()
    for (context_start, _, emit_start, emit_end), context_text in zip(contextual_ranges, context_texts, strict=False):
        document = documents_by_context[context_text]
        alpha_tokens = [token for token in document if token.is_alpha]
        for index, token in enumerate(alpha_tokens):
            start = context_start + token.idx
            end = start + len(token.text)
            if start < emit_start or end > emit_end:
                continue
            previous_tag = alpha_tokens[index - 1].tag_ if index else "BOS"
            next_tag = alpha_tokens[index + 1].tag_ if index + 1 < len(alpha_tokens) else "EOS"
            raw_tokens.append(
                (
                    token.text,
                    start,
                    end,
                    token.tag_,
                    previous_tag,
                    next_tag,
                    index + 1 == len(alpha_tokens),
                )
            )
            if token.tag_ in PENN_PROPER_NOUN_TAGS and _known_surface_as(token.text, "adj"):
                lexical_prior_candidates.add(token.text.casefold())

    lexical_priors = _lowercase_lexical_priors(model, lexical_prior_candidates)
    tagged: list[TaggedToken] = []
    for word, start, end, tag, previous_tag, next_tag, is_last in raw_tokens:
        pos = _contextual_pos(
            word,
            tag,
            previous_tag,
            next_tag,
            is_last,
            lexical_priors.get(word.casefold(), ""),
        )
        if pos not in {"ADV", "ADJ", "VERB"}:
            continue
        tagged.append(
            TaggedToken(
                text=word,
                lemma=word,
                pos=pos,
                start=start,
                end=end,
                tag=tag,
            )
        )
    return tuple(tagged), model_name


def _lowercase_lexical_priors(model: Any, words: set[str]) -> dict[str, str]:
    if not words:
        return {}
    ordered = sorted(words)
    documents = model.pipe(ordered, batch_size=128)
    return {
        word: next((token.tag_ for token in document if token.is_alpha), "")
        for word, document in zip(ordered, documents, strict=False)
    }


@lru_cache(maxsize=65536)
def _contextual_pos(  # type: ignore[return]
    word: str,
    tag: str,
    previous_tag: str,
    next_tag: str,
    is_last: bool,
    lexical_prior: str,
) -> str:
    value = word.casefold()
    if tag == "WRB":
        return "ADV"
    if tag in PENN_ADVERB_TAGS:
        if value in {"not", "no", "course"} or (value == "best" and is_last):
            return "OTHER"
        return "ADV"
    if tag in PENN_ADJECTIVE_TAGS:
        return "OTHER" if value == "sorry" and is_last else "ADJ"
    if tag in PENN_PROPER_NOUN_TAGS:
        proper_noun_chain = previous_tag in PENN_PROPER_NOUN_TAGS
        if (
            lexical_prior in PENN_ADJECTIVE_TAGS
            and next_tag in PENN_NOUN_TAGS
            and (not proper_noun_chain or value.endswith(RELATIONAL_ADJECTIVE_SUFFIXES))
        ):
            return "ADJ"
        return "PROPN"
    if tag in PENN_VERB_TAGS:
        if value in AUXILIARY_FORMS:
            return "VERB" if value in HAVE_FORMS and next_tag in HAVE_OBJECT_TAGS else "AUX"
        if (
            tag == "VBG"
            and previous_tag in NOMINAL_GERUND_LEFT_TAGS
            and next_tag in (PENN_NOUN_TAGS | NOMINAL_GERUND_RIGHT_TAGS)
        ):
            # Nominal gerund: "her singing", "the weaving", "a gentle tapping".
            return "NOUN"
        if tag == "VBZ" and previous_tag == "BOS" and next_tag in {"EOS", "DT", "RP"}:
            return "NOUN"
        if not _known_surface_as(value, "verb") and _known_surface_as(value, "noun"):
            # Surface exists only as a noun in WordNet: a verb reading would be
            # a tagger error, not a writer habit worth flagging.
            return "NOUN"
        return "VERB"


def _context_chunk_ranges(text: str) -> tuple[tuple[int, int], ...]:
    """Split large input at prose boundaries without cutting words or offsets."""
    if len(text) <= CONTEXT_CHUNK_CHARS:
        return ((0, len(text)),)

    ranges: list[tuple[int, int]] = []
    start = 0
    while len(text) - start > CONTEXT_CHUNK_CHARS:
        limit = start + CONTEXT_CHUNK_CHARS
        search_start = start + CONTEXT_CHUNK_CHARS // 2
        window = text[search_start:limit]
        sentence_ends = list(re.finditer(r"[.!?][\"'\u201d\u2019)\]]*\s+", window))
        if sentence_ends:
            end = search_start + sentence_ends[-1].end()
        else:
            newline = text.rfind("\n", search_start, limit)
            whitespace = max(text.rfind(" ", search_start, limit), text.rfind("\t", search_start, limit))
            end = max(newline + 1 if newline >= 0 else -1, whitespace + 1 if whitespace >= 0 else -1)
        if end <= start:
            end = limit
            while end < len(text) and not text[end].isspace():
                end += 1
        ranges.append((start, end))
        start = end
    ranges.append((start, len(text)))
    return tuple(ranges)


@lru_cache(maxsize=3)
def _wordnet_words(part: str) -> frozenset[str]:
    path = WORDNET_DIR / f"index.{part}"
    if not path.is_file():
        return frozenset()
    words: set[str] = set()
    with path.open("r", encoding="utf-8", errors="replace") as stream:
        for line in stream:
            if not line or line.startswith("  "):
                continue
            word = line.split(" ", 1)[0].casefold()
            if word and "_" not in word:
                words.add(word)
    return frozenset(words)


@lru_cache(maxsize=3)
def _wordnet_exceptions(part: str) -> dict[str, frozenset[str]]:
    path = WORDNET_DIR / f"{part}.exc"
    if not path.is_file():
        return {}
    exceptions: dict[str, frozenset[str]] = {}
    with path.open("r", encoding="utf-8", errors="replace") as stream:
        for line in stream:
            values = [value.casefold() for value in line.split() if "_" not in value]
            if len(values) > 1:
                exceptions[values[0]] = frozenset(values[1:])
    return exceptions


def _known_as(token: TaggedToken, part: str) -> bool:
    if part == "adv" and token.tag == "WRB":
        return True
    words = _wordnet_words(part)
    if not words:
        return False
    if _known_surface_as(token.text, part):
        return True
    forms = {token.lemma.casefold()}
    if forms & words:
        return True
    exceptions = _wordnet_exceptions(part)
    return any(exceptions.get(form, frozenset()) & words for form in forms)


def _regular_base_forms(word: str, part: str) -> set[str]:
    forms = {word}
    if part == "verb":
        if word.endswith("ies") and len(word) > 3:
            forms.add(word[:-3] + "y")
        if word.endswith("es") and len(word) > 2:
            forms.update({word[:-2], word[:-1]})
        elif word.endswith("s") and len(word) > 1:
            forms.add(word[:-1])
        if word.endswith("ied") and len(word) > 3:
            forms.add(word[:-3] + "y")
        if word.endswith("ed") and len(word) > 2:
            stem = word[:-2]
            forms.update({stem, stem + "e"})
            if len(stem) > 1 and stem[-1] == stem[-2]:
                forms.add(stem[:-1])
        if word.endswith("ing") and len(word) > 3:
            stem = word[:-3]
            forms.update({stem, stem + "e"})
            if len(stem) > 1 and stem[-1] == stem[-2]:
                forms.add(stem[:-1])
    elif part == "noun":
        if word.endswith("ies") and len(word) > 3:
            forms.add(word[:-3] + "y")
        if word.endswith("es") and len(word) > 2:
            forms.update({word[:-2], word[:-1]})
        elif word.endswith("s") and len(word) > 1:
            forms.add(word[:-1])
    elif part == "adj":
        for suffix in ("est", "er"):
            if word.endswith(suffix) and len(word) > len(suffix):
                stem = word[: -len(suffix)]
                forms.update({stem, stem + "e"})
                if stem.endswith("i"):
                    forms.add(stem[:-1] + "y")
                if len(stem) > 1 and stem[-1] == stem[-2]:
                    forms.add(stem[:-1])
    return forms


@lru_cache(maxsize=16384)
def _known_surface_as(word: str, part: str) -> bool:
    value = word.casefold()
    words = _wordnet_words(part)
    if not words:
        return False
    exceptions = _wordnet_exceptions(part)
    return bool(_regular_base_forms(value, part) & words or exceptions.get(value, frozenset()) & words)


def _lexical_spans(text: str, part: str, exceptions: set[str]) -> list[tuple[int, int]]:
    return [
        match.span()
        for match in TOKEN_RE.finditer(text)
        if match.group(0).casefold() not in exceptions and _known_surface_as(match.group(0), part)
    ]


def _heuristic_adverb_spans(text: str, exceptions: set[str]) -> list[tuple[int, int]]:
    tokens = list(TOKEN_RE.finditer(text))
    token_indexes = {token.start(): index for index, token in enumerate(tokens)}
    spans: list[tuple[int, int]] = []
    seen: set[tuple[int, int]] = set()
    for match in WORD_RE.finditer(text):
        value = match.group(0).casefold()
        if value in exceptions:
            continue
        index = token_indexes.get(match.start(), -1)
        previous = tokens[index - 1].group(0).casefold() if index > 0 else ""
        if previous in DETERMINERS:
            continue
        spans.append(match.span())
        seen.add(match.span())
    for index, match in enumerate(tokens):
        value = match.group(0).casefold()
        if value in exceptions or match.span() in seen or not _known_surface_as(value, "adv"):
            continue
        previous = tokens[index - 1].group(0).casefold() if index > 0 else ""
        if value in COMMON_NON_LY_ADVERBS or (
            value in POST_VERB_NON_LY_ADVERBS
            and previous not in DETERMINERS
            and (_known_surface_as(previous, "verb") or previous in COMMON_NON_LY_ADVERBS)
        ):
            spans.append(match.span())
    spans.sort()
    return spans


class _LexicalPosAnalyzer:
    name = ""
    part = ""
    accepted_pos: frozenset[str] = frozenset()
    flag_type = ""
    suggestion = ""
    enabled_by_default = True
    # Lexical-only tier (live lane, no spaCy): surfaces WordNet also knows as
    # nouns get flagged regardless of usage ("the watch" in the verb lens).
    # Lenses set this to drop those dual-class surfaces from that tier only.
    lexical_suppress_noun_surfaces = False

    def _dual_class_noun_risk(self, surface: str) -> bool:
        """True when a lexical-tier verb flag is likely a noun usage.

        Without syntax, a surface WordNet knows as a noun is only trustworthy
        as a verb when it carries an inflected past marker (-ed): "gleamed",
        "checked". Base-form duals ("watch", "cut") and -ing forms ("the
        weaving") are suppressed; irregular pasts ("ran", "fell") survive.
        """
        value = surface.casefold()
        if not _known_surface_as(value, "noun"):
            return False
        return not value.endswith("ed")

    def analyze(self, text: str, profile: dict[str, Any] | None = None) -> AnalyzerResult:
        settings = (profile or {}).get(self.name, {}) or {}
        if settings.get("enabled", self.enabled_by_default) is False:
            return AnalyzerResult(name=self.name, score=0.0)
        exceptions = {
            str(value).casefold()
            for value in settings.get("exceptions", [])
            if str(value).strip()
        }
        excluded_dialogue = dialogue_spans(text) if settings.get("ignore_dialogue", False) else []
        lexical_data_available = bool(_wordnet_words(self.part))
        contextual = settings.get(
            "confirm_pos", not bool((profile or {}).get("_live_lexical_only", False))
        )
        tagged, model_name = _tagged_tokens(text) if contextual else ((), "deferred")
        selected = [
            token
            for token in tagged
            if token.pos in self.accepted_pos
            and token.text.casefold() not in exceptions
            and _known_as(token, self.part)
            and not inside_dialogue(token.start, token.end, excluded_dialogue)
        ]
        if model_name not in {"unavailable", "deferred"} and lexical_data_available:
            spans = [(token.start, token.end) for token in selected]
            tier = "pos+wordnet"
            source = "pos+wordnet"
            confidence = 0.95
        else:
            spans = _lexical_spans(text, self.part, exceptions)
            if self.lexical_suppress_noun_surfaces:
                spans = [
                    span
                    for span in spans
                    if not self._dual_class_noun_risk(text[span[0]:span[1]])
                ]
            tier = "lexical+wordnet" if lexical_data_available else "unavailable"
            source = tier
            confidence = 0.90
        flags = [
            Flag(
                type=self.flag_type,
                severity="taste_flag",
                start=start,
                end=end,
                excerpt=excerpt(text, start, end),
                suggestion=self.suggestion,
                confidence=confidence,
                source=source,
            )
            for start, end in spans
        ]
        return AnalyzerResult(
            name=self.name,
            score=float(len(flags)),
            flags=flags,
            metrics={
                "tier": tier,
                "engine": model_name,
                "lexicon": "Princeton WordNet 3.1" if lexical_data_available else "unavailable",
                "ignored_dialogue": bool(excluded_dialogue),
                "context_chunk_chars": CONTEXT_CHUNK_CHARS,
                "contextual_pos_chunked": len(text) > CONTEXT_CHUNK_CHARS,
                "contextual_pos_skipped_for_size": False,
                "total_findings": len(spans),
                "findings_truncated": False,
            },
        )


class PossibleAdjectiveAnalyzer(_LexicalPosAnalyzer):
    name = "possible_adjectives"
    part = "adj"
    accepted_pos = frozenset({"ADJ"})
    flag_type = "adjective"
    suggestion = "Check whether the adjective adds specific, necessary information or can be made more concrete."
    enabled_by_default = False


class PossibleVerbAnalyzer(_LexicalPosAnalyzer):
    name = "possible_verbs"
    part = "verb"
    # Auxiliary verbs are grammatical glue, not useful targets for a lexical
    # verb lens. Including them paints ordinary forms of be/have/do everywhere.
    accepted_pos = frozenset({"VERB"})
    flag_type = "verb"
    suggestion = "Check whether this verb is precise enough for the action or state being described."
    enabled_by_default = False
    lexical_suppress_noun_surfaces = True


class PossibleAdverbAnalyzer(_LexicalPosAnalyzer):
    name = "possible_adverbs"
    part = "adv"
    accepted_pos = frozenset({"ADV"})
    flag_type = "adverb"
    suggestion = "Check whether the adverb adds necessary precision or repeats what the verb already conveys."
    enabled_by_default = True

    def analyze(self, text: str, profile: dict[str, Any] | None = None) -> AnalyzerResult:
        settings = (profile or {}).get(self.name, {}) or {}
        if settings.get("enabled", True) is False:
            return AnalyzerResult(name=self.name, score=0.0)
        user_exceptions = {
            str(value).casefold()
            for value in settings.get("exceptions", [])
            if str(value).strip()
        }
        excluded_dialogue = dialogue_spans(text) if settings.get("ignore_dialogue", True) else []
        lexical_data_available = bool(_wordnet_words(self.part))
        contextual = settings.get(
            "confirm_pos", not bool((profile or {}).get("_live_lexical_only", False))
        )
        tagged, model_name = _tagged_tokens(text) if contextual else ((), "deferred")
        selected = [
            token
            for token in tagged
            if token.pos in self.accepted_pos
            and token.text.casefold() not in user_exceptions
            and _known_as(token, self.part)
            and not inside_dialogue(token.start, token.end, excluded_dialogue)
        ]
        if model_name not in {"unavailable", "deferred"} and lexical_data_available:
            spans = [(token.start, token.end) for token in selected]
            tier = "pos+wordnet"
            source = "pos+wordnet"
            confidence = 0.95
        else:
            spans = [
                span
                for span in _heuristic_adverb_spans(text, DEFAULT_ADVERB_EXCEPTIONS | user_exceptions)
                if _known_surface_as(text[span[0]:span[1]], self.part)
                and not inside_dialogue(*span, excluded_dialogue)
            ]
            tier = "heuristic+wordnet" if lexical_data_available else "heuristic"
            source = "heuristic+wordnet" if lexical_data_available else "heuristic"
            confidence = 0.95 if lexical_data_available else 0.85
        flags = [
            Flag(
                type="adverb" if tier == "pos+wordnet" else "possible_adverb",
                severity="taste_flag",
                start=start,
                end=end,
                excerpt=excerpt(text, start, end),
                suggestion=self.suggestion,
                confidence=confidence,
                source=source,
            )
            for start, end in spans
        ]
        return AnalyzerResult(
            name=self.name,
            score=float(len(flags)),
            flags=flags,
            metrics={
                "tier": tier,
                "engine": model_name,
                "lexicon": "Princeton WordNet 3.1" if lexical_data_available else "unavailable",
                "ignored_dialogue": bool(excluded_dialogue),
                "context_chunk_chars": CONTEXT_CHUNK_CHARS,
                "contextual_pos_chunked": len(text) > CONTEXT_CHUNK_CHARS,
                "contextual_pos_skipped_for_size": False,
                "total_findings": len(spans),
                "findings_truncated": False,
            },
        )
