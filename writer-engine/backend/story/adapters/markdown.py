from __future__ import annotations

import re

from backend.story.adapters.base import ExtractedDocument, SourceAdapter, SourceCandidate
from backend.story.sources import ExtractedStructure, LinkHint

_HEADING = re.compile(r"(?m)^(#{1,6})[ \t]+(.+?)[ \t]*#*[ \t]*$")
_WIKILINK = re.compile(r"\[\[([^\]|#]+)(?:#[^\]|]+)?(?:\|([^\]]+))?\]\]")
_MARKDOWN_LINK = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")


class MarkdownAdapter(SourceAdapter):
    name = "markdown"
    extensions = frozenset({".md", ".markdown"})

    def extract(self, candidate: SourceCandidate) -> ExtractedDocument:
        raw = candidate.path.read_bytes()
        if b"\x00" in raw[:8192]:
            raise ValueError("binary-looking markdown source")
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            text = raw.decode("utf-8", errors="replace")

        matches = list(_HEADING.finditer(text))
        structures: list[ExtractedStructure] = []
        for index, match in enumerate(matches):
            end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
            structures.append(
                ExtractedStructure(
                    kind="heading",
                    title=match.group(2).strip(),
                    start_offset=match.start(),
                    end_offset=end,
                    level=len(match.group(1)),
                )
            )

        links: list[LinkHint] = []
        for match in _WIKILINK.finditer(text):
            links.append(
                LinkHint(
                    target=match.group(1).strip(),
                    label=(match.group(2) or match.group(1)).strip(),
                    start_offset=match.start(),
                    end_offset=match.end(),
                    kind="wikilink",
                )
            )
        for match in _MARKDOWN_LINK.finditer(text):
            links.append(
                LinkHint(
                    target=match.group(2).strip(),
                    label=match.group(1).strip(),
                    start_offset=match.start(),
                    end_offset=match.end(),
                    kind="markdown_link",
                )
            )
        return ExtractedDocument(text=text, structures=structures, links=links, metadata={})
