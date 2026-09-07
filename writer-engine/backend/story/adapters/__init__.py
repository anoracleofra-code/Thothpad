from backend.story.adapters.base import SourceAdapter
from backend.story.adapters.docx import DocxAdapter
from backend.story.adapters.generic import GenericFolderAdapter
from backend.story.adapters.markdown import MarkdownAdapter
from backend.story.adapters.plaintext import PlainTextAdapter

__all__ = ["DocxAdapter", "GenericFolderAdapter", "MarkdownAdapter", "PlainTextAdapter", "SourceAdapter"]
