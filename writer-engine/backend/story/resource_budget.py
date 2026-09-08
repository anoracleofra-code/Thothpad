from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field

from backend.text_utils import cancellation_checkpoint


class StoryBudgetExceeded(RuntimeError):
    pass


@dataclass(slots=True)
class StoryResourceBudget:
    maximum_records: int = 1_000
    maximum_characters: int = 250_000
    timeout_ms: int = 5_000
    clock: Callable[[], float] = time.monotonic
    used_records: int = 0
    used_characters: int = 0
    _started: float = field(init=False)

    def __post_init__(self) -> None:
        self.maximum_records = max(1, min(int(self.maximum_records), 100_000))
        self.maximum_characters = max(1, min(int(self.maximum_characters), 10_000_000))
        self.timeout_ms = max(1, min(int(self.timeout_ms), 120_000))
        self._started = self.clock()

    def checkpoint(self, *, records: int = 0, characters: int = 0) -> None:
        cancellation_checkpoint()
        self.used_records += max(0, int(records))
        self.used_characters += max(0, int(characters))
        if self.used_records > self.maximum_records:
            raise StoryBudgetExceeded("Story Engine operation exceeded its record budget")
        if self.used_characters > self.maximum_characters:
            raise StoryBudgetExceeded("Story Engine operation exceeded its character budget")
        if (self.clock() - self._started) * 1000.0 > self.timeout_ms:
            raise StoryBudgetExceeded("Story Engine operation exceeded its time budget")

    def report(self) -> dict[str, int | bool]:
        return {
            "maximum_records": self.maximum_records,
            "maximum_characters": self.maximum_characters,
            "timeout_ms": self.timeout_ms,
            "used_records": self.used_records,
            "used_characters": self.used_characters,
            "bounded": True,
            "cancellation_aware": True,
        }


def story_resource_policy() -> dict[str, int | bool]:
    return StoryResourceBudget().report()
