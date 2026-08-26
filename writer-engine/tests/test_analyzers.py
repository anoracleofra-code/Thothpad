from backend.analyzers import run_all_analyzers


def names_with_flags(text: str) -> set[str]:
    return {result.name for result in run_all_analyzers(text, {"name": "test"}) if result.flags}


def test_detects_common_ai_tells():
    text = (
        "It wasn't fear. It wasn't anger. It was recognition. "
        "The answer isn't speed. It's control. "
        "Her breath caught in her throat. The air was thick with silence. "
        "This matters because the stakes are high."
    )
    names = names_with_flags(text)
    assert "negative_listing" in names
    assert "binary_contrast" in names
    assert "body_cliches" in names
    assert "cinematic_fog" in names
    assert "vague_abstracts" in names


def test_clean_sample_avoids_hard_fail_noise():
    text = "Mara set the cup beside the register. Rain tapped the front window. She counted twelve dollars and locked the drawer."
    results = run_all_analyzers(text, {"name": "test"})
    hard = [flag for result in results for flag in result.flags if flag.severity == "hard_fail"]
    assert hard == []


def test_rules_library_detects_new_tell_families():
    text = "I am humbled to announce a neon-soaked story about holding space for the chosen one."
    names = names_with_flags(text)
    assert "rules_library" in names


def test_cliche_library_detects_common_idioms():
    text = "The clue was a dime a dozen, and the search became a wild goose chase."
    results = run_all_analyzers(text, {"name": "test"})
    cliches = next(result for result in results if result.name == "cliches")
    excerpts = {flag.excerpt.lower() for flag in cliches.flags}
    assert "dime a dozen" in excerpts
    assert "wild goose chase" in excerpts


def test_cliche_library_ignores_curly_quoted_dialogue_by_default():
    results = run_all_analyzers("\u201cLet us leverage synergies,\u201d Mara said.", {"name": "test"})
    cliches = next(result for result in results if result.name == "cliches")
    assert not any(flag.excerpt.lower() == "leverage synergies" for flag in cliches.flags)


def test_stylometry_reports_repeated_phrase_metrics():
    text = "Mara opened the door. " * 7
    results = run_all_analyzers(text, {"name": "test"})
    stylometry = next(result for result in results if result.name == "stylometry")
    assert stylometry.metrics["repeated_ngrams"]
    assert any(flag.type == "repeated_phrase" for flag in stylometry.flags)


def test_filter_words_are_categorized_and_ignore_dialogue():
    text = 'Mara noticed the clock and began to run. "I really thought you had gone," she said.'
    results = run_all_analyzers(text, {"name": "test"})
    filters = next(result for result in results if result.name == "filter_words")
    assert {flag.excerpt.lower() for flag in filters.flags} == {"noticed", "began to"}
    assert {flag.type for flag in filters.flags} == {"filter_perception", "filter_distancing"}


def test_profile_phrase_rules_are_applied():
    results = run_all_analyzers("Let that sink in.", {"hard_bans": ["Let that sink in."]})
    profile_patterns = next(result for result in results if result.name == "profile_patterns")
    assert len(profile_patterns.flags) == 1
    assert profile_patterns.flags[0].severity == "hard_fail"


def test_profile_phrase_rules_do_not_match_inside_larger_words():
    results = run_all_analyzers(
        "The article described partial success.",
        {"hard_bans": ["art"], "soft_flags": ["success"]},
    )
    profile_patterns = next(result for result in results if result.name == "profile_patterns")
    assert [flag.excerpt for flag in profile_patterns.flags] == ["success"]


def test_punctuated_profile_phrase_requires_an_outer_boundary():
    results = run_all_analyzers(
        "Let that sink in.Another sentence.",
        {"hard_bans": ["Let that sink in."]},
    )
    profile_patterns = next(result for result in results if result.name == "profile_patterns")
    assert profile_patterns.flags == []


def _triad_excerpts(text: str) -> set[str]:
    from backend.analyzers.triad_cadence import TriadCadenceAnalyzer

    result = TriadCadenceAnalyzer().analyze(text)
    return {flag.excerpt for flag in result.flags}


def test_triad_flags_genuine_literary_triads():
    excerpts = _triad_excerpts(
        "How time seemed to blur, how fear fled, and thought fled, and even your body."
    )
    assert any("fear fled" in excerpt for excerpt in excerpts)
    assert _triad_excerpts(
        "Jon wove a path between rocks and puddles, past great oaks, greygreen sentinels, and black-barked ironwoods."
    )


def test_triad_excludes_all_caps_appendix_rosters():
    text = "their cousin, SER STAFFORD LANNISTER, brother of Tywin."
    assert not any("SER STAFFORD" in excerpt for excerpt in _triad_excerpts(text))


def test_triad_excludes_proper_noun_rosters():
    assert not _triad_excerpts("Ser Gregor, Ser Amory, Ser Ilyn rode out.")
    assert not _triad_excerpts("Behind came Hardhand, Iron Wind, Grey Ghost.")
    assert not _triad_excerpts("He passed the Dragon Gate, the Lion Gate, and the Old Gate.")
    assert not _triad_excerpts("Isaac, Derkhan, Lemuel and Yagharek went down.")


def test_triad_excludes_dialogue_ping_pong():
    text = (
        "\u201cDo they truly eat frogs?\u201d he asked the old knight.\n"
        "\u201cAye,\u201d Ser Rodrik said.\n"
        "\u201cAnd eels?\u201d"
    )
    assert not _triad_excerpts(text)


def test_triad_excludes_attribution_interrupts():
    assert not _triad_excerpts("one strange boy, she thought, tucking the handkerchief away.")
    assert not _triad_excerpts("These troops, she pondered, must have been marching all night.")


def test_triad_excludes_clause_stitched_fragments():
    assert not _triad_excerpts("It was his brother, not Ned Stark, but you would never know it.")
    assert not _triad_excerpts("answer that question, Mistress, for once.")


def test_triad_keeps_common_noun_enumeration():
    assert _triad_excerpts("They had boiled eggs, stewed plums, and porridge for breakfast.")


def test_triad_flags_protected_tricolon_in_real_acok_sentence():
    # The actual ACOK battle-fever line: greedy leftmost matching absorbs the
    # mid-clause "but" into item1; a clause-start-only discourse exclusion
    # must keep the "how fear fled, and thought fled" tricolon flagged.
    text = (
        "How time seemed to blur\nand slow and even stop, how the past and "
        "the future vanished until there was nothing but the instant, how "
        "fear fled, and thought fled, and\neven your body."
    )
    assert any("how fear fled, and thought fled" in excerpt for excerpt in _triad_excerpts(text))

def test_triad_discourse_start_only_excludes_true_clause_starts():
    assert not _triad_excerpts("However tired, hungry, and cold he was, he marched on.")
    # Mid-clause absorption keeps the flag (tricolon follows inside the span).
    kept = _triad_excerpts("nothing but the instant, how fear fled, and thought fled")
    assert any("fear fled" in e for e in kept)
    # A connective that genuinely opens the clause still excludes (above).
    assert not _triad_excerpts("However tired, hungry, and cold, he marched on.")
    assert not _triad_excerpts("It was cloth-of-gold, heavy, with the crowned head.")
    assert not _triad_excerpts("He sent them all out, swiftly, without hesitation.")
    assert any(
        "Varys" in excerpt
        for excerpt in _triad_excerpts("\"Who cut you, Varys? When and why? Who are you, truly?\"")
    )
    assert not _triad_excerpts(
        "No part of this book may be reproduced, electronic, mechanical, including photocopying, "
        "recording, or by any information storage. Copyright \u00a9 1999 by Author. All rights reserved."
    )


def test_triad_excludes_vocative_and_participial_interrupts():
    assert not _triad_excerpts("That's blood up there, child, smeared across the sky.")
    assert not _triad_excerpts("Lin, touched, shook her head and said nothing.")
    assert not _triad_excerpts("'unfortunately for you, old son, you are implicated.'")
    assert not _triad_excerpts("her brother Jaime, unharmed, they shall remain safe.")




def test_triad_excludes_relative_clause_stitches():
    assert not _triad_excerpts("Ser Jared Frey, who was lean, balding, and pockmarked.")


def test_triad_excludes_connective_opener_stitches():
    assert not _triad_excerpts("And then, far far off, beyond the godswood, the wolf howled.")


def test_triad_excludes_broken_prepositional_phrase_spans():
    assert not _triad_excerpts("At one point in that long, miserable evening, Lin had reflected.")


def test_triad_excludes_subject_apposition_verb_resumption():
    assert not _triad_excerpts("Those lights, those eyes, swivelled from the corpse.")


def test_triad_excludes_verb_chain_stitches():
    assert not _triad_excerpts("she washes her and feeds her, massages her tense, bruised shoulders")


def test_triad_excludes_all_caps_token_inside_any_item():
    # Real GRRM appendix format: ALL-CAPS token buried mid-item, not a
    # standalone all-caps item.
    assert not _triad_excerpts("- VARYS, a eunuch, called the SPIDER, master of whisperers")


def test_triad_excludes_parenthetical_technical_aside():
    assert not _triad_excerpts(
 "detect various types of energy fields\u2014heat, elyctrostatic, potential, thaumaturgic emissions\u2014and represents them"
    )


def test_triad_flags_genuine_tricolon_fully_inside_one_quote():
    excerpts = _triad_excerpts("\u201cThe room was cold, narrow, and empty,\u201d she said.")
    assert any("cold" in excerpt for excerpt in excerpts)


def test_novel_cliche_pack_registered_and_loaded():
    from backend.analyzers.cliches import CATEGORIES, _load_rule_pack

    assert CATEGORIES.get("novel") == "novel-cliches.json"
    pack = _load_rule_pack()
    assert len(pack["novel"]) >= 15
    results = run_all_analyzers("The envoy raised an eyebrow and exchanged a glance with his aide.", {"name": "test"})
    cliches = next(result for result in results if result.name == "cliches")
    matched = {flag.excerpt.lower() for flag in cliches.flags}
    assert "raised an eyebrow" in matched
    assert any(flag.type == "novel_cliche" for flag in cliches.flags)
