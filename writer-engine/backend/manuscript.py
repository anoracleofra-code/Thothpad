from __future__ import annotations

import json
import math
import re
from collections import Counter, defaultdict
from datetime import UTC, datetime
from statistics import median
from typing import Any

from backend import config
from backend.analyzers import run_all_analyzers
from backend.atomic_io import atomic_write_text
from backend.metrics import (
    STOPWORDS,
    lemmatize_tokens,
    ngram_counts,
    paragraph_endings,
    repeated_ngrams,
    sentence_openings,
    text_statistics,
)
from backend.pipeline import aggregate_score
from backend.profiles import load_profile
from backend.projects import safe_project_name
from backend.storage import save_run
from backend.text_utils import document_features, words
from backend.validation import validate_documents

IMAGE_FAMILIES = {
    "breath": {"breath", "breathe", "breathing", "exhale", "inhale", "lungs"},
    "eyes_and_gaze": {"eye", "eyes", "gaze", "glance", "look", "looked", "stare", "stared"},
    "hands": {"hand", "hands", "finger", "fingers", "fist", "fists", "grip", "gripped"},
    "heart_and_chest": {"heart", "heartbeat", "chest", "ribs", "pulse"},
    "jaw_and_mouth": {"jaw", "mouth", "teeth", "lips", "tongue"},
    "light_and_shadow": {"light", "lights", "shadow", "shadows", "dark", "darkness", "glow"},
    "silence_and_sound": {"silence", "silent", "quiet", "sound", "echo", "echoed"},
    "temperature": {"cold", "chill", "heat", "hot", "warm", "warmth"},
}


def _normalize_documents(documents: list[dict[str, Any]]) -> list[dict[str, str]]:
    normalized: list[dict[str, str]] = []
    for index, document in enumerate(documents):
        text = str(document.get("text", ""))
        if not text.strip():
            continue
        name = str(document.get("name") or f"document-{index + 1}.md")
        normalized.append({"name": name, "text": text})
    return normalized


def _likely_proper_nouns(documents: list[dict[str, str]]) -> set[str]:
    capitalized: Counter[str] = Counter()
    total: Counter[str] = Counter()
    for document in documents:
        for match in re.finditer(r"\b[A-Za-z][A-Za-z'-]*\b", document["text"]):
            token = match.group(0)
            lowered = token.lower().strip("'")
            total[lowered] += 1
            if token[0].isupper() and match.start() > 0 and document["text"][match.start() - 1] not in ".!?\n":
                capitalized[lowered] += 1
    return {
        token
        for token, count in capitalized.items()
        if count >= 2 and count / max(total[token], 1) >= 0.6
    }


def _protected_terms(profile: dict[str, Any]) -> set[str]:
    values: set[str] = set()
    for key in ("protected_terms", "preserve", "proper_nouns"):
        for item in profile.get(key, []) or []:
            item_words = words(str(item))
            if len(item_words) == 1:
                values.add(item_words[0])
    return values


def _chapter_summary(
    document: dict[str, str],
    profile: dict[str, Any],
    grammar: dict[str, Any] | None = None,
) -> dict[str, Any]:
    text = document["text"]
    with document_features(text) as features:
        analysis = run_all_analyzers(text, profile)
        if grammar:
            from backend.grammar import analyze_grammar

            analysis.append(analyze_grammar(text, grammar))
        utf16_index = features.utf16_index
        flags = [
            flag.to_dict(text, utf16_index=utf16_index) | {"analyzer": result.name}
            for result in analysis
            for flag in result.flags
        ]
        flags.sort(key=lambda flag: (flag["start_utf16"], flag["end_utf16"]))
        stats = text_statistics(text)
        return {
            "name": document["name"],
            "score": aggregate_score(analysis, profile),
            "word_count": stats["word_count"],
            "stats": stats,
            "flags": flags,
            "analysis": [
                result.to_dict(text, utf16_index=utf16_index) for result in analysis
            ],
        }


def analyze_manuscript(
    documents: list[dict[str, Any]],
    profile_name: str = config.DEFAULT_PROFILE,
    *,
    overrides: dict[str, Any] | None = None,
    project: str | None = None,
    persist: bool = False,
    grammar: dict[str, Any] | None = None,
) -> dict[str, Any]:
    validate_documents(documents)
    docs = _normalize_documents(documents)
    if not docs:
        raise ValueError("At least one non-empty document is required.")

    profile = load_profile(profile_name, overrides)
    chapters = [_chapter_summary(document, profile, grammar) for document in docs]
    token_lists = [words(document["text"]) for document in docs]
    all_tokens = [token for values in token_lists for token in values]
    use_spacy = bool((profile.get("manuscript", {}) or {}).get("use_spacy", False))
    all_lemmas, lemma_engine = lemmatize_tokens(all_tokens, use_spacy=use_spacy)
    proper_nouns = _likely_proper_nouns(docs)
    excluded = STOPWORDS | proper_nouns | _protected_terms(profile)

    lemma_counts = Counter(
        lemma for lemma in all_lemmas if len(lemma) >= 4 and lemma not in excluded
    )
    lemma_doc_counts: dict[str, int] = defaultdict(int)
    cursor = 0
    for values in token_lists:
        chapter_lemmas = set(all_lemmas[cursor : cursor + len(values)])
        cursor += len(values)
        for lemma in chapter_lemmas:
            lemma_doc_counts[lemma] += 1

    minimum_word_count = max(4, math.ceil(len(all_tokens) / 2000) * 4)
    # Per-chapter occurrence vectors for the crutch-word curves: how many
    # times each repeated lemma lands in each document.
    lemma_chapter_counts: dict[str, list[int]] = defaultdict(
        lambda: [0] * len(docs)
    )
    cursor = 0
    for doc_index, values in enumerate(token_lists):
        for lemma in all_lemmas[cursor : cursor + len(values)]:
            if len(lemma) >= 4 and lemma not in excluded:
                lemma_chapter_counts[lemma][doc_index] += 1
        cursor += len(values)
    repeated_words = [
        {
            "lemma": lemma,
            "count": count,
            "per_1000_words": round(count / max(len(all_tokens), 1) * 1000, 3),
            "affected_files": lemma_doc_counts[lemma],
            "per_chapter": lemma_chapter_counts.get(lemma, []),
        }
        for lemma, count in lemma_counts.most_common()
        if count >= minimum_word_count
    ][:100]

    phrase_rows = repeated_ngrams(
        all_tokens,
        sizes=(3, 4, 5, 6),
        minimum=max(2, len(docs)),
        limit=120,
    )
    document_ngram_sets = [
        {
            size: set(ngram_counts(tokens, size))
            for size in (3, 4, 5, 6)
        }
        for tokens in token_lists
    ]
    phrase_file_counts: dict[str, int] = {}
    for row in phrase_rows:
        phrase_tokens = row["phrase"].split()
        phrase_file_counts[row["phrase"]] = sum(
            1
            for indexes in document_ngram_sets
            if row["phrase"] in indexes[len(phrase_tokens)]
        )
        row["affected_files"] = phrase_file_counts[row["phrase"]]
    repeated_phrases = [
        row for row in phrase_rows
        if row["count"] >= 3 or row["affected_files"] >= 2
    ][:80]

    openings: Counter[str] = Counter()
    endings: Counter[str] = Counter()
    for document in docs:
        openings.update(sentence_openings(document["text"], 2))
        endings.update(paragraph_endings(document["text"], 3))
    repeated_openings = [
        {"opening": value, "count": count}
        for value, count in openings.most_common()
        if count >= 3
    ][:40]
    repeated_endings = [
        {"ending": value, "count": count}
        for value, count in endings.most_common()
        if count >= 3 and not all(token in STOPWORDS for token in value.split())
    ][:30]

    # Chapter-opener audit: how each chapter BEGINS, across chapters —
    # position-aware, distinct from the sentence-level anaphora above.
    # A chapter whose first token repeats across 3+ chapters, or whose
    # opening bigram repeats across 2+ chapters, reads formulaic.
    first_words: Counter[str] = Counter()
    first_bigrams: Counter[str] = Counter()
    opener_samples: list[dict[str, Any]] = []
    for document, tokens in zip(docs, token_lists, strict=False):
        if not tokens:
            continue
        first_words[tokens[0]] += 1
        if len(tokens) >= 2:
            first_bigrams[f"{tokens[0]} {tokens[1]}"] += 1
        opener_samples.append(
            {
                "name": document["name"],
                "opener": " ".join(tokens[:8]),
            }
        )
    chapter_openers = {
        "repeated_first_words": [
            {"word": word, "count": count}
            for word, count in first_words.most_common()
            if count >= 3
        ][:20],
        "repeated_first_bigrams": [
            {"bigram": bigram, "count": count}
            for bigram, count in first_bigrams.most_common()
            if count >= 2
        ][:20],
        "openers": opener_samples[:100],
    }

    pattern_map: dict[tuple[str, str], dict[str, Any]] = {}
    for chapter in chapters:
        for flag in chapter["flags"]:
            key = (flag["analyzer"], flag["type"])
            item = pattern_map.setdefault(
                key,
                {
                    "analyzer": flag["analyzer"],
                    "type": flag["type"],
                    "total_matches": 0,
                    "files": set(),
                    "examples": [],
                },
            )
            item["total_matches"] += 1
            item["files"].add(chapter["name"])
            if len(item["examples"]) < 3:
                item["examples"].append(flag["excerpt"])
    pattern_hotspots = []
    for item in pattern_map.values():
        pattern_hotspots.append(
            {
                **item,
                "affected_files": len(item["files"]),
                "files": sorted(item["files"]),
            }
        )
    pattern_hotspots.sort(
        key=lambda item: (-item["affected_files"], -item["total_matches"], item["type"])
    )

    token_counter = Counter(all_tokens)
    image_families: list[dict[str, Any]] = []
    for family, vocabulary in IMAGE_FAMILIES.items():
        count = sum(token_counter[token] for token in vocabulary)
        affected = sum(1 for tokens in token_lists if set(tokens) & vocabulary)
        if count:
            image_families.append(
                {
                    "family": family,
                    "count": count,
                    "affected_files": affected,
                    "per_1000_words": round(count / max(len(all_tokens), 1) * 1000, 3),
                }
            )
    image_families.sort(key=lambda item: -item["count"])

    combined_text = "\n\n".join(
        f"# {document['name']}\n\n{document['text']}" for document in docs
    )
    manuscript_stats = (
        chapters[0]["stats"] if len(chapters) == 1
        else text_statistics(combined_text)
    )
    score = round(sum(chapter["score"] for chapter in chapters) / len(chapters), 3)
    report: dict[str, Any] = {
        "mode": "manuscript",
        "profile": profile.get("name", profile_name),
        "project": project,
        "document_count": len(docs),
        "score_before": score,
        "score_after": score,
        "output_text": "",
        "manuscript_stats": manuscript_stats,
        "chapters": chapters,
        "repetition": {
            "lemma_engine": lemma_engine,
            "minimum_word_count": minimum_word_count,
            "excluded_proper_nouns": sorted(proper_nouns),
            "chapter_names": [doc["name"] for doc in docs],
            "repeated_words": repeated_words,
            "repeated_phrases": repeated_phrases,
            "repeated_sentence_openings": repeated_openings,
            "repeated_paragraph_endings": repeated_endings,
            "chapter_openers": chapter_openers,
            "image_families": image_families,
        },
        "pattern_hotspots": pattern_hotspots[:80],
        "analysis_before": [],
        "analysis_after": [],
        "persisted": bool(persist),
    }
    if persist:
        saved = save_run(
            mode="manuscript",
            profile_name=profile.get("name", profile_name),
            input_text=combined_text,
            output_text="",
            report=report,
            derivation={},
            run_config={
                "mode": "manuscript",
                "profile": profile.get("name", profile_name),
                "project": project,
                "documents": [document["name"] for document in docs],
            },
        )
        report.update(saved)
        if project:
            _save_project_ledger(project, report)
    return report


def manuscript_report_with_timeline(report: dict[str, Any], project: str | None) -> dict[str, Any]:
    """Attach the project quality timeline to a manuscript report envelope."""
    if project and report.get("mode") == "manuscript":
        report["quality_timeline"] = read_project_timeline(project)
    comparison = genre_comparison_section(report, str(report.get("profile", "")))
    if comparison:
        report["genre_comparison"] = comparison
    return report


def genre_comparison_section(report: dict[str, Any], profile_name: str) -> dict[str, Any] | None:
    """Compare this run's lens densities against stored genre baselines."""
    baselines = load_lens_baselines(profile_name)
    if not baselines:
        return None
    word_count = report.get("manuscript_stats", {}).get("word_count") or 0
    if not word_count:
        return None
    current: dict[str, float] = {}
    for hotspot in report.get("pattern_hotspots", []):
        analyzer = str(hotspot.get("analyzer", ""))
        if analyzer in baselines and analyzer not in current:
            current[analyzer] = round(hotspot.get("total_matches", 0) * 1000 / word_count, 3)
    return {
        "calibration": profile_name,
        "baselines": baselines,
        "current_densities": current,
    }


def _save_project_ledger(project: str, report: dict[str, Any]) -> None:
    project_dir = config.PROJECTS_DIR / safe_project_name(project)
    if not project_dir.exists():
        return
    ledger_path = project_dir / "quality-ledger.json"
    try:
        ledger = json.loads(ledger_path.read_text(encoding="utf-8")) if ledger_path.exists() else {"runs": []}
    except json.JSONDecodeError:
        ledger = {"runs": []}
    ledger.setdefault("runs", []).append(
        {
            "run_id": report.get("run_id"),
            "created_at": datetime.now(UTC).isoformat(),
            "score": report.get("score_before"),
            "document_count": report.get("document_count"),
            "word_count": report.get("manuscript_stats", {}).get("word_count"),
            "category_counts": report.get("counts_by_analyzer") or {},
            "top_hotspots": report.get("pattern_hotspots", [])[:10],
        }
    )
    atomic_write_text(ledger_path, json.dumps(ledger, indent=2, ensure_ascii=False))


def read_project_timeline(project: str) -> dict[str, Any]:
    """Return the ordered quality-ledger runs recorded for a project."""
    project_dir = config.PROJECTS_DIR / safe_project_name(project)
    ledger_path = project_dir / "quality-ledger.json"
    if not ledger_path.is_file():
        return {"project": project, "runs": []}
    try:
        ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {"project": project, "runs": []}
    runs = ledger.get("runs")
    if not isinstance(runs, list):
        runs = []
    runs = [run for run in runs if isinstance(run, dict)]
    runs.sort(key=lambda run: str(run.get("created_at", "")))
    return {"project": project, "runs": runs}


def calibrate_corpus(
    samples: list[str],
    name: str,
    reference_samples: list[str] | None = None,
) -> dict[str, Any]:
    sample_tokens = [token for sample in samples for token in words(sample)]
    reference_tokens = [
        token for sample in (reference_samples or []) for token in words(sample)
    ]
    sample_counts = Counter(token for token in sample_tokens if token not in STOPWORDS and len(token) >= 4)
    reference_counts = Counter(
        token for token in reference_tokens if token not in STOPWORDS and len(token) >= 4
    )
    rows: list[dict[str, Any]] = []
    for token, count in sample_counts.items():
        if count < 2:
            continue
        sample_frequency = count / max(len(sample_tokens), 1)
        reference_frequency = reference_counts[token] / max(len(reference_tokens), 1)
        ratio = sample_frequency / max(reference_frequency, 1 / max(len(reference_tokens), 100000))
        if ratio <= 1.0:
            continue
        rows.append(
            {
                "word": token,
                "count": count,
                "sample_frequency": round(sample_frequency, 8),
                "reference_frequency": round(reference_frequency, 8),
                "overrepresentation_ratio": round(ratio, 3),
            }
        )
    rows.sort(key=lambda item: (-item["overrepresentation_ratio"], -item["count"], item["word"]))

    ngrams: dict[str, list[dict[str, Any]]] = {}
    for size in (2, 3, 4):
        sample_ng = ngram_counts(sample_tokens, size)
        reference_ng = ngram_counts(reference_tokens, size)
        values: list[dict[str, Any]] = []
        for phrase, count in sample_ng.items():
            if count < 2 or all(token in STOPWORDS for token in phrase.split()):
                continue
            sample_frequency = count / max(sum(sample_ng.values()), 1)
            reference_frequency = reference_ng[phrase] / max(sum(reference_ng.values()), 1)
            ratio = sample_frequency / max(
                reference_frequency, 1 / max(sum(reference_ng.values()), 100000)
            )
            if ratio <= 1.0:
                continue
            values.append(
                {
                    "phrase": phrase,
                    "count": count,
                    "overrepresentation_ratio": round(ratio, 3),
                }
            )
        values.sort(key=lambda item: (-item["overrepresentation_ratio"], -item["count"]))
        ngrams[str(size)] = values[:200]

    safe_name = re.sub(r"[^A-Za-z0-9_-]+", "-", name).strip("-") or "calibration"
    output_dir = config.PROFILES_DIR / "calibrations"
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / f"{safe_name}.json"
    result = {
        "name": safe_name,
        "sample_count": len(samples),
        "reference_sample_count": len(reference_samples or []),
        "sample_word_count": len(sample_tokens),
        "reference_word_count": len(reference_tokens),
        "top_overrepresented_words": rows[:300],
        "top_ngrams": ngrams,
        "lens_baselines": lens_density_baselines(samples),
        "path": str(path),
    }
    atomic_write_text(path, json.dumps(result, indent=2, ensure_ascii=False))
    return result


def lens_density_baselines(samples: list[str]) -> dict[str, float]:
    """Median flags-per-1000-words per analyzer across the given samples."""
    try:
        profile = load_profile()
    except Exception:
        profile = None
    per_analyzer_samples: dict[str, list[float]] = {}
    for sample in samples:
        word_total = max(len(words(sample)), 1)
        for analyzer in run_all_analyzers(sample, profile):
            density = len(analyzer.flags) * 1000 / word_total
            per_analyzer_samples.setdefault(analyzer.name, []).append(round(density, 3))
    return {
        analyzer: float(median(densities))
        for analyzer, densities in sorted(per_analyzer_samples.items())
    }


def load_lens_baselines(name: str) -> dict[str, float]:
    """Load stored genre baselines for a calibration name; empty when absent."""
    safe_name = re.sub(r"[^A-Za-z0-9_-]+", "-", name).strip("-") or "calibration"
    path = config.PROFILES_DIR / "calibrations" / f"{safe_name}.json"
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    baselines = data.get("lens_baselines")
    if not isinstance(baselines, dict):
        return {}
    return {str(k): float(v) for k, v in baselines.items()}
