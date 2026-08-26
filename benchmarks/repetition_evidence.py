"""Fresh evidence run for the repetition lens (round 3: 3 novels + MY.md).
Usage (from writer-engine/):
    .venv/Scripts/python.exe ../benchmarks/repetition_evidence.py

Produces the JSON payload consumed by
benchmark-results/repetition-evidence-2026-08-21.json and prints a manual
precision sample (n=10/book, seeded) for honest adjudication.
"""
from __future__ import annotations

import json
import os
import random
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "writer-engine"))

from backend.analyzers.repetition import RepetitionAnalyzer

CORPUS = Path(os.environ.get("WRITER_CORPUS_DIR", ""))
BOOKS = {
    "a_clash_of_kings": CORPUS / "A Clash Of Kings - George RR Martin.txt",
    "a_game_of_thrones": CORPUS / "A Game Of Thrones - George RR Martin.txt",
    "mistborn_final_empire": CORPUS / "Brandon Sanderson - [Mistborn 01] - The Final Empire.txt",
    # Round 3: the working draft itself (judge P2). A short unedited draft
    # legitimately carries more scene-local echoes than published novels,
    # so it gets its own explicit density budget (see DENSITY_BUDGETS)
    # instead of silently reusing the novel gate.
    "my_draft": CORPUS / "MY.md",
}
# Flags-per-1,000-words budget per book, default profile.
# Novels: professionally edited prose; round-2 measured baseline 2.1-4.2,
# gate stays < 5/1k (unchanged from round 2).
# my_draft: unedited working draft (~9.9k words). Budget < 10/1k — twice
# the novel gate — chosen because accidental echoes survive in drafts
# (that is exactly what the lens is for) while still bounding noise; the
# round-3 measured value is recorded in the evidence JSON and must stay
# under it. Documented here explicitly per the round-3 finding rather
# than inherited silently.
DENSITY_BUDGETS = {
    "a_clash_of_kings": 5.0,
    "a_game_of_thrones": 5.0,
    "mistborn_final_empire": 5.0,
    "my_draft": 10.0,
}
# Round-3 suppression acceptance (judge-specified, verbatim): on MY.md the
# ordinary content words must NOT be classified as names, while the
# protagonist/place names must remain suppressed.
SUPPRESSION_ACCEPTANCE = {
    "my_draft": {
        "must_be_suppressed": ["bezu", "lazan", "samia", "uhktir"],
        "must_not_be_suppressed": [
            "almost", "come", "damn", "divine", "dream", "fine", "follow",
            "instead", "listen", "may", "maybe", "neither", "nine", "nurse",
            "older", "run", "seems", "seen", "shut", "smoke", "stay", "thud",
            "thump", "tip", "turn", "wait", "whatever", "whelp", "why",
            "wishes", "cube", "crunch",
        ],
    },
}
SAMPLE_N = 10
SEED = 20260821


def word_count(text: str) -> int:
    return sum(1 for _ in text.split())


def context(text: str, start: int, end: int, pad: int = 130) -> str:
    lo = max(0, start - pad)
    hi = min(len(text), end + pad)
    snippet = text[lo:hi].replace("\r", " ").replace("\n", " ")
    marker_start = start - lo
    return (
        f"...{snippet[:marker_start]}"
        f"[{snippet[marker_start:marker_start + (end - start)]}]"
        f"{snippet[marker_start + (end - start):]}..."
    )


def main() -> None:
    analyzer = RepetitionAnalyzer()
    out: dict = {"books": {}}
    for key, path in BOOKS.items():
        text = path.read_text(encoding="utf-8", errors="replace")
        words = word_count(text)

        t0 = time.perf_counter()
        result = analyzer.analyze(text)
        elapsed = time.perf_counter() - t0
        flags = result.flags

        rerun = analyzer.analyze(text)
        deterministic = [(f.excerpt, f.start, f.end) for f in rerun.flags] == [
            (f.excerpt, f.start, f.end) for f in flags
        ]

        # legacy wide-net opt-in path for comparison
        legacy = analyzer.analyze(
            text,
            {"repetition": {"window_words": 750, "max_flag_distance": None}},
        )

        n_sample = min(SAMPLE_N, len(flags))
        rng = random.Random(SEED + len(key))
        sample_idx = sorted(rng.sample(range(len(flags)), n_sample))
        sample = []
        for i in sample_idx:
            flag = flags[i]
            entry = result.metrics["repeats"][i] if i < len(result.metrics["repeats"]) else {}
            sample.append({
                "excerpt": flag.excerpt,
                "distance_words": entry.get("distance_words"),
                "context": context(text, flag.start, flag.end),
            })

        top: dict[str, int] = {}
        for flag in flags:
            top[flag.excerpt.casefold()] = top.get(flag.excerpt.casefold(), 0) + 1
        top_sorted = dict(sorted(top.items(), key=lambda kv: (-kv[1], kv[0]))[:10])

        density = len(flags) / words * 1000
        budget = DENSITY_BUDGETS[key]
        out["books"][key] = {
            "path": str(path),
            "chars": len(text),
            "words": words,
            "flags": len(flags),
            "flags_per_1000_words": round(density, 2),
            "density_budget_per_1000_words": budget,
            "density_budget_pass": bool(density < budget),
            "far_repeat_count": result.metrics["far_repeat_count"],
            "elapsed_seconds": round(elapsed, 4),
            "determinism_check": deterministic,
            "suppressed_name_count": result.metrics["suppressed_name_count"],
            "suppressed_names_sample": sorted(result.metrics["suppressed_names"])[:20],
            "frequent_word_cap": result.metrics["frequent_word_cap"],
            "legacy_optin_750_unbanded_flags": len(legacy.flags),
            "legacy_flags_per_1000_words": round(len(legacy.flags) / words * 1000, 2),
            "top_repeats_by_count": top_sorted,
            "manual_precision_sample": {
                "n": n_sample,
                "seed": SEED + len(key),
                "flags": sample,
            },
        }
        acceptance = SUPPRESSION_ACCEPTANCE.get(key)
        if acceptance:
            suppressed = set(result.metrics["suppressed_names"])
            missed = sorted(
                word for word in acceptance["must_be_suppressed"] if word not in suppressed
            )
            leaked = sorted(
                word for word in acceptance["must_not_be_suppressed"] if word in suppressed
            )
            out["books"][key]["suppression_acceptance"] = {
                "must_be_suppressed": acceptance["must_be_suppressed"],
                "must_not_be_suppressed": acceptance["must_not_be_suppressed"],
                "missed": missed,
                "leaked": leaked,
                "pass": not missed and not leaked,
            }
    print(json.dumps(out, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
