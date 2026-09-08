from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from typing import Any

from backend.story.project import StoryProject
from backend.story.store import StoryStore

_WORD = re.compile(r"[\w'-]+", re.UNICODE)
_QUESTION = re.compile(r"\?")
_DIALOGUE = re.compile(r'["“][^"”]{2,}["”]')

_TENSION = frozenset(
    {
        "afraid",
        "alarm",
        "blood",
        "danger",
        "deadline",
        "fear",
        "gun",
        "knife",
        "panic",
        "risk",
        "threat",
        "trapped",
        "urgent",
        "wound",
    }
)
_ACTION = frozenset(
    {
        "attack",
        "broke",
        "chased",
        "climbed",
        "dashed",
        "drew",
        "fired",
        "fled",
        "grabbed",
        "hit",
        "jumped",
        "kicked",
        "ran",
        "rushed",
        "shot",
        "struck",
        "threw",
    }
)
_REFLECTION = frozenset(
    {
        "believed",
        "considered",
        "felt",
        "imagined",
        "knew",
        "remembered",
        "realized",
        "recalled",
        "thought",
        "understood",
        "wondered",
    }
)
_MYSTERY = frozenset(
    {
        "clue",
        "hidden",
        "missing",
        "mystery",
        "secret",
        "strange",
        "unknown",
        "unexplained",
        "why",
        "who",
    }
)
_WONDER = frozenset(
    {
        "ancient",
        "astonished",
        "beautiful",
        "endless",
        "impossible",
        "marvel",
        "vast",
        "wonder",
        "wondrous",
    }
)
_RELIEF = frozenset(
    {
        "breathed",
        "calm",
        "eased",
        "laughed",
        "relief",
        "released",
        "safe",
        "smiled",
        "unclenched",
    }
)
_CONFLICT = frozenset(
    {
        "against",
        "argued",
        "blocked",
        "but",
        "fight",
        "forbade",
        "opposed",
        "refused",
        "resisted",
        "versus",
    }
)
_INTIMACY = frozenset(
    {
        "confessed",
        "embraced",
        "kissed",
        "love",
        "mother",
        "father",
        "sister",
        "brother",
        "trusted",
        "whispered",
    }
)
_DISTANT = frozenset({"years", "centuries", "history", "people", "kingdom", "nation", "war"})
_CLOSE = frozenset({"I", "me", "my", "mine", "we", "us", "our"})


@dataclass(frozen=True, slots=True)
class Dimension:
    name: str
    label: str
    rationale: str


def _unit_text(store: StoryStore, unit: dict[str, Any], *, maximum_chars: int = 80_000) -> str:
    source_id = str(unit.get("source_id") or "")
    if not source_id:
        return ""
    start = int(unit.get("start_offset") or 0)
    end = int(unit.get("end_offset") or 0)
    pieces: list[str] = []
    for row in store.rows(
        """
        SELECT start_offset,end_offset,text FROM source_chunks
        WHERE source_id=? AND end_offset>? AND start_offset<? ORDER BY ordinal
        """,
        (source_id, start, end),
    ):
        local_start = max(start, int(row["start_offset"])) - int(row["start_offset"])
        local_end = min(end, int(row["end_offset"])) - int(row["start_offset"])
        pieces.append(str(row["text"])[local_start:local_end])
        if sum(len(piece) for piece in pieces) >= maximum_chars:
            break
    return "".join(pieces)[:maximum_chars]


def _qualitative(count: int, *, low: str = "quiet", middle: str = "present", high: str = "elevated") -> str:
    if count <= 0:
        return low
    if count <= 2:
        return middle
    return high


def _matches(words: list[str], lexicon: frozenset[str]) -> list[str]:
    found: list[str] = []
    seen: set[str] = set()
    for word in words:
        folded = word.casefold()
        if folded in lexicon and folded not in seen:
            found.append(word)
            seen.add(folded)
        if len(found) >= 6:
            break
    return found


def _lexical_dimension(name: str, words: list[str], lexicon: frozenset[str], noun: str) -> Dimension:
    cues = _matches(words, lexicon)
    label = _qualitative(len(cues))
    if cues:
        rationale = f"The bounded prose contains {noun} cues such as: {', '.join(cues)}."
    else:
        rationale = f"No strong {noun} cue from the transparent built-in vocabulary appears in this bounded unit."
    return Dimension(name, label, rationale)


def analyze_story_unit_experience(
    store: StoryStore,
    story_unit_id: str,
    *,
    branch_id: str = "mainline",
) -> dict[str, Any]:
    unit = next(
        iter(store.rows("SELECT * FROM story_units WHERE story_unit_id=? AND branch_id=?", (story_unit_id, branch_id))),
        None,
    )
    if unit is None:
        raise KeyError("story unit not found")
    unit_record = dict(unit)
    text = _unit_text(store, unit_record)
    words = _WORD.findall(text)

    dimensions = [
        _lexical_dimension("tension", words, _TENSION, "threat/tension"),
        _lexical_dimension("action", words, _ACTION, "physical-action"),
        _lexical_dimension("reflection", words, _REFLECTION, "reflective/mental"),
        _lexical_dimension("mystery", words, _MYSTERY, "mystery/uncertainty"),
        _lexical_dimension("wonder", words, _WONDER, "wonder/scale"),
        _lexical_dimension("relief", words, _RELIEF, "release/relief"),
        _lexical_dimension("conflict", words, _CONFLICT, "opposition/conflict"),
        _lexical_dimension("intimacy", words, _INTIMACY, "relational/intimacy"),
    ]

    question_count = len(_QUESTION.findall(text))
    mystery_count = len(_matches(words, _MYSTERY))
    curiosity_label = _qualitative(question_count + mystery_count)
    curiosity_rationale = (
        "Questions or uncertainty cues are present in the bounded prose."
        if question_count + mystery_count
        else "No explicit question mark or strong built-in uncertainty cue appears in this bounded unit."
    )
    dimensions.append(Dimension("curiosity", curiosity_label, curiosity_rationale))

    dialogue_present = bool(_DIALOGUE.search(text))
    close_pronouns = sum(word in _CLOSE for word in words)
    distant_cues = len(_matches(words, _DISTANT))
    if close_pronouns > 0:
        distance_label = "close"
        distance_rationale = "First-person pronouns create a close grammatical vantage in this bounded unit."
    elif dialogue_present and distant_cues == 0:
        distance_label = "near"
        distance_rationale = "Direct dialogue is present without strong summary-distance cues."
    elif distant_cues >= 2:
        distance_label = "distant"
        distance_rationale = "The bounded prose contains broad historical/collective summary cues."
    else:
        distance_label = "mixed"
        distance_rationale = "The transparent grammatical cues do not strongly favor close or distant narration."
    dimensions.append(Dimension("narrative_distance", distance_label, distance_rationale))

    store.connection.execute(
        "DELETE FROM reader_experience WHERE story_unit_id=? AND branch_id=?",
        (story_unit_id, branch_id),
    )
    for dimension in dimensions:
        experience_id = str(
            uuid.uuid5(
                uuid.NAMESPACE_URL,
                f"thothpad-reader-experience:{story_unit_id}:{branch_id}:{dimension.name}",
            )
        )
        store.connection.execute(
            """
            INSERT INTO reader_experience(
                experience_id,story_unit_id,dimension,label,rationale,confidence,branch_id
            ) VALUES(?,?,?,?,?,0.5,?)
            """,
            (
                experience_id,
                story_unit_id,
                dimension.name,
                dimension.label,
                dimension.rationale,
                branch_id,
            ),
        )
    store.commit()
    return {
        "story_unit_id": story_unit_id,
        "display_title": unit_record.get("display_title", ""),
        "dimensions": [
            {"dimension": dimension.name, "label": dimension.label, "rationale": dimension.rationale}
            for dimension in dimensions
        ],
        "qualitative_only": True,
        "numeric_scores_exposed": False,
        "interpretation_limit": (
            "These are transparent prose/state cues for reader-experience visualization, not quality scores or "
            "claims about how every reader will feel."
        ),
    }


def reader_experience_timeline(
    project: StoryProject,
    store: StoryStore,
    *,
    branch_id: str = "mainline",
    source_id: str | None = None,
    maximum_units: int = 500,
) -> dict[str, Any]:
    params: list[Any] = [branch_id]
    source_filter = ""
    if source_id:
        source_filter = " AND u.source_id=?"
        params.append(source_id)
    units = [
        dict(row)
        for row in store.rows(
            f"""
            SELECT u.*,s.relative_path FROM story_units u
            LEFT JOIN sources s ON s.source_id=u.source_id
            WHERE u.branch_id=? AND u.kind IN ('chapter','scene'){source_filter}
            """,  # noqa: S608 - source_filter is a fixed SQL fragment
            tuple(params),
        )
    ]
    manuscript_order = {path.casefold(): index for index, path in enumerate(project.active_manuscripts())}
    fallback = len(manuscript_order) + 1
    units.sort(
        key=lambda unit: (
            manuscript_order.get(str(unit.get("relative_path") or "").casefold(), fallback),
            str(unit.get("relative_path") or "").casefold(),
            int(unit.get("ordinal") or 0),
        )
    )
    timeline = [
        analyze_story_unit_experience(store, str(unit["story_unit_id"]), branch_id=branch_id)
        for unit in units[: max(1, min(int(maximum_units), 500))]
    ]
    return {
        "branch_id": branch_id,
        "timeline": timeline,
        "dimensions": [
            "tension",
            "curiosity",
            "intimacy",
            "action",
            "reflection",
            "mystery",
            "wonder",
            "relief",
            "conflict",
            "narrative_distance",
        ],
        "qualitative_only": True,
        "numeric_scores_exposed": False,
        "ordering": "writer_owned_manuscript_order_then_story_unit_ordinal",
    }
