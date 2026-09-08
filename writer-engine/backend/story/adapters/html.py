from __future__ import annotations

import html
from html.parser import HTMLParser

from backend.story.adapters.base import ExtractedDocument, SourceAdapter, SourceCandidate
from backend.story.sources import ExtractedStructure, LinkHint


class _StoryHTMLParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.structures: list[ExtractedStructure] = []
        self.links: list[LinkHint] = []
        self._heading_level = 0
        self._heading_start = 0
        self._heading_text: list[str] = []
        self._href: str | None = None
        self._link_start = 0
        self._link_text: list[str] = []

    @property
    def text(self) -> str:
        return "".join(self.parts)

    def _append_break(self) -> None:
        if self.parts and not self.text.endswith("\n"):
            self.parts.append("\n")

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        folded = tag.casefold()
        if folded in {"p", "div", "section", "article", "br", "li", "blockquote"}:
            self._append_break()
        if len(folded) == 2 and folded.startswith("h") and folded[1].isdigit():
            self._append_break()
            self._heading_level = int(folded[1])
            self._heading_start = len(self.text)
            self._heading_text = []
        if folded == "a":
            attrs_map = {name.casefold(): value for name, value in attrs}
            href = attrs_map.get("href")
            if href:
                self._href = href
                self._link_start = len(self.text)
                self._link_text = []

    def handle_endtag(self, tag: str) -> None:
        folded = tag.casefold()
        if self._heading_level and folded == f"h{self._heading_level}":
            title = " ".join("".join(self._heading_text).split())
            end = len(self.text)
            if title:
                self.structures.append(
                    ExtractedStructure(
                        kind="heading",
                        title=title,
                        start_offset=self._heading_start,
                        end_offset=end,
                        level=self._heading_level,
                    )
                )
            self._heading_level = 0
            self._heading_text = []
            self._append_break()
        if folded == "a" and self._href is not None:
            label = " ".join("".join(self._link_text).split())
            self.links.append(
                LinkHint(
                    target=self._href,
                    label=label or self._href,
                    start_offset=self._link_start,
                    end_offset=len(self.text),
                    kind="html_link",
                )
            )
            self._href = None
            self._link_text = []
        if folded in {"p", "div", "section", "article", "li", "blockquote"}:
            self._append_break()

    def handle_data(self, data: str) -> None:
        value = html.unescape(data)
        self.parts.append(value)
        if self._heading_level:
            self._heading_text.append(value)
        if self._href is not None:
            self._link_text.append(value)


class HtmlAdapter(SourceAdapter):
    name = "html"
    extensions = frozenset({".html", ".htm"})

    def extract(self, candidate: SourceCandidate) -> ExtractedDocument:
        raw = candidate.path.read_bytes()
        if b"\x00" in raw[:8192]:
            raise ValueError("binary-looking HTML source")
        try:
            source = raw.decode("utf-8")
        except UnicodeDecodeError:
            source = raw.decode("utf-8", errors="replace")
        parser = _StoryHTMLParser()
        parser.feed(source)
        parser.close()
        text = parser.text.strip("\n")
        structures = []
        for structure in parser.structures:
            start = min(structure.start_offset, len(text))
            end = min(max(start, structure.end_offset), len(text))
            structures.append(
                ExtractedStructure(
                    kind=structure.kind,
                    title=structure.title,
                    start_offset=start,
                    end_offset=end,
                    level=structure.level,
                )
            )
        return ExtractedDocument(text=text, structures=structures, links=parser.links, metadata={"format": "html"})
