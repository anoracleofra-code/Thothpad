from __future__ import annotations

from typing import Any

from backend.story.context import ContextCompiler, EpistemicMode
from backend.story.query import StoryQueryEngine


def _bounded_limit(value: Any, default: int = 100, maximum: int = 200) -> int:
    if isinstance(value, bool):
        return default
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return max(1, min(parsed, maximum))


def story_tool_manifest() -> list[dict[str, Any]]:
    """Read-only Story Engine capabilities safe to expose as R0 tools."""

    return [
        {"id": "get_project_understanding", "risk": "R0", "description": "Read normalized project/source counts."},
        {"id": "resolve_entity", "risk": "R0", "description": "Resolve a story entity by name or alias."},
        {"id": "get_entity", "risk": "R0", "description": "Read one entity, claims, aliases, and grounded mentions."},
        {"id": "find_story_evidence", "risk": "R0", "description": "Search indexed project evidence."},
        {"id": "query_claims", "risk": "R0", "description": "Read provenance-backed story claims."},
        {"id": "get_story_unit", "risk": "R0", "description": "Read a stable story unit."},
        {"id": "list_story_units", "risk": "R0", "description": "List normalized manuscript/chapter/scene units."},
        {"id": "list_entities", "risk": "R0", "description": "List normalized story entities and aliases."},
        {"id": "list_conflicts", "risk": "R0", "description": "List explicit story conflicts with grounded claims."},
        {
            "id": "resolve_story_unit",
            "risk": "R0",
            "description": "Resolve a project-relative source and native scope title to one stable story unit.",
        },
        {
            "id": "get_character_knowledge",
            "risk": "R0",
            "description": "Read tracked character knowledge/belief state.",
        },
        {
            "id": "get_character_beliefs",
            "risk": "R0",
            "description": "Read tracked beliefs and suspicions for a character.",
        },
        {"id": "get_reader_state", "risk": "R0", "description": "Read tracked reader information state."},
        {"id": "query_timeline", "risk": "R0", "description": "Read normalized timeline events for a branch."},
        {"id": "get_world_state", "risk": "R0", "description": "Read typed world state for a resolved entity."},
        {"id": "where_is_entity", "risk": "R0", "description": "Read tracked location state for an entity."},
        {"id": "who_has_object", "risk": "R0", "description": "Read tracked possession state for an object."},
        {"id": "list_threads", "risk": "R0", "description": "Read bounded active narrative threads with evidence."},
        {
            "id": "list_reader_questions",
            "risk": "R0",
            "description": "Read bounded tracked reader questions with evidence.",
        },
        {"id": "list_dramatic_promises", "risk": "R0", "description": "Read bounded dramatic promises with evidence."},
        {
            "id": "trace_causality",
            "risk": "R0",
            "description": "Trace bounded causal dependencies around a story record.",
        },
        {"id": "get_decision_history", "risk": "R0", "description": "Read bounded consequential character decisions."},
        {
            "id": "get_opposition_state",
            "risk": "R0",
            "description": "Read bounded opposition attached to story objectives.",
        },
        {
            "id": "get_scene_contract",
            "risk": "R0",
            "description": "Read the reviewed scene contract for one story unit.",
        },
        {
            "id": "get_author_decisions",
            "risk": "R0",
            "description": "Read writer-owned structural decisions and rationale.",
        },
        {
            "id": "audit_scene",
            "risk": "R0",
            "description": (
                "Compose a deterministic scene audit from tracked decisions, causality, opposition, "
                "contracts, and forward state."
            ),
        },
        {
            "id": "audit_chapter",
            "risk": "R0",
            "description": (
                "Compose a deterministic chapter audit from tracked decisions, causality, opposition, "
                "contracts, and forward state."
            ),
        },
        {"id": "list_branches", "risk": "R0", "description": "List bounded alternate branches and freshness state."},
        {
            "id": "compare_branch",
            "risk": "R0",
            "description": "Read one branch overlay diff, merge history, and freshness.",
        },
        {
            "id": "get_retcon_impact",
            "risk": "R0",
            "description": "Trace bounded registered downstream dependencies for a potential retcon.",
        },
        {
            "id": "cold_reader_at",
            "risk": "R0",
            "description": "Read only the evidence, reader state, questions, and promises available at a story cutoff.",
        },
        {
            "id": "audit_reveal_fairness",
            "risk": "R0",
            "description": (
                "Audit tracked setup evidence before a reveal without treating missing tracking as a prose defect."
            ),
        },
        {
            "id": "get_reader_expectations",
            "risk": "R0",
            "description": "Read tracked reader questions and dramatic promises that are open by a story cutoff.",
        },
        {
            "id": "get_dramatic_irony",
            "risk": "R0",
            "description": "Compare tracked reader access with one character's tracked knowledge at a story cutoff.",
        },
        {
            "id": "get_writer_model",
            "risk": "R0",
            "description": "Read confirmed and provisional writer preferences with bounded behavioral evidence.",
        },
        {
            "id": "explain_writer_preference",
            "risk": "R0",
            "description": "Explain one writer preference and the concrete local evidence supporting it.",
        },
        {
            "id": "run_editorial_council",
            "risk": "R0",
            "description": (
                "Run seven independent bounded read-only reviewers over one immutable Story State snapshot and "
                "return agreement/disagreement without agent chatter."
            ),
        },
        {"id": "list_story_lenses", "risk": "R0", "description": "List writer-defined reusable Story Lenses."},
        {
            "id": "get_story_lens",
            "risk": "R0",
            "description": "Read one Story Lens and its exact-source evidence findings.",
        },
        {
            "id": "run_story_lens",
            "risk": "R0",
            "description": (
                "Run deterministic lexical/entity retrieval for one Story Lens and return exact evidence candidates; "
                "retrieval itself never asserts the semantic lens is true."
            ),
        },
        {
            "id": "get_reader_experience",
            "risk": "R0",
            "description": "Read qualitative reader-experience cues for one story unit without numerical scores.",
        },
        {
            "id": "get_reader_experience_timeline",
            "risk": "R0",
            "description": (
                "Build a qualitative structural timeline for tension, curiosity, intimacy, action, reflection, "
                "mystery, wonder, relief, conflict, and narrative distance."
            ),
        },
        {
            "id": "explore_story",
            "risk": "R0",
            "description": "Explore bounded cross-state story nodes, evidence, and dependency edges.",
        },
        {
            "id": "get_scene_semantics",
            "risk": "R0",
            "description": (
                "Read exact scene presence plus explicitly tracked location, contract, decision, opposition, "
                "and claim state."
            ),
        },
        {
            "id": "audit_continuity",
            "risk": "R0",
            "description": (
                "Audit tracked conflicts, world-state ambiguity, and character knowledge-access gaps without "
                "inventing continuity facts."
            ),
        },
        {
            "id": "get_character_arc",
            "risk": "R0",
            "description": "Read the ordered tracked decision/knowledge/relationship changes for one character.",
        },
        {
            "id": "get_relationship_arc",
            "risk": "R0",
            "description": "Read explicit relationship-state transitions between two entities.",
        },
        {
            "id": "audit_ending_integrity",
            "risk": "R0",
            "description": (
                "Audit tracked open threads/promises and causal prerequisites at a selected ending without turning "
                "backpropagation into canon."
            ),
        },
        {
            "id": "get_project_health",
            "risk": "R0",
            "description": "Read engineering and coverage metrics without producing a universal story-quality score.",
        },
        {
            "id": "get_index_status",
            "risk": "R0",
            "description": "Read Story Engine cache/index integrity, FTS, stale evidence, and foreign-key status.",
        },
        {
            "id": "run_wow_acceptance",
            "risk": "R0",
            "description": "Run the ten-step Story Engine acceptance harness against the active project.",
        },
        {
            "id": "get_story_context",
            "risk": "R0",
            "description": "Compile inspectable task-specific story context.",
        },
    ]


def invoke_story_tool(
    tool_id: str,
    arguments: dict[str, Any],
    *,
    query: StoryQueryEngine,
    context: ContextCompiler,
) -> dict[str, Any]:
    if tool_id == "get_project_understanding":
        return query.get_project_understanding()
    if tool_id == "resolve_entity":
        return query.resolve_entity(str(arguments.get("name", "")))
    if tool_id == "get_entity":
        return query.get_entity(str(arguments.get("entity_id", "")))
    if tool_id == "find_story_evidence":
        return {
            "results": query.find_story_evidence(
                str(arguments.get("query", "")),
                limit=_bounded_limit(arguments.get("limit"), 16),
            )
        }
    if tool_id == "query_claims":
        return {
            "claims": query.query_claims(
                entity=str(arguments.get("entity")) if arguments.get("entity") else None,
                predicate=str(arguments.get("predicate")) if arguments.get("predicate") else None,
                branch_id=str(arguments.get("branch_id", "mainline")),
                include_noncanonical=bool(arguments.get("include_noncanonical", True)),
            )[: _bounded_limit(arguments.get("limit"), 100)]
        }
    if tool_id == "get_story_unit":
        return query.get_story_unit(str(arguments.get("story_unit_id", "")))
    if tool_id == "list_story_units":
        return {
            "story_units": query.list_story_units(
                source_id=str(arguments.get("source_id")) if arguments.get("source_id") else None,
                branch_id=str(arguments.get("branch_id", "mainline")),
            )[: _bounded_limit(arguments.get("limit"), 100)]
        }
    if tool_id == "list_entities":
        return {"entities": query.list_entities(limit=_bounded_limit(arguments.get("limit"), 200, 500))}
    if tool_id == "list_conflicts":
        return {
            "conflicts": query.list_conflicts(
                include_closed=bool(arguments.get("include_closed", False)),
                limit=_bounded_limit(arguments.get("limit"), 100, 500),
            )
        }
    if tool_id == "resolve_story_unit":
        return {
            "resolution": query.resolve_story_unit(
                source_path=str(arguments.get("source_path", "")),
                title=str(arguments.get("title", "")),
                branch_id=str(arguments.get("branch_id", "mainline")),
            )
        }
    if tool_id == "get_character_knowledge":
        return {
            "knowledge": query.character_knowledge(
                str(arguments.get("character", "")),
                branch_id=str(arguments.get("branch_id", "mainline")),
                limit=_bounded_limit(arguments.get("limit"), 100),
            )
        }
    if tool_id == "get_character_beliefs":
        return {
            "beliefs": query.character_beliefs(
                str(arguments.get("character", "")),
                branch_id=str(arguments.get("branch_id", "mainline")),
                limit=_bounded_limit(arguments.get("limit"), 100),
            )
        }
    if tool_id == "get_reader_state":
        return {
            "reader_state": query.reader_state(
                branch_id=str(arguments.get("branch_id", "mainline")),
                through_story_unit=(
                    str(arguments.get("through_story_unit")) if arguments.get("through_story_unit") else None
                ),
            )[: _bounded_limit(arguments.get("limit"), 100)]
        }
    if tool_id == "query_timeline":
        return {
            "events": query.timeline(
                branch_id=str(arguments.get("branch_id", "mainline")),
                limit=_bounded_limit(arguments.get("limit"), 100),
            )
        }
    if tool_id == "get_world_state":
        return {
            "world_state": query.world_state(
                str(arguments.get("entity", "")),
                state_type=str(arguments.get("state_type")) if arguments.get("state_type") else None,
                branch_id=str(arguments.get("branch_id", "mainline")),
                limit=_bounded_limit(arguments.get("limit"), 100),
            )
        }
    if tool_id == "where_is_entity":
        return {
            "locations": query.world_state(
                str(arguments.get("entity", "")),
                state_type="location",
                branch_id=str(arguments.get("branch_id", "mainline")),
                limit=_bounded_limit(arguments.get("limit"), 100),
            )
        }
    if tool_id == "who_has_object":
        return {
            "possessions": query.possession_state(
                str(arguments.get("object", "")),
                branch_id=str(arguments.get("branch_id", "mainline")),
                limit=_bounded_limit(arguments.get("limit"), 100),
            )
        }
    if tool_id == "list_threads":
        return {
            "threads": query.threads(
                branch_id=str(arguments.get("branch_id", "mainline")),
                limit=_bounded_limit(arguments.get("limit"), 100),
            )
        }
    if tool_id == "list_reader_questions":
        return {
            "reader_questions": query.reader_questions(
                branch_id=str(arguments.get("branch_id", "mainline")),
                limit=_bounded_limit(arguments.get("limit"), 100),
            )
        }
    if tool_id == "list_dramatic_promises":
        return {
            "dramatic_promises": query.dramatic_promises(
                branch_id=str(arguments.get("branch_id", "mainline")),
                limit=_bounded_limit(arguments.get("limit"), 100),
            )
        }
    if tool_id == "trace_causality":
        return {
            "causality": query.causality(
                record_kind=str(arguments.get("record_kind", "")),
                record_id=str(arguments.get("record_id", "")),
                branch_id=str(arguments.get("branch_id", "mainline")),
                direction=str(arguments.get("direction", "both")),
                maximum_depth=_bounded_limit(arguments.get("maximum_depth"), 8),
            )
        }
    if tool_id == "get_decision_history":
        return {
            "decisions": query.decision_history(
                character=str(arguments.get("character")) if arguments.get("character") else None,
                branch_id=str(arguments.get("branch_id", "mainline")),
                limit=_bounded_limit(arguments.get("limit"), 100),
            )
        }
    if tool_id == "get_opposition_state":
        return {
            "opposition": query.opposition_state(
                objective_id=str(arguments.get("objective_id")) if arguments.get("objective_id") else None,
                branch_id=str(arguments.get("branch_id", "mainline")),
                limit=_bounded_limit(arguments.get("limit"), 100),
            )
        }
    if tool_id == "get_scene_contract":
        return {"scene_contract": query.scene_contract(str(arguments.get("story_unit_id", "")))}
    if tool_id == "get_author_decisions":
        return {
            "author_decisions": query.author_decisions(
                story_unit_id=(str(arguments.get("story_unit_id")) if arguments.get("story_unit_id") else None),
                branch_id=str(arguments.get("branch_id", "mainline")),
                limit=_bounded_limit(arguments.get("limit"), 100),
            )
        }
    if tool_id == "audit_scene":
        return {
            "audit": query.audit_scene(
                str(arguments.get("story_unit_id", "")),
                branch_id=str(arguments.get("branch_id", "mainline")),
            )
        }
    if tool_id == "audit_chapter":
        return {
            "audit": query.audit_chapter(
                str(arguments.get("story_unit_id", "")),
                branch_id=str(arguments.get("branch_id", "mainline")),
            )
        }
    if tool_id == "list_branches":
        return {"branches": query.branches(limit=_bounded_limit(arguments.get("limit"), 100))}
    if tool_id == "compare_branch":
        return {"comparison": query.compare_branch(str(arguments.get("branch_id", "")))}
    if tool_id == "get_retcon_impact":
        return {
            "impact": query.retcon_impact(
                source_kind=str(arguments.get("source_kind", "")),
                source_id=str(arguments.get("source_id", "")),
                maximum_nodes=_bounded_limit(arguments.get("maximum_nodes"), 5_000, 5_000),
            )
        }
    if tool_id == "cold_reader_at":
        return {
            "cold_reader": query.cold_reader_at(
                str(arguments.get("story_unit_id", "")),
                prompt=str(arguments.get("prompt", ""))
                or "What can a reader know, wonder, and reasonably expect here?",
                branch_id=str(arguments.get("branch_id", "mainline")),
                maximum_chars=_bounded_limit(arguments.get("maximum_chars"), 40_000, 100_000),
            )
        }
    if tool_id == "audit_reveal_fairness":
        return {
            "reveal_fairness": query.reveal_fairness(
                str(arguments.get("story_unit_id", "")),
                claim_id=str(arguments.get("claim_id")) if arguments.get("claim_id") else None,
                branch_id=str(arguments.get("branch_id", "mainline")),
                limit=_bounded_limit(arguments.get("limit"), 100, 500),
            )
        }
    if tool_id == "get_reader_expectations":
        return {
            "reader_expectations": query.reader_expectations(
                str(arguments.get("story_unit_id", "")),
                branch_id=str(arguments.get("branch_id", "mainline")),
                limit=_bounded_limit(arguments.get("limit"), 100, 500),
            )
        }
    if tool_id == "get_dramatic_irony":
        return {
            "dramatic_irony": query.dramatic_irony(
                str(arguments.get("story_unit_id", "")),
                character=str(arguments.get("character", "")),
                branch_id=str(arguments.get("branch_id", "mainline")),
                limit=_bounded_limit(arguments.get("limit"), 100, 500),
            )
        }
    if tool_id == "get_writer_model":
        return query.writer_model(
            scope_kind=str(arguments.get("scope_kind")) if arguments.get("scope_kind") else None,
            scope_id=str(arguments.get("scope_id")) if arguments.get("scope_id") is not None else None,
            include_ignored=bool(arguments.get("include_ignored", False)),
            limit=_bounded_limit(arguments.get("limit"), 100, 500),
        )
    if tool_id == "explain_writer_preference":
        return {"preference": query.explain_writer_preference(str(arguments.get("preference_id", "")))}
    if tool_id == "run_editorial_council":
        return {
            "council": query.editorial_council(
                str(arguments.get("story_unit_id", "")),
                branch_id=str(arguments.get("branch_id", "mainline")),
            )
        }
    if tool_id == "list_story_lenses":
        return {"story_lenses": query.story_lenses(include_archived=bool(arguments.get("include_archived", False)))}
    if tool_id == "get_story_lens":
        return {"story_lens": query.story_lens(str(arguments.get("lens_id", "")))}
    if tool_id == "run_story_lens":
        return {
            "story_lens": query.run_story_lens(
                str(arguments.get("lens_id", "")),
                maximum_findings=_bounded_limit(arguments.get("maximum_findings"), 100, 500),
            )
        }
    if tool_id == "get_reader_experience":
        return {
            "reader_experience": query.reader_experience(
                str(arguments.get("story_unit_id", "")),
                branch_id=str(arguments.get("branch_id", "mainline")),
            )
        }
    if tool_id == "get_reader_experience_timeline":
        return {
            "reader_experience_timeline": query.reader_experience_timeline(
                branch_id=str(arguments.get("branch_id", "mainline")),
                source_id=str(arguments.get("source_id")) if arguments.get("source_id") else None,
                maximum_units=_bounded_limit(arguments.get("maximum_units"), 200, 500),
            )
        }
    if tool_id == "explore_story":
        return {
            "explorer": query.explore_story(
                str(arguments.get("query", "")),
                branch_id=str(arguments.get("branch_id", "mainline")),
                limit=_bounded_limit(arguments.get("limit"), 50, 100),
            )
        }
    if tool_id == "get_scene_semantics":
        return {
            "scene_semantics": query.scene_semantics(
                str(arguments.get("story_unit_id", "")),
                branch_id=str(arguments.get("branch_id", "mainline")),
            )
        }
    if tool_id == "audit_continuity":
        return {
            "continuity": query.continuity_audit(
                str(arguments.get("story_unit_id", "")),
                character=str(arguments.get("character", "")),
                branch_id=str(arguments.get("branch_id", "mainline")),
            )
        }
    if tool_id == "get_character_arc":
        return {
            "character_arc": query.character_arc(
                str(arguments.get("character", "")),
                branch_id=str(arguments.get("branch_id", "mainline")),
            )
        }
    if tool_id == "get_relationship_arc":
        return {
            "relationship_arc": query.relationship_arc(
                str(arguments.get("entity_a", "")),
                str(arguments.get("entity_b", "")),
                branch_id=str(arguments.get("branch_id", "mainline")),
            )
        }
    if tool_id == "audit_ending_integrity":
        return {
            "ending_integrity": query.ending_integrity(
                str(arguments.get("story_unit_id", "")),
                branch_id=str(arguments.get("branch_id", "mainline")),
            )
        }
    if tool_id == "get_project_health":
        return {"project_health": query.project_health()}
    if tool_id == "get_index_status":
        return {"index_status": query.index_status()}
    if tool_id == "run_wow_acceptance":
        return {
            "acceptance": query.wow_acceptance(
                story_unit_id=str(arguments.get("story_unit_id")) if arguments.get("story_unit_id") else None,
                character=str(arguments.get("character", "")),
                branch_id=str(arguments.get("branch_id", "mainline")),
            )
        }
    if tool_id == "get_story_context":
        compiled = context.compile(
            prompt=str(arguments.get("prompt", "")),
            mode=EpistemicMode(str(arguments.get("mode", EpistemicMode.AUTHOR_OMNISCIENT))),
            maximum_chars=int(arguments.get("maximum_chars", 40_000)),
            active_character=str(arguments.get("active_character", "")),
            active_story_unit=(str(arguments.get("active_story_unit")) if arguments.get("active_story_unit") else None),
            branch_id=str(arguments.get("branch_id", "mainline")),
            user_pins=[str(item) for item in arguments.get("user_pins", []) if isinstance(item, str)],
        )
        return {
            "context": compiled.retrieved(),
            "story_state": compiled.model_state(),
            "inspector": compiled.inspector(),
        }
    raise KeyError(f"unknown Story Engine tool: {tool_id}")
