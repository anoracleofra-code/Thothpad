"""Evaluate ThothPad POS lenses against a Universal Dependencies test split.

Usage:
    python benchmarks/evaluate_pos_ud.py path/to/en_ewt-ud-test.conllu

The corpus is an evaluation input only and is not bundled with ThothPad.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path

from backend.analyzers.parts_of_speech import (
    PossibleAdjectiveAnalyzer,
    PossibleAdverbAnalyzer,
    PossibleVerbAnalyzer,
)


@dataclass(frozen=True)
class Sentence:
    text: str
    gold: dict[tuple[int, int], str]


def sentences(path: Path) -> list[Sentence]:
    parsed: list[Sentence] = []
    text = ""
    tokens: list[tuple[str, str]] = []
    for line in path.read_text(encoding="utf-8").splitlines() + [""]:
        if line.startswith("# text = "):
            text = line[9:]
        elif line and not line.startswith("#"):
            columns = line.split("\t")
            if len(columns) >= 4 and "-" not in columns[0] and "." not in columns[0]:
                tokens.append((columns[1], columns[3]))
        elif not line and text and tokens:
            cursor = 0
            spans: dict[tuple[int, int], str] = {}
            for form, tag in tokens:
                start = text.find(form, cursor)
                if start < 0:
                    raise ValueError(f"could not align {form!r} in {text!r}")
                end = start + len(form)
                spans[(start, end)] = tag
                cursor = end
            parsed.append(Sentence(text, spans))
            text = ""
            tokens = []
    return parsed


def score(corpus: list[Sentence], analyzer, gold_tag: str) -> tuple[float, float, float, int, int, int]:
    true_positive = false_positive = false_negative = 0
    profile = {analyzer.name: {"enabled": True, "ignore_dialogue": False}}
    for sentence in corpus:
        predicted = {(flag.start, flag.end) for flag in analyzer.analyze(sentence.text, profile).flags}
        gold = {span for span, tag in sentence.gold.items() if tag == gold_tag and sentence.text[slice(*span)].isalpha()}
        true_positive += len(predicted & gold)
        false_positive += len(predicted - gold)
        false_negative += len(gold - predicted)
    precision = true_positive / max(1, true_positive + false_positive)
    recall = true_positive / max(1, true_positive + false_negative)
    f1 = 2 * precision * recall / max(precision + recall, 1e-12)
    return precision, recall, f1, true_positive, false_positive, false_negative


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("conllu", type=Path)
    arguments = parser.parse_args()
    corpus = sentences(arguments.conllu)
    for label, analyzer, tag in (
        ("ADV", PossibleAdverbAnalyzer(), "ADV"),
        ("ADJ", PossibleAdjectiveAnalyzer(), "ADJ"),
        ("VERB", PossibleVerbAnalyzer(), "VERB"),
    ):
        precision, recall, f1, tp, fp, fn = score(corpus, analyzer, tag)
        print(f"{label}: precision={precision:.4f} recall={recall:.4f} f1={f1:.4f} tp={tp} fp={fp} fn={fn}")


if __name__ == "__main__":
    main()
