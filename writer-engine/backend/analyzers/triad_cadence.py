from __future__ import annotations

import re
from typing import Any

from backend.models import AnalyzerResult, Flag
from backend.text_utils import excerpt, sentences, words

from .dialogue import dialogue_spans

# --- structural exclusion helpers -------------------------------------------
#
# Published-novel audits showed the raw three-item-list regex and the
# three-short-sentence window both fire on non-rhetorical material:
# appendix rosters (ALL-CAPS name lines), ship/army/house lists, dialogue
# ping-pong between speakers, and clause fragments stitched together by
# commas across clause boundaries. The helpers below classify a candidate
# span structurally so those classes are excluded while genuine literary
# triads survive.

_BE_VERBS = {"is", "am", "are", "was", "were", "be", "been", "being"}
_HAVE_DO = {"has", "have", "had", "do", "does", "did", "done"}
_MODALS = {
    "will", "would", "shall", "should", "can", "could", "may", "might",
    "must", "ought",
}
_PRONOUNS = {"he", "she", "it", "they", "i", "we", "you", "who", "that", "this"}
_IRREGULAR_PAST = {
    "said", "came", "went", "got", "took", "saw", "ran", "rose", "fell",
    "felt", "held", "kept", "left", "made", "meant", "told", "knew",
    "thought", "stood", "turned", "drew", "drove", "ate", "gave", "grew",
    "heard", "lay", "lost", "met", "paid", "rode", "sang", "sat", "spoke",
    "threw", "wore", "won", "wrote", "began", "brought", "built", "bought",
    "caught", "chose", "dealt", "fled", "flew", "forgot", "froze", "hung",
    "led", "rang", "shook", "shot", "shut", "sank", "slept", "spun",
    "struck", "swam", "swung", "taught", "tore", "woke", "found", "read",
    "put", "beat", "became", "bit", "broke", "drank", "sent", "spent",
}
_TAG_VERBS = {
    "said", "asked", "replied", "shouted", "whispered", "muttered",
    "thought", "knew", "realized", "pondered", "wondered", "mused",
    "added", "cried", "called", "answered", "nodded", "shrugged",
    "paused", "smiled", "frowned", "laughed", "sighed", "snapped",
    "growled", "agreed", "admitted", "insisted", "offered", "noted",
}
# An item starting with one of these marks the "items" as clause fragments
# stitched across a clause boundary rather than a parallel triad. Includes
# relative pronouns ("who was lean, balding") and subordinators ("before
# snapping back"). For item1 this check is conditioned on the span truly
# beginning a clause: greedy leftmost matching can absorb a mid-clause
# connective into item1 while the real tricolon sits later in the span
# ("...nothing but the instant, how fear fled, and thought fled").
_DISCOURSE_STARTS = {
    "but", "however", "though", "although", "indeed", "after", "before",
    "instead", "rather", "whose", "which", "who", "whom", "where",
    "perhaps", "maybe", "until", "when", "while", "because", "since",
    "once", "whether", "unless", "if",
}
_DISCOURSE_EXACT = {"then", "perhaps", "maybe", "indeed"}
# A span whose first item starts with one of these while its third item
# resumes with a capitalized subject + finite verb is a broken
# prepositional-phrase span ("in that long, miserable evening, Lin had
# reflected"), not a triad.
_PP_STARTS = {
    "the", "a", "an", "this", "that", "these", "those", "one", "in", "at",
    "on", "of", "for", "with", "by", "from", "during", "after", "before",
    "under", "over", "near", "past", "till",
}
_SUBJECT_PRONOUNS = {"he", "she", "it", "they", "i", "we", "you"}
_CLAUSE_START_PUNCT = set(".!?;:…—–)]}\u201d\u2019\"'")
_POSSESSIVES = {"my", "your", "his", "her", "its", "our", "their"}
_BARE_OBJECT_PRONOUNS = {"him", "her", "them", "me", "us"}
_ABBREVIATION_PERIOD = re.compile(r"\b(?:Mr|Mrs|Ms|Dr|St|Mt|Sr|Jr)\.")
# Items that are bare discourse adverbs (or two-word connective beats) mark
# comma-chained narration fragments rather than a parallel triad.
_ADVERB_ITEMS = {
    "too", "then", "now", "finally", "eventually", "surely", "perhaps",
    "maybe", "indeed", "again", "soon", "still", "yet", "there", "however",
    "instead", "besides", "meanwhile", "otherwise", "moreover", "why",
    "how", "what", "even",
}
# Bare filler/vocative address items ("..., hmmm, loving gesture",
# "..., boy, not trimming hedges").
_FILLER_ITEMS = {
    "hmm", "hmmm", "hm", "ah", "oh", "well", "aye", "yes", "no", "lad",
    "boy", "girl", "man", "sir", "child", "love", "friend", "brother",
    "sister",
}
_TAIL_PREPS = {
    "without", "with", "through", "against", "beneath", "beyond", "toward",
    "towards", "among", "amongst", "across", "around", "despite",
}
_STITCH_OPENERS = {
    "and then", "but then", "but now", "only then", "even then", "even now",
    "there too", "just then", "and now", "and yet",
    "there was", "there were", "there is", "there are",
}
# An item ending with one of these means the match runs into a continuing
# clause (dangling conjunction/preposition/relative pronoun).
_DANGLING_ENDS = {
    "and", "or", "but", "nor", "so", "yet", "with", "of", "to", "in",
    "on", "at", "by", "from", "as", "than", "which", "who", "that",
    "for", "into", "onto", "over", "under", "toward", "towards",
    "because", "while", "since", "though", "although", "behind", "past",
}
_NUMBER_WORDS = {
    "one", "two", "three", "four", "five", "six", "seven", "eight",
    "nine", "ten", "eleven", "twelve", "twenty", "hundred", "thousand",
}
_CAPS_TOKEN = re.compile(r"[A-Z][A-Z']{2,}")

def _item_tokens(item: str) -> list[str]:
    return re.findall(r"[A-Za-z']+", item)


_NAME_PARTICLES = {"the", "a", "an", "of", "and", "or", "at"}


def _is_proper_noun_like(item: str) -> bool:
    """Every alphabetic word capitalized (or ALL-CAPS); allows name particles."""
    tokens = _item_tokens(item)
    if not tokens:
        return False
    return all(
        token.lower() in _NAME_PARTICLES
        or token[0].isupper()
        or token.isupper()
        for token in tokens
    )

def _is_all_caps_item(item: str) -> bool:
    return bool(_CAPS_TOKEN.search(item))


_ATTRIBUTION_SUBJECT = r"(?:he|she|it|they|I|we|you|[A-Z][A-Za-z'\-]*)"
_ATTRIBUTION_VERBS = "|".join(
    f"{verb}|{verb.capitalize()}" for verb in sorted(_TAG_VERBS)
)
_ATTRIBUTION = re.compile(
    "^(?:"
    + _ATTRIBUTION_SUBJECT + r"\s+(?:" + _ATTRIBUTION_VERBS + ")"
    r"|(?:"
    + _ATTRIBUTION_VERBS + r")\s+(?:him|her|it|them|myself|[A-Z][A-Za-z'\-]*))"
    "$"
)

def _has_finite_verb(span: str) -> bool:
    for token in _item_tokens(span):
        low = token.lower()
        if low in _BE_VERBS or low in _HAVE_DO or low in _MODALS:
            return True
        if low in _IRREGULAR_PAST:
            return True
        if len(low) > 3 and low.endswith("ed") and low[-3] not in "aeiouy":
            return True
    return False


def _is_number_roster(items: list[str]) -> bool:
    starts = [item.strip().split(" ", 1)[0].lower() for item in items if item.strip()]
    return len(starts) == 3 and all(token in _NUMBER_WORDS for token in starts)

def _has_nonmodal_finite_verb(span: str) -> bool:
    """Like _has_finite_verb but ignores bare modals ("will", "must"): a
    vocative interrupt can sit before a modal clause ("..., Baruk, for Vorcan
    will surely take...")."""
    for token in _item_tokens(span):
        low = token.lower()
        if low in _MODALS:
            continue
        if low in _BE_VERBS or low in _HAVE_DO:
            return True
        if low in _IRREGULAR_PAST:
            return True
        if len(low) > 3 and low.endswith("ed") and low[-3] not in "aeiouy":
            return True
    return False


def _starts_clause(text: str, start: int) -> bool:
    """True when position `start` begins a clause (sentence start or after
    sentence/clause punctuation), not mid-clause narration."""
    before = text[:start].rstrip()
    if not before:
        return True
    return before[-1] in _CLAUSE_START_PUNCT


def _is_discourse_start(item: str) -> bool:
    s = item.strip()
    if not s:
        return False
    return (
        s.split(" ", 1)[0].lower() in _DISCOURSE_STARTS
        or s.lower() in _DISCOURSE_EXACT
    )


def _starts_verbish(token: str) -> bool:
    """True when a single token reads as a verb form (finite, participle)."""
    low = token.lower()
    if low in _BE_VERBS or low in _HAVE_DO or low in _MODALS:
        return True
    if low in _IRREGULAR_PAST:
        return True
    if len(low) > 3 and low.endswith("ed") and low[-3] not in "aeiouy":
        return True
    if len(low) > 4 and low.endswith("ing"):
        return True
    return False


def _in_parenthetical_aside(text: str, start: int, end: int) -> bool:
    """True when the span sits directly inside an em-dash or bracket pair —
    a technical aside ("—heat, elyctrostatic, potential, thaumaturgic
    emissions—"), not prose cadence."""
    openers = {"\u2014": "\u2014", "\u2013": "\u2013", "(": ")", "[": "]", "{": "}"}
    prev = text[start - 1] if start else ""
    closer = openers.get(prev)
    if not closer:
        return False
    window = text[end : end + 160]
    idx = window.find(closer)
    return idx != -1 and idx <= 120


def _classify_list_span(items: list[str], span: str, text: str, start: int) -> bool:
    """True when a regex three-item match should be EXCLUDED as non-rhetorical."""
    # The regex allows "\s+" between words: normalize newlines inside items so
    # first/last-word and dangling-end checks see them.
    stripped = [" ".join(item.split()) for item in items]
    first_words = [s.split(" ", 1)[0].lower() if s else "" for s in stripped]
    last_words = [s.rsplit(" ", 1)[-1].lower() if s else "" for s in stripped]
    item_tokens = [_item_tokens(s) for s in stripped]
    if _is_number_roster(items):
        return True
    if any(low in _DANGLING_ENDS for low in last_words):
        return True  # match runs into a continuing clause ("..., her ears and")
    if any(_ATTRIBUTION.match(s) for s in stripped):
        return True  # dialogue attribution interrupt ("she thought", "realized Isaac")
    if any(
        s.lower() in _FILLER_ITEMS or re.fullmatch(r"hm+|ah+|oh+", s.lower())
        for s in stripped
    ):
        return True  # filler/vocative address interrupt: "..., hmmm, loving gesture"
    if (
        len(item_tokens[1]) >= 2
        and len(first_words[1]) > 4
        and first_words[1].endswith("ing")
        and not _has_finite_verb(stripped[0])
        and not _starts_verbish(first_words[2])
        # a coordinated pair in item1 ("still and silent, agog, slack-jawed")
        # is an interrupted enumeration, not a clause stitch
        and " and " not in f" {stripped[0]} "
        and " or " not in f" {stripped[0]} "
    ):
        return True  # participial middle chain: "..., blinking rapidly, her eyes fouled"
    if (
        first_words[2] in _TAIL_PREPS
        and len(item_tokens[1]) <= 2
        and not _has_finite_verb(stripped[0])
        and not _has_finite_verb(stripped[1])
    ):
        return True  # adverbial pair + trailing PP elaboration: "out, swiftly, without hesitation"
    clause_start = _starts_clause(text, start)
    _finite_aux = _BE_VERBS | _HAVE_DO | _MODALS
    if (
        first_words[0] in _BE_VERBS
        and first_words[1] not in _BE_VERBS
        and first_words[2] not in _BE_VERBS
    ):
        return True  # match swallowed the clause verb into item1: "was cloth-of-gold, heavy, with the crowned"
    if (
        first_words[0] in _HAVE_DO
        and len(item_tokens[0]) >= 2
        and item_tokens[0][1].lower() in _BE_VERBS
    ):
        return True  # perfect-aux runover into item1: "had been replaced, Remade, with"
    if (
        first_words[1] == "and"
        and len(item_tokens[1]) >= 2
        and item_tokens[1][1].lower() in _PP_STARTS | _TAIL_PREPS | {"below", "above", "behind"}
    ):
        return True  # "and"-prep continuation stitch: "Derkhan shot, and below them, the poised marksman"
    # Discourse-word starts: unconditional for items 2/3 (they follow commas
    # inside the span, so a connective there really does begin a stitched
    # clause). For item1 only exclude when it truly begins a clause: greedy
    # leftmost matching can absorb a mid-clause "but"/"which" into item1 while
    # a genuine tricolon follows later in the same span.
    if any(_is_discourse_start(s) for s in stripped[1:]):
        return True
    if _is_discourse_start(stripped[0]) and clause_start:
        return True
    # Bare discourse-adverb items and two-word connective beats anywhere in
    # the span stitch narration fragments together ("There, too, the river
    # was...", "Only then, finally, with the victims...").
    if any(
        s.lower() in _ADVERB_ITEMS or s.lower() in _STITCH_OPENERS
        for s in stripped
    ):
        return True
    aux_runovers = sum(
        1 for toks in item_tokens
        if len(toks) >= 2 and toks[1].lower() in _finite_aux
    )
    if (
        len(item_tokens[2]) >= 2
        and item_tokens[2][1].lower() in _finite_aux
        and aux_runovers == 1
    ):
        return True  # list ran into the clause verb: "...the winged creatures became"
    if any(
        len(toks) >= 2
        and toks[0].lower() in _finite_aux | _IRREGULAR_PAST
        and toks[1][:1].isupper()
        for toks in item_tokens
    ):
        return True  # verb-first inversion stitch: "and burst balloons, stood Lin, lounging"
    number_tails = sum(
        1 for toks in item_tokens if toks and toks[-1].lower() in _NUMBER_WORDS
    )
    if number_tails == 1:
        return True  # lone appositive number interrupt: "Cley Cerwyn, fourteen, arrived";
                     # counting escalations ("sixteen, and twenty, and fifty") keep their flag
    if any(_is_all_caps_item(s) for s in stripped):
        return True  # appendix-style roster lines; ALL-CAPS token anywhere ("VARYS, a eunuch, called the SPIDER")
    proper_items = sum(1 for item in items if _is_proper_noun_like(item))
    if proper_items >= 2:
        return True  # name/ship/house/place rosters
    bare_pronouns = _BARE_OBJECT_PRONOUNS | _SUBJECT_PRONOUNS
    if any(s.lower() in bare_pronouns for s in stripped):
        return True  # clause stitch/resumption through a bare pronoun item
    if (
        len(item_tokens[1]) == 1
        and len(item_tokens[2]) >= 3
        and (_starts_verbish(first_words[2]) or first_words[2] in bare_pronouns)
        # keep pure action chains ("I came, I saw, I conquered the field"):
        # those have a verbish single-token item1 too
        and not (len(item_tokens[0]) == 1 and _starts_verbish(first_words[0]))
        # a coordinated pair in item1 ("still and silent, agog, slack-jawed")
        # is an interrupted enumeration, not a clause stitch
        and " and " not in f" {stripped[0]} "
        and " or " not in f" {stripped[0]} "
    ):
        return True  # vocative/participial interrupt: "there, child, smeared across the sky"
    if (
        first_words[2] in _SUBJECT_PRONOUNS
        # anaphora keeps its flag: "They ran, they hid, they survived." has
        # pronoun-opened items too; a resumption stitch does not
        and first_words[0] not in _SUBJECT_PRONOUNS
        and first_words[1] not in _SUBJECT_PRONOUNS
        and len(item_tokens[2]) >= 2
        and _starts_verbish(item_tokens[2][1].lower())
    ):
        return True  # clause resumption through pronoun subject: "for you, old son, you are implicated"
    if (
        _starts_verbish(first_words[2])
        and not _has_finite_verb(stripped[0])
        and not _has_finite_verb(stripped[1])
        and len(item_tokens[2]) >= 3
        and " and " not in f" {stripped[0]} "
        and " or " not in f" {stripped[0]} "
        and " and " not in f" {stripped[2]} "
        and " or " not in f" {stripped[2]} "
    ):
        return True  # subject apposition + verb resumption: "Those lights, those eyes, swivelled from the"
    if (
        first_words[0] in {"and", "or"}
        and item_tokens[0]
        and item_tokens[0][-1].lower() in _BARE_OBJECT_PRONOUNS
        and _starts_verbish(first_words[2])
    ):
        return True  # verb-chain stitch: "and feeds her, massages her tense, bruised shoulders"
    if (
        first_words[0] in _PP_STARTS
        and len(item_tokens[2]) >= 2
        and item_tokens[2][0][:1].isupper()
        and _starts_verbish(item_tokens[2][1].lower())
    ):
        return True  # broken PP span + clause resumption: "in that long, miserable evening, Lin had reflected"
    if (
        first_words[2] in {"the", "a", "an", "its", "his", "her", "their"}
        and len(item_tokens[2]) >= 3
        and _starts_verbish(item_tokens[2][2].lower())
        and not _has_finite_verb(stripped[0])
    ):
        return True  # PP + clause resumption: "the next hot, sticky day, the city sprawled"
    if (
        len(item_tokens[2]) >= 3
        and item_tokens[2][0][:1].isupper()
        and not _is_proper_noun_like(" ".join(item_tokens[2][:2]))
        and _starts_verbish(item_tokens[2][1].lower())
        and (_has_finite_verb(stripped[0]) or _has_finite_verb(stripped[1]))
    ):
        return True  # name + verb clause continuation: "released him, David rushed gratefully"
    possessive_count = sum(1 for first in first_words if first in _POSSESSIVES)
    if possessive_count == 1 and any(
        toks and toks[-1].lower().endswith("ing") for toks in item_tokens
    ):
        return True  # appositive body-detail stitch: "his gravelly voice, his ancient, lifeless eyes"
    if (
        len(items) == 3
        and first_words[2] in _BE_VERBS
        and first_words[0] not in _BE_VERBS
    ):
        return True  # match ran past the list into the clause verb: "roofs, past church-beacons, were"
    if proper_items >= 1 and not _has_nonmodal_finite_verb(span):
        # bare vocative / name drop set off by commas: "answer that question, Mistress, for"
        bare = [
            s for s in stripped
            if len(_item_tokens(s)) == 1 and _is_proper_noun_like(s)
        ]
        lowercase_neighbor = any(s[:1].islower() for s in stripped if s)
        if bare and lowercase_neighbor:
            return True
    if _in_parenthetical_aside(text, start, start + len(span)):
        return True  # technical aside bounded by dashes/brackets
    return False


def _is_front_matter(text: str, start: int, end: int) -> bool:
    """True when the span sits in publisher boilerplate (copyright page,
    jacket legal text) — not manuscript prose."""
    window = text[max(0, start - 300) : end + 300]
    return "Copyright \u00a9" in window or "All rights reserved" in window


def _straddles_dialogue(start: int, end: int, spans: list[tuple[int, int]]) -> bool:
    """True when the span overlaps quote material but is not fully inside one quote."""
    overlapped = False
    for lo, hi in spans:
        if hi <= start:
            continue
        if lo >= end:
            break
        if start >= lo and end <= hi:
            return False
        overlapped = True
    return overlapped


class TriadCadenceAnalyzer:
    name = "triad_cadence"

    def analyze(self, text: str, profile: dict[str, Any] | None = None) -> AnalyzerResult:
        flags: list[Flag] = []
        list_pattern = re.compile(
            r"\b([A-Za-z][A-Za-z'\-]{2,}(?:\s+[A-Za-z][A-Za-z'\-]{2,}){0,2}),\s+"
            r"([A-Za-z][A-Za-z'\-]{2,}(?:\s+[A-Za-z][A-Za-z'\-]{2,}){0,2}),\s+"
            r"(?:and\s+)?([A-Za-z][A-Za-z'\-]{2,}(?:\s+[A-Za-z][A-Za-z'\-]{2,}){0,2})\b"
        )
        quotes = dialogue_spans(text)
        for match in list_pattern.finditer(text):
            items = [match.group(1), match.group(2), match.group(3)]
            if _is_front_matter(text, match.start(), match.end()):
                continue  # publisher boilerplate, not manuscript prose
            if _classify_list_span(items, match.group(0), text, match.start()):
                continue
            if _straddles_dialogue(match.start(), match.end(), quotes):
                continue  # spans a quote boundary: ping-pong or quote+narration stitch
            flags.append(
                Flag(
                    type="three_item_list",
                    severity="context_flag",
                    start=match.start(),
                    end=match.end(),
                    excerpt=excerpt(text, match.start(), match.end()),
                    suggestion="Check whether this three-part cadence is doing real work or just adding AI rhythm.",
                    source="heuristic",
                )
            )

        sents = sentences(text)
        for i in range(len(sents) - 2):
            window = sents[i : i + 3]
            lengths = [len(words(s[2])) for s in window]
            if all(1 <= length <= 5 for length in lengths):
                start, end = window[0][0], window[-1][1]
                window_text = text[start:end]
                if _is_front_matter(text, start, end):
                    continue  # publisher boilerplate, not manuscript prose
                if _straddles_dialogue(start, end, quotes):
                    continue  # alternating speaker beats or quote/narration stitch
                if ".." in window_text or _ABBREVIATION_PERIOD.search(window_text):
                    continue  # sentence splitter artifacts (ellipsis, honorific periods)
                alpha_tokens = re.findall(r"[A-Za-z']+", window_text)
                if alpha_tokens:
                    caps = sum(1 for t in alpha_tokens if _CAPS_TOKEN.fullmatch(t))
                    if caps / len(alpha_tokens) >= 0.5:
                        continue  # appendix roster lines split into short rows
                flags.append(
                    Flag(
                        type="three_punchy_fragments",
                        severity="context_flag",
                        start=start,
                        end=end,
                        excerpt=excerpt(text, start, end),
                        suggestion="Break the stacked fragments. Use one sentence with a concrete action or image.",
                        source="heuristic",
                    )
                )
        return AnalyzerResult(name=self.name, score=float(len(flags)), flags=flags)
