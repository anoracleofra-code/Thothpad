from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol


class RetrievalSignal(Protocol):
    name: str

    def score(self, *, terms: list[str], candidate: Any) -> float: ...


@dataclass(slots=True)
class StaticRetrievalSignal:
    """Test/integration signal for externally computed local semantic scores."""

    scores: dict[str, float]
    name: str = "semantic"

    def score(self, *, terms: list[str], candidate: Any) -> float:
        del terms
        return float(self.scores.get(str(candidate["chunk_id"]), 0.0))


def bounded_signal_score(value: Any) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0.0
    return max(-4.0, min(4.0, number))


def retrieval_capabilities() -> dict[str, Any]:
    return {
        "default": "sqlite_fts5_or_bounded_like",
        "optional_signal_interface": True,
        "mandatory_vector_database": False,
        "semantic_similarity_is_authority": False,
        "maximum_optional_signals": 8,
        "maximum_signal_contribution": 4.0,
        "authority_scoring_is_independent": True,
    }
