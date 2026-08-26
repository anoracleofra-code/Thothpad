from .parts_of_speech import (
    DEFAULT_ADVERB_EXCEPTIONS as DEFAULT_EXCEPTIONS,
)
from .parts_of_speech import (
    PossibleAdverbAnalyzer,
    _spacy_model,
    _tagged_tokens,
    spacy_model_status,
)

__all__ = [
    "DEFAULT_EXCEPTIONS",
    "PossibleAdverbAnalyzer",
    "_spacy_model",
    "_tagged_tokens",
    "spacy_model_status",
]
