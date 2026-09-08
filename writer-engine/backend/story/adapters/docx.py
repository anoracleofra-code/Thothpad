from __future__ import annotations

import re
import zipfile
from xml.etree import ElementTree

from backend.story.adapters.base import ExtractedDocument, SourceAdapter, SourceCandidate
from backend.story.sources import ExtractedStructure

_W_NS = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
_HEADING_STYLE = re.compile(r"heading\s*([1-6])", re.IGNORECASE)
MAX_DOCX_ENTRIES = 5_000
MAX_DOCUMENT_XML_BYTES = 32 * 1024 * 1024
MAX_COMPRESSION_RATIO = 200


class DocxAdapter(SourceAdapter):
    """Minimal dependency-free DOCX reader for text and Word heading styles."""

    name = "docx"
    extensions = frozenset({".docx"})

    def extract(self, candidate: SourceCandidate) -> ExtractedDocument:
        try:
            with zipfile.ZipFile(candidate.path) as archive:
                infos = archive.infolist()
                if len(infos) > MAX_DOCX_ENTRIES:
                    raise ValueError("DOCX archive contains too many entries")
                info = archive.getinfo("word/document.xml")
                if info.file_size > MAX_DOCUMENT_XML_BYTES:
                    raise ValueError("DOCX document XML exceeds the safe extraction limit")
                compressed = max(1, int(info.compress_size))
                if info.file_size / compressed > MAX_COMPRESSION_RATIO:
                    raise ValueError("DOCX document XML compression ratio exceeds the safe limit")
                xml = archive.read("word/document.xml")
        except (KeyError, OSError, zipfile.BadZipFile) as exc:
            raise ValueError("malformed DOCX source") from exc

        try:
            root = ElementTree.fromstring(xml)
        except ElementTree.ParseError as exc:
            raise ValueError("malformed DOCX document XML") from exc

        paragraphs: list[tuple[str, int | None]] = []
        for paragraph in root.iter(f"{_W_NS}p"):
            style_level: int | None = None
            properties = paragraph.find(f"{_W_NS}pPr")
            if properties is not None:
                style = properties.find(f"{_W_NS}pStyle")
                if style is not None:
                    value = style.get(f"{_W_NS}val") or ""
                    match = _HEADING_STYLE.search(value)
                    if match:
                        style_level = int(match.group(1))
            text = "".join(node.text or "" for node in paragraph.iter(f"{_W_NS}t"))
            if text or style_level:
                paragraphs.append((text, style_level))

        pieces: list[str] = []
        starts: list[int] = []
        cursor = 0
        for text, _level in paragraphs:
            starts.append(cursor)
            pieces.append(text)
            cursor += len(text) + 2
        full_text = "\n\n".join(pieces)

        heading_indices = [index for index, (_text, level) in enumerate(paragraphs) if level]
        structures: list[ExtractedStructure] = []
        for ordinal, paragraph_index in enumerate(heading_indices):
            text, level = paragraphs[paragraph_index]
            next_index = heading_indices[ordinal + 1] if ordinal + 1 < len(heading_indices) else len(paragraphs)
            start = starts[paragraph_index]
            end = starts[next_index] if next_index < len(starts) else len(full_text)
            structures.append(
                ExtractedStructure(
                    kind="heading",
                    title=text.strip(),
                    start_offset=start,
                    end_offset=end,
                    level=level or 1,
                )
            )
        return ExtractedDocument(text=full_text, structures=structures, links=[], metadata={})
