from __future__ import annotations

import re
import unicodedata
from collections import defaultdict
from collections.abc import Iterable

from backend.story.project import StoryProject
from backend.story.store import StoryStore

_WINDOWS_RESERVED = {
    "con",
    "prn",
    "aux",
    "nul",
    *(f"com{value}" for value in range(1, 10)),
    *(f"lpt{value}" for value in range(1, 10)),
}
_CONTROL = re.compile(r"[\x00-\x1f]")


def analyze_relative_paths(paths: Iterable[str]) -> dict[str, object]:
    normalized_groups: dict[str, list[str]] = defaultdict(list)
    problems: list[dict[str, str]] = []
    count = 0
    for raw in paths:
        count += 1
        value = str(raw).replace("\\", "/")
        portable = unicodedata.normalize("NFC", value).casefold()
        normalized_groups[portable].append(value)
        if value.startswith("/") or ".." in value.split("/"):
            problems.append({"path": value, "kind": "unsafe_relative_path"})
        if _CONTROL.search(value):
            problems.append({"path": value, "kind": "control_character"})
        for segment in value.split("/"):
            stem = segment.rstrip(" .").split(".", 1)[0].casefold()
            if stem in _WINDOWS_RESERVED:
                problems.append({"path": value, "kind": "windows_reserved_name"})
            if segment.endswith((" ", ".")):
                problems.append({"path": value, "kind": "trailing_space_or_dot"})
            if len(segment) > 255:
                problems.append({"path": value, "kind": "segment_too_long"})
    collisions = [values for values in normalized_groups.values() if len(set(values)) > 1]
    for values in collisions:
        problems.append({"path": " | ".join(sorted(values)), "kind": "unicode_or_case_collision"})
    return {
        "path_count": count,
        "problems": problems[:200],
        "problem_count": len(problems),
        "portable": not problems,
        "normalization": "NFC+casefold",
        "source_files_mutated": False,
    }


def path_resilience_report(project: StoryProject, store: StoryStore) -> dict[str, object]:
    paths = [str(row["relative_path"]) for row in store.rows("SELECT relative_path FROM sources WHERE tombstoned=0")]
    report = analyze_relative_paths(paths)
    report["project_id"] = project.project_id
    return report
