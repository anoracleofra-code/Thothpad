from __future__ import annotations

import re

from backend.story.adapters.base import ExtractedDocument, SourceAdapter, SourceCandidate

_HEX = re.compile(r"\\'([0-9a-fA-F]{2})")
_CONTROL = re.compile(r"\\[a-zA-Z]+-?\d* ?")
_ESCAPED = re.compile(r"\\([{}\\])")


def _decode_rtf(raw: bytes) -> str:
    source = raw.decode("latin-1", errors="replace")
    source = _HEX.sub(lambda match: bytes([int(match.group(1), 16)]).decode("cp1252", errors="replace"), source)
    source = source.replace("\\par", "\n").replace("\\line", "\n").replace("\\tab", "\t")
    source = _ESCAPED.sub(lambda match: match.group(1), source)
    source = _CONTROL.sub("", source)
    source = source.replace("{", "").replace("}", "")
    lines = [line.rstrip() for line in source.splitlines()]
    return "\n".join(lines).strip()


class RtfAdapter(SourceAdapter):
    name = "rtf"
    extensions = frozenset({".rtf"})

    def extract(self, candidate: SourceCandidate) -> ExtractedDocument:
        raw = candidate.path.read_bytes()
        if not raw.lstrip().startswith(b"{\\rtf"):
            raise ValueError("invalid RTF source")
        return ExtractedDocument(text=_decode_rtf(raw), structures=[], links=[], metadata={"format": "rtf"})
