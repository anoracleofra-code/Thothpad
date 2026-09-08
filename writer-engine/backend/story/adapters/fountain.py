from __future__ import annotations

import re

from backend.story.adapters.base import ExtractedDocument, SourceAdapter, SourceCandidate
from backend.story.sources import ExtractedStructure

_SCENE = re.compile(r"(?m)^(?P<title>(?:INT\.|EXT\.|INT/EXT\.|I/E\.|EST\.)[^\r\n]*)\s*$", re.IGNORECASE)
_SECTION = re.compile(r"(?m)^(?P<marks>#{1,6})\s*(?P<title>[^\r\n#].*?)\s*$")


class FountainAdapter(SourceAdapter):
    name = "fountain"
    extensions = frozenset({".fountain"})

    def extract(self, candidate: SourceCandidate) -> ExtractedDocument:
        raw = candidate.path.read_bytes()
        if b"\x00" in raw[:8192]:
            raise ValueError("binary-looking Fountain source")
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            text = raw.decode("utf-8", errors="replace")
        matches: list[tuple[int, int, str, int]] = []
        for match in _SECTION.finditer(text):
            matches.append((match.start(), match.end(), match.group("title").strip(), len(match.group("marks"))))
        for match in _SCENE.finditer(text):
            matches.append((match.start(), match.end(), match.group("title").strip(), 2))
        matches.sort(key=lambda item: item[0])
        structures: list[ExtractedStructure] = []
        for index, (start, _line_end, title, level) in enumerate(matches):
            end = matches[index + 1][0] if index + 1 < len(matches) else len(text)
            structures.append(
                ExtractedStructure(kind="heading", title=title, start_offset=start, end_offset=end, level=level)
            )
        return ExtractedDocument(text=text, structures=structures, links=[], metadata={"format": "fountain"})
