from __future__ import annotations

from copy import deepcopy
from typing import Any

from backend.analyzers import run_all_analyzers
from backend.llm_clients import complete_chat, scrub_provider
from backend.models import AnalyzerResult, Derivation, RunRequest
from backend.profiles import load_profile
from backend.storage import save_run
from backend.text_utils import Utf16Index
from backend.validation import validate_passes, validate_profile, validate_text

REWRITE_MODES = {"rewrite", "deslop", "line_edit", "write_from_brief"}


def aggregate_score(results: list[AnalyzerResult], profile: dict[str, Any]) -> float:
    weights = profile.get("analyzer_weights", {}) or {}
    severity_weight = {"hard_fail": 5.0, "strong_flag": 3.0, "context_flag": 1.3, "taste_flag": 0.6}
    total = 0.0
    for result in results:
        analyzer_weight = float(weights.get(result.name, 1.0))
        if result.name == "slop_score":
            total += min(float(result.score), 30.0) * analyzer_weight
        for flag in result.flags:
            total += severity_weight.get(flag.severity, 1.0) * analyzer_weight
    return round(total, 3)


def derive(profile: dict[str, Any], request: RunRequest) -> Derivation:
    return Derivation(
        readerProfile=profile.get("reader_profile")
        or "A working writer or editor who wants prose that keeps meaning while losing generic AI cadence.",
        registerTarget=profile.get("register_target") or "direct, concrete prose with varied rhythm",
        sceneAnchor=profile.get("scene_anchor")
        or "A local writing tool session where the user is revising draft prose and comparing versions.",
        textureConstraint=", ".join(profile.get("prefer", [])[:4])
        or "specific nouns, physical action, concrete stakes, human agency",
        antiPattern=profile.get("anti_pattern")
        or "Use positive routing. Preserve facts and POV while replacing generic cadence with register-specific action.",  # noqa: E501
    )


def _analysis_to_dict(results: list[AnalyzerResult], text: str) -> list[dict[str, Any]]:
    index = Utf16Index(text)
    return [result.to_dict(text, utf16_index=index) for result in results]


def build_messages(
    *,
    text: str,
    mode: str,
    profile: dict[str, Any],
    derivation: Derivation,
    flags: list[dict[str, Any]] | None = None,
    preserve: list[str] | None = None,
) -> list[dict[str, str]]:
    preserve_items = preserve or profile.get("preserve", ["meaning", "facts", "POV"])
    mode_instruction = {
        "rewrite": "Rewrite the prose so it sounds human, specific, and register-faithful while preserving meaning.",
        "deslop": "Aggressively remove generic AI cadence while preserving facts, POV, plot order, and speaker intent.",
        "line_edit": "Make a lighter line edit. Keep as much wording as possible while fixing obvious formulaic patterns.",  # noqa: E501
        "write_from_brief": "Write new prose from the brief using the derivation fields.",
    }.get(mode, "Rewrite the prose.")

    flag_lines = ""
    for flag in (flags or [])[:20]:
        flag_lines += f"- {flag.get('analyzer')}:{flag.get('type')} [{flag.get('severity')}]: {flag.get('excerpt')}\n"

    voice = profile.get("voice_fingerprint", {}) or {}
    voice_line = ""
    if voice:
        voice_line = (
            "\nVoice fingerprint targets: "
            f"average sentence length {voice.get('sentence_length_target', 'unspecified')}; "
            f"sentence variation {voice.get('sentence_length_variation', 'unspecified')}; "
            f"average paragraph length {voice.get('paragraph_length_target', 'unspecified')}; "
            f"dialogue word ratio {voice.get('dialogue_word_ratio', 'unspecified')}."
        )

    system = f"""You are ThothPad's prose engine.

Use Stop-Slop-v2 positive routing. Do not use a banned-phrase list as your generation context.
Complete these derivation fields silently before writing:
- readerProfile: {derivation.readerProfile}
- registerTarget: {derivation.registerTarget}
- sceneAnchor: {derivation.sceneAnchor}
- textureConstraint: {derivation.textureConstraint}
- antiPattern: {derivation.antiPattern}

Preserve: {", ".join(preserve_items)}
Prefer: {", ".join(profile.get("prefer", []))}
Avoid by positive redirection: {", ".join(profile.get("avoid", []))}
{voice_line}

Return only the revised prose. Do not explain the edit.
"""
    user = f"{mode_instruction}\n\n"
    if flag_lines:
        user += "Repair these detected issues without mechanically mentioning them:\n" + flag_lines + "\n"
    user += "TEXT:\n" + text
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


def flatten_flags(results: list[AnalyzerResult]) -> list[dict[str, Any]]:
    flags: list[dict[str, Any]] = []
    for result in results:
        for flag in result.flags:
            item = flag.to_dict()
            item["analyzer"] = result.name
            flags.append(item)
    severity_order = {"hard_fail": 0, "strong_flag": 1, "taste_flag": 2, "context_flag": 3}
    flags.sort(key=lambda item: severity_order.get(item.get("severity", ""), 9))
    return flags


def run_pipeline(request: RunRequest) -> dict[str, Any]:
    if request.profile_snapshot is None:
        profile = load_profile(request.profile, request.overrides)
    else:
        profile = deepcopy(validate_profile(request.profile_snapshot))
        if profile.get("name") != request.profile:
            raise ValueError("profile_snapshot name must match profile")
        for key, value in (request.overrides or {}).items():
            if isinstance(value, dict) and isinstance(profile.get(key), dict):
                profile[key].update(value)
            else:
                profile[key] = value
    derivation = derive(profile, request)
    input_text = validate_text(request.text or "")

    before = run_all_analyzers(input_text, profile)
    score_before = aggregate_score(before, profile)
    output_text = input_text
    llm_errors: list[str] = []

    if request.mode in REWRITE_MODES:
        active_text = input_text
        active_analysis = before
        analyzed_text = input_text
        passes = validate_passes(request.passes or 1)
        for pass_idx in range(passes):
            flags = flatten_flags(active_analysis)
            response = complete_chat(
                build_messages(
                    text=active_text,
                    mode=request.mode,
                    profile=profile,
                    derivation=derivation,
                    flags=flags,
                    preserve=request.preserve,
                ),
                request.provider,
            )
            if response.error:
                llm_errors.append(response.error)
                break
            active_text = response.text
            if pass_idx + 1 < passes:
                active_analysis = run_all_analyzers(active_text, profile)
                analyzed_text = active_text
        output_text = active_text

    if output_text == input_text:
        after = before
    elif request.mode in REWRITE_MODES and analyzed_text == output_text:
        after = active_analysis
    else:
        after = run_all_analyzers(output_text, profile)
    score_after = aggregate_score(after, profile)

    report: dict[str, Any] = {
        "mode": request.mode,
        "profile": profile.get("name", request.profile),
        "derivation": derivation.to_dict(),
        "score_before": score_before,
        "score_after": score_after,
        "llm_errors": llm_errors,
        "analysis_before": _analysis_to_dict(before, input_text),
        "analysis_after": _analysis_to_dict(after, output_text),
        "output_text": output_text,
        "persisted": bool(request.persist),
    }
    if request.persist:
        saved = save_run(
            mode=request.mode,
            profile_name=profile.get("name", request.profile),
            input_text=input_text,
            output_text=output_text,
            report=report,
            derivation=derivation.to_dict(),
            run_config={
                "mode": request.mode,
                "profile": profile.get("name", request.profile),
                "provider": scrub_provider(request.provider),
                "passes": request.passes,
                "aggressiveness": request.aggressiveness,
            },
        )
        report.update(saved)
    return report


def compare_texts(
    before_text: str,
    after_text: str,
    profile_name: str = "creative-default",
    *,
    persist: bool = False,
) -> dict[str, Any]:
    validate_text(before_text)
    validate_text(after_text)
    profile = load_profile(profile_name)
    before = run_all_analyzers(before_text, profile)
    after = run_all_analyzers(after_text, profile)
    report = {
        "mode": "compare",
        "profile": profile.get("name", profile_name),
        "score_before": aggregate_score(before, profile),
        "score_after": aggregate_score(after, profile),
        "analysis_before": _analysis_to_dict(before, before_text),
        "analysis_after": _analysis_to_dict(after, after_text),
        "output_text": after_text,
        "persisted": bool(persist),
    }
    if persist:
        saved = save_run(
            mode="compare",
            profile_name=profile.get("name", profile_name),
            input_text=before_text,
            output_text=after_text,
            report=report,
            derivation={},
            run_config={"mode": "compare", "profile": profile.get("name", profile_name)},
        )
        report.update(saved)
    return report
