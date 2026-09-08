from __future__ import annotations

from backend.story.adapters.base import ExtractedDocument, SourceAdapter, SourceCandidate


class PlainTextAdapter(SourceAdapter):
    name = "plaintext"
    extensions = frozenset({".txt", ".rst"})

    def extract(self, candidate: SourceCandidate) -> ExtractedDocument:
        raw = candidate.path.read_bytes()
        if b"\x00" in raw[:8192]:
            raise ValueError("binary-looking text source")
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            text = raw.decode("utf-8", errors="replace")
        return ExtractedDocument(text=text, structures=[], links=[], metadata={})
