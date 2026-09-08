from __future__ import annotations

import json
import sys
from typing import Any

from backend import config
from backend.manuscript import (
    analyze_manuscript,
    calibrate_corpus,
    load_lens_baselines,
    read_project_timeline,
)
from backend.models import RunRequest
from backend.pipeline import REWRITE_MODES, compare_texts, run_pipeline
from backend.profiles import list_profiles
from backend.storage import load_run
from backend.story.service import call_story_tool, project_understanding
from backend.validation import reject_json_constant as _reject_json_constant
from backend.validation import strict_bool_arg as _strict_bool
from backend.validation import validate_passes as _validate_passes
from backend.voice_profile import build_voice_profile

TOOLS = [
    {
        "name": "prose_diagnose",
        "description": "Analyze formulaic prose patterns; findings do not determine authorship.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "text": {"type": "string"},
                "profile": {"type": "string"},
                "persist": {"type": "boolean"},
                "overrides": {"type": "object", "description": "Per-analyzer profile overrides"},
            },
            "required": ["text"],
        },
    },
    {
        "name": "prose_rewrite",
        "description": "Rewrite prose using ThothPad routing.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "text": {"type": "string"},
                "profile": {"type": "string"},
                "mode": {"type": "string"},
                "passes": {"type": "integer"},
                "persist": {"type": "boolean"},
            },
            "required": ["text"],
        },
    },
    {
        "name": "prose_deslop",
        "description": "Aggressively reduce AI prose tells.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "text": {"type": "string"},
                "profile": {"type": "string"},
                "aggressiveness": {"type": "string"},
                "persist": {"type": "boolean"},
            },
            "required": ["text"],
        },
    },
    {
        "name": "prose_compare",
        "description": "Compare two drafts.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "before": {"type": "string"},
                "after": {"type": "string"},
                "profile": {"type": "string"},
                "persist": {"type": "boolean"},
            },
            "required": ["before", "after"],
        },
    },
    {
        "name": "prose_build_voice_profile",
        "description": "Build a voice profile from samples.",
        "inputSchema": {
            "type": "object",
            "properties": {"samples": {"type": "array", "items": {"type": "string"}}, "name": {"type": "string"}},
            "required": ["samples", "name"],
        },
    },
    {
        "name": "prose_list_profiles",
        "description": "List ThothPad profiles.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "prose_get_run",
        "description": "Load a saved ThothPad run report.",
        "inputSchema": {"type": "object", "properties": {"run_id": {"type": "string"}}, "required": ["run_id"]},
    },
    {
        "name": "prose_analyze_manuscript",
        "description": "Analyze multiple files as one manuscript for cross-file repetition, cliches, rhythm, and pattern hotspots.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "documents": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {"name": {"type": "string"}, "text": {"type": "string"}},
                        "required": ["name", "text"],
                    },
                },
                "profile": {"type": "string"},
                "project": {"type": "string"},
                "persist": {"type": "boolean"},
            },
            "required": ["documents"],
        },
    },
    {
        "name": "prose_calibrate_corpus",
        "description": "Build a model- or genre-specific overrepresentation profile from prose samples and optional human reference samples.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "samples": {"type": "array", "items": {"type": "string"}},
                "reference_samples": {"type": "array", "items": {"type": "string"}},
                "name": {"type": "string"},
            },
            "required": ["samples", "name"],
        },
    },
    {
        "name": "prose_quality_timeline",
        "description": "Return the ordered quality-ledger runs recorded for a project.",
        "inputSchema": {"type": "object", "properties": {"project": {"type": "string"}}, "required": ["project"]},
    },
    {
        "name": "prose_lens_baselines",
        "description": "Return stored genre lens-density baselines for a calibration name.",
        "inputSchema": {"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"]},
    },
    {
        "name": "story_project_understanding",
        "description": "Inspect a Story Project that the writer has already initialized in ThothPad.",
        "inputSchema": {
            "type": "object",
            "properties": {"project_root": {"type": "string"}},
            "required": ["project_root"],
        },
    },
    {
        "name": "story_resolve_entity",
        "description": "Resolve an entity name or alias in an initialized ThothPad Story Project.",
        "inputSchema": {
            "type": "object",
            "properties": {"project_root": {"type": "string"}, "name": {"type": "string"}},
            "required": ["project_root", "name"],
        },
    },
    {
        "name": "story_find_evidence",
        "description": "Search provenance-backed project evidence without exposing arbitrary filesystem access.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "project_root": {"type": "string"},
                "query": {"type": "string"},
                "limit": {"type": "integer", "minimum": 1, "maximum": 100},
            },
            "required": ["project_root", "query"],
        },
    },
    {
        "name": "story_query_claims",
        "description": "Query normalized story claims with authority and exact source provenance.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "project_root": {"type": "string"},
                "entity": {"type": "string"},
                "predicate": {"type": "string"},
                "branch_id": {"type": "string"},
                "include_noncanonical": {"type": "boolean"},
            },
            "required": ["project_root"],
        },
    },
    {
        "name": "story_get_context",
        "description": "Compile an inspectable, epistemically bounded context package for a story task.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "project_root": {"type": "string"},
                "prompt": {"type": "string"},
                "mode": {
                    "type": "string",
                    "enum": [
                        "author_omniscient",
                        "current_pov",
                        "character",
                        "reader",
                        "cold_reader",
                        "manuscript_only",
                        "world_reference_only",
                        "custom",
                    ],
                },
                "active_character": {"type": "string"},
                "active_story_unit": {"type": "string"},
                "maximum_chars": {"type": "integer", "minimum": 1000, "maximum": 250000},
            },
            "required": ["project_root", "prompt"],
        },
    },
    {
        "name": "story_get_character_knowledge",
        "description": "Read tracked knowledge and belief state for a character.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "project_root": {"type": "string"},
                "character": {"type": "string"},
                "branch_id": {"type": "string"},
            },
            "required": ["project_root", "character"],
        },
    },
    {
        "name": "story_get_character_beliefs",
        "description": "Read tracked beliefs, suspicions, and disbelief state for a character.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "project_root": {"type": "string"},
                "character": {"type": "string"},
                "branch_id": {"type": "string"},
                "limit": {"type": "integer", "minimum": 1, "maximum": 500},
            },
            "required": ["project_root", "character"],
        },
    },
    {
        "name": "story_get_reader_state",
        "description": "Read tracked reader information state, optionally through a story unit.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "project_root": {"type": "string"},
                "branch_id": {"type": "string"},
                "through_story_unit": {"type": "string"},
            },
            "required": ["project_root"],
        },
    },
    {
        "name": "story_query_timeline",
        "description": "Read normalized timeline events for a Story Project branch.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "project_root": {"type": "string"},
                "branch_id": {"type": "string"},
                "limit": {"type": "integer", "minimum": 1, "maximum": 500},
            },
            "required": ["project_root"],
        },
    },
    {
        "name": "story_get_world_state",
        "description": "Read typed world state for a resolved story entity.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "project_root": {"type": "string"},
                "entity": {"type": "string"},
                "state_type": {"type": "string"},
                "branch_id": {"type": "string"},
                "limit": {"type": "integer", "minimum": 1, "maximum": 500},
            },
            "required": ["project_root", "entity"],
        },
    },
    {
        "name": "story_where_is_entity",
        "description": "Read tracked location state for a story entity.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "project_root": {"type": "string"},
                "entity": {"type": "string"},
                "branch_id": {"type": "string"},
                "limit": {"type": "integer", "minimum": 1, "maximum": 500},
            },
            "required": ["project_root", "entity"],
        },
    },
    {
        "name": "story_who_has_object",
        "description": "Read tracked possession state for a resolved story object.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "project_root": {"type": "string"},
                "object": {"type": "string"},
                "branch_id": {"type": "string"},
                "limit": {"type": "integer", "minimum": 1, "maximum": 500},
            },
            "required": ["project_root", "object"],
        },
    },
    {
        "name": "story_list_threads",
        "description": "Read bounded narrative threads with provenance-backed evidence when available.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "project_root": {"type": "string"},
                "branch_id": {"type": "string"},
                "limit": {"type": "integer", "minimum": 1, "maximum": 500},
            },
            "required": ["project_root"],
        },
    },
    {
        "name": "story_list_reader_questions",
        "description": "Read bounded tracked reader questions with provenance-backed evidence.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "project_root": {"type": "string"},
                "branch_id": {"type": "string"},
                "limit": {"type": "integer", "minimum": 1, "maximum": 500},
            },
            "required": ["project_root"],
        },
    },
    {
        "name": "story_list_dramatic_promises",
        "description": "Read bounded dramatic promises with provenance-backed evidence.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "project_root": {"type": "string"},
                "branch_id": {"type": "string"},
                "limit": {"type": "integer", "minimum": 1, "maximum": 500},
            },
            "required": ["project_root"],
        },
    },
    {
        "name": "story_trace_causality",
        "description": "Trace bounded causal dependencies around a normalized story record.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "project_root": {"type": "string"},
                "record_kind": {"type": "string"},
                "record_id": {"type": "string"},
                "direction": {"type": "string", "enum": ["upstream", "downstream", "both"]},
                "maximum_depth": {"type": "integer", "minimum": 1, "maximum": 32},
                "branch_id": {"type": "string"},
            },
            "required": ["project_root", "record_kind", "record_id"],
        },
    },
    {
        "name": "story_get_decision_history",
        "description": "Read bounded consequential character decisions.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "project_root": {"type": "string"},
                "character": {"type": "string"},
                "branch_id": {"type": "string"},
                "limit": {"type": "integer", "minimum": 1, "maximum": 500},
            },
            "required": ["project_root"],
        },
    },
    {
        "name": "story_get_opposition_state",
        "description": "Read bounded opposition attached to story objectives.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "project_root": {"type": "string"},
                "objective_id": {"type": "string"},
                "branch_id": {"type": "string"},
                "limit": {"type": "integer", "minimum": 1, "maximum": 500},
            },
            "required": ["project_root"],
        },
    },
    {
        "name": "story_get_scene_contract",
        "description": "Read the reviewed scene contract for one stable story unit.",
        "inputSchema": {
            "type": "object",
            "properties": {"project_root": {"type": "string"}, "story_unit_id": {"type": "string"}},
            "required": ["project_root", "story_unit_id"],
        },
    },
    {
        "name": "story_get_author_decisions",
        "description": "Read writer-owned structural decisions and rationale.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "project_root": {"type": "string"},
                "story_unit_id": {"type": "string"},
                "branch_id": {"type": "string"},
                "limit": {"type": "integer", "minimum": 1, "maximum": 500},
            },
            "required": ["project_root"],
        },
    },
    {
        "name": "story_audit_scene",
        "description": "Compose a deterministic scene audit from tracked narrative state; missing state is reported as untracked, not as a prose defect.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "project_root": {"type": "string"},
                "story_unit_id": {"type": "string"},
                "branch_id": {"type": "string"},
            },
            "required": ["project_root", "story_unit_id"],
        },
    },
    {
        "name": "story_audit_chapter",
        "description": "Compose a deterministic chapter audit from tracked narrative state; missing state is reported as untracked, not as a prose defect.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "project_root": {"type": "string"},
                "story_unit_id": {"type": "string"},
                "branch_id": {"type": "string"},
            },
            "required": ["project_root", "story_unit_id"],
        },
    },
    {
        "name": "story_list_branches",
        "description": "List alternate Story Engine branches with freshness and merge coverage.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "project_root": {"type": "string"},
                "limit": {"type": "integer", "minimum": 1, "maximum": 500},
            },
            "required": ["project_root"],
        },
    },
    {
        "name": "story_compare_branch",
        "description": "Read one alternate branch diff, merge history, and stale-base status.",
        "inputSchema": {
            "type": "object",
            "properties": {"project_root": {"type": "string"}, "branch_id": {"type": "string"}},
            "required": ["project_root", "branch_id"],
        },
    },
    {
        "name": "story_get_retcon_impact",
        "description": "Trace registered downstream dependencies for a potential retcon without mutating story truth.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "project_root": {"type": "string"},
                "source_kind": {"type": "string"},
                "source_id": {"type": "string"},
                "maximum_nodes": {"type": "integer", "minimum": 1, "maximum": 5000},
            },
            "required": ["project_root", "source_kind", "source_id"],
        },
    },
    {
        "name": "story_cold_reader_at",
        "description": "Read only story evidence and reader state available at one manuscript cutoff.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "project_root": {"type": "string"},
                "story_unit_id": {"type": "string"},
                "prompt": {"type": "string"},
                "branch_id": {"type": "string"},
                "maximum_chars": {"type": "integer", "minimum": 1000, "maximum": 100000},
            },
            "required": ["project_root", "story_unit_id"],
        },
    },
    {
        "name": "story_audit_reveal_fairness",
        "description": "Audit tracked manuscript setup before a reveal without treating missing tracking as proof of unfairness.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "project_root": {"type": "string"},
                "story_unit_id": {"type": "string"},
                "claim_id": {"type": "string"},
                "branch_id": {"type": "string"},
                "limit": {"type": "integer", "minimum": 1, "maximum": 500},
            },
            "required": ["project_root", "story_unit_id"],
        },
    },
    {
        "name": "story_get_reader_expectations",
        "description": "Read tracked reader questions and dramatic promises open by a story cutoff.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "project_root": {"type": "string"},
                "story_unit_id": {"type": "string"},
                "branch_id": {"type": "string"},
                "limit": {"type": "integer", "minimum": 1, "maximum": 500},
            },
            "required": ["project_root", "story_unit_id"],
        },
    },
    {
        "name": "story_get_dramatic_irony",
        "description": "Compare tracked reader access with one character's tracked knowledge at a story cutoff.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "project_root": {"type": "string"},
                "story_unit_id": {"type": "string"},
                "character": {"type": "string"},
                "branch_id": {"type": "string"},
                "limit": {"type": "integer", "minimum": 1, "maximum": 500},
            },
            "required": ["project_root", "story_unit_id", "character"],
        },
    },
    {
        "name": "story_get_writer_model",
        "description": "Read confirmed and provisional writer preferences with bounded behavioral evidence.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "project_root": {"type": "string"},
                "scope_kind": {"type": "string"},
                "scope_id": {"type": "string"},
                "include_ignored": {"type": "boolean"},
                "limit": {"type": "integer", "minimum": 1, "maximum": 500},
            },
            "required": ["project_root"],
        },
    },
    {
        "name": "story_explain_writer_preference",
        "description": "Explain one writer preference using its bounded local evidence.",
        "inputSchema": {
            "type": "object",
            "properties": {"project_root": {"type": "string"}, "preference_id": {"type": "string"}},
            "required": ["project_root", "preference_id"],
        },
    },
    {
        "name": "story_run_editorial_council",
        "description": "Run seven independent bounded read-only editorial reviewers and synthesize agreement/disagreement without agent chatter.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "project_root": {"type": "string"},
                "story_unit_id": {"type": "string"},
                "branch_id": {"type": "string"},
            },
            "required": ["project_root", "story_unit_id"],
        },
    },
    {
        "name": "story_list_lenses",
        "description": "List writer-defined reusable Story Lenses.",
        "inputSchema": {
            "type": "object",
            "properties": {"project_root": {"type": "string"}, "include_archived": {"type": "boolean"}},
            "required": ["project_root"],
        },
    },
    {
        "name": "story_get_lens",
        "description": "Read one Story Lens and its exact-source evidence findings.",
        "inputSchema": {
            "type": "object",
            "properties": {"project_root": {"type": "string"}, "lens_id": {"type": "string"}},
            "required": ["project_root", "lens_id"],
        },
    },
    {
        "name": "story_run_lens",
        "description": "Run deterministic evidence retrieval for a Story Lens; lexical/entity retrieval never proves the semantic statement.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "project_root": {"type": "string"},
                "lens_id": {"type": "string"},
                "maximum_findings": {"type": "integer", "minimum": 1, "maximum": 500},
            },
            "required": ["project_root", "lens_id"],
        },
    },
    {
        "name": "story_get_reader_experience",
        "description": "Read qualitative reader-experience cues for one story unit without fake numerical precision.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "project_root": {"type": "string"},
                "story_unit_id": {"type": "string"},
                "branch_id": {"type": "string"},
            },
            "required": ["project_root", "story_unit_id"],
        },
    },
    {
        "name": "story_get_reader_experience_timeline",
        "description": "Build a qualitative structural reader-experience timeline in writer-owned manuscript order.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "project_root": {"type": "string"},
                "branch_id": {"type": "string"},
                "source_id": {"type": "string"},
                "maximum_units": {"type": "integer", "minimum": 1, "maximum": 500},
            },
            "required": ["project_root"],
        },
    },
    {
        "name": "story_explore",
        "description": "Explore bounded cross-state story nodes, evidence, relationships, and dependency edges.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "project_root": {"type": "string"},
                "query": {"type": "string"},
                "branch_id": {"type": "string"},
                "limit": {"type": "integer", "minimum": 1, "maximum": 100},
            },
            "required": ["project_root", "query"],
        },
    },
    {
        "name": "story_get_scene_semantics",
        "description": "Read exact character-presence plus explicitly tracked scene location, contract, decision, opposition, and fact state.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "project_root": {"type": "string"},
                "story_unit_id": {"type": "string"},
                "branch_id": {"type": "string"},
            },
            "required": ["project_root", "story_unit_id"],
        },
    },
    {
        "name": "story_audit_continuity",
        "description": "Audit tracked conflicts, world-state ambiguity, and character knowledge-access gaps without inventing continuity facts.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "project_root": {"type": "string"},
                "story_unit_id": {"type": "string"},
                "character": {"type": "string"},
                "branch_id": {"type": "string"},
            },
            "required": ["project_root", "story_unit_id"],
        },
    },
    {
        "name": "story_get_character_arc",
        "description": "Read ordered tracked decision, knowledge, and relationship-state changes for one character.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "project_root": {"type": "string"},
                "character": {"type": "string"},
                "branch_id": {"type": "string"},
            },
            "required": ["project_root", "character"],
        },
    },
    {
        "name": "story_get_relationship_arc",
        "description": "Read explicit relationship-state transitions between two resolved entities.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "project_root": {"type": "string"},
                "entity_a": {"type": "string"},
                "entity_b": {"type": "string"},
                "branch_id": {"type": "string"},
            },
            "required": ["project_root", "entity_a", "entity_b"],
        },
    },
    {
        "name": "story_audit_ending_integrity",
        "description": "Audit tracked open threads/promises and causal prerequisites at an ending without turning backward diagnosis into canon.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "project_root": {"type": "string"},
                "story_unit_id": {"type": "string"},
                "branch_id": {"type": "string"},
            },
            "required": ["project_root", "story_unit_id"],
        },
    },
    {
        "name": "story_get_project_health",
        "description": "Read Story Engine engineering/coverage metrics without a universal quality score.",
        "inputSchema": {
            "type": "object",
            "properties": {"project_root": {"type": "string"}},
            "required": ["project_root"],
        },
    },
    {
        "name": "story_get_index_status",
        "description": "Read Story Engine cache/index integrity and stale-evidence status; does not rebuild or mutate it.",
        "inputSchema": {
            "type": "object",
            "properties": {"project_root": {"type": "string"}},
            "required": ["project_root"],
        },
    },
    {
        "name": "story_run_wow_acceptance",
        "description": "Run the ten-step read-only Universal Story Engine acceptance harness.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "project_root": {"type": "string"},
                "story_unit_id": {"type": "string"},
                "character": {"type": "string"},
                "branch_id": {"type": "string"},
            },
            "required": ["project_root"],
        },
    },
    {
        "name": "story_get_performance_report",
        "description": "Read indexed Story Model query plans and local latency observations.",
        "inputSchema": {
            "type": "object",
            "properties": {"project_root": {"type": "string"}},
            "required": ["project_root"],
        },
    },
    {
        "name": "story_get_security_audit",
        "description": "Inspect Story Project filesystem/adaptor safety boundaries without changing files.",
        "inputSchema": {
            "type": "object",
            "properties": {"project_root": {"type": "string"}},
            "required": ["project_root"],
        },
    },
    {
        "name": "story_get_model_fingerprint",
        "description": "Read a path/ID-independent normalized Story Model fingerprint.",
        "inputSchema": {
            "type": "object",
            "properties": {"project_root": {"type": "string"}},
            "required": ["project_root"],
        },
    },
    {
        "name": "story_get_acceptance_metrics",
        "description": "Read engineering acceptance metrics without producing a story-quality score.",
        "inputSchema": {
            "type": "object",
            "properties": {"project_root": {"type": "string"}},
            "required": ["project_root"],
        },
    },
    {
        "name": "story_get_retrieval_capabilities",
        "description": "Inspect lexical/default and optional semantic retrieval-signal guarantees.",
        "inputSchema": {
            "type": "object",
            "properties": {"project_root": {"type": "string"}},
            "required": ["project_root"],
        },
    },
    {
        "name": "story_explain_record",
        "description": "Explain one Story Model record through tracked provenance and dependency edges.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "project_root": {"type": "string"},
                "record_kind": {"type": "string"},
                "record_id": {"type": "string"},
            },
            "required": ["project_root", "record_kind", "record_id"],
        },
    },
    {
        "name": "story_run_operational_acceptance",
        "description": "Run the read-only ten-step Story Engine acceptance harness for phases 26-35.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "project_root": {"type": "string"},
                "prompt": {"type": "string"},
            },
            "required": ["project_root"],
        },
    },
    {
        "name": "story_get_release_validation",
        "description": "Read release-grade Story Engine hard-gate validation for the active project.",
        "inputSchema": {"type": "object", "properties": {"project_root": {"type": "string"}}, "required": ["project_root"]},
    },
    {
        "name": "story_get_recovery_status",
        "description": "Read crash-recovery journal status without changing Story State.",
        "inputSchema": {"type": "object", "properties": {"project_root": {"type": "string"}}, "required": ["project_root"]},
    },
    {
        "name": "story_get_observability_report",
        "description": "Read local content-free Story Engine operational counters and timings.",
        "inputSchema": {"type": "object", "properties": {"project_root": {"type": "string"}}, "required": ["project_root"]},
    },
    {
        "name": "story_get_resource_policy",
        "description": "Read bounded Story Engine record/character/time/cancellation policy.",
        "inputSchema": {"type": "object", "properties": {"project_root": {"type": "string"}}, "required": ["project_root"]},
    },
    {
        "name": "story_get_path_resilience",
        "description": "Read Unicode/cross-platform project-relative path portability diagnostics.",
        "inputSchema": {"type": "object", "properties": {"project_root": {"type": "string"}}, "required": ["project_root"]},
    },
    {
        "name": "story_get_offline_readiness",
        "description": "Read deterministic local/offline guarantees and model privacy readiness.",
        "inputSchema": {"type": "object", "properties": {"project_root": {"type": "string"}}, "required": ["project_root"]},
    },
    {
        "name": "story_get_compatibility_status",
        "description": "Read Story Project/State schema compatibility and rollback-backup availability.",
        "inputSchema": {"type": "object", "properties": {"project_root": {"type": "string"}}, "required": ["project_root"]},
    },
    {
        "name": "story_run_release_candidate_acceptance",
        "description": "Run the read-only engine release-candidate harness for phases 36-45.",
        "inputSchema": {"type": "object", "properties": {"project_root": {"type": "string"}}, "required": ["project_root"]},
    },
]


def tool_call(name: str, args: dict[str, Any]) -> dict[str, Any]:
    if name.startswith("story_"):
        root = args.get("project_root")
        if not isinstance(root, str) or not root.strip():
            raise ValueError("project_root must be a non-empty string")
        if name == "story_project_understanding":
            return project_understanding(root, initialize=False)
        mapping = {
            "story_resolve_entity": "resolve_entity",
            "story_find_evidence": "find_story_evidence",
            "story_query_claims": "query_claims",
            "story_get_context": "get_story_context",
            "story_get_character_knowledge": "get_character_knowledge",
            "story_get_character_beliefs": "get_character_beliefs",
            "story_get_reader_state": "get_reader_state",
            "story_query_timeline": "query_timeline",
            "story_get_world_state": "get_world_state",
            "story_where_is_entity": "where_is_entity",
            "story_who_has_object": "who_has_object",
            "story_list_threads": "list_threads",
            "story_list_reader_questions": "list_reader_questions",
            "story_list_dramatic_promises": "list_dramatic_promises",
            "story_trace_causality": "trace_causality",
            "story_get_decision_history": "get_decision_history",
            "story_get_opposition_state": "get_opposition_state",
            "story_get_scene_contract": "get_scene_contract",
            "story_get_author_decisions": "get_author_decisions",
            "story_audit_scene": "audit_scene",
            "story_audit_chapter": "audit_chapter",
            "story_list_branches": "list_branches",
            "story_compare_branch": "compare_branch",
            "story_get_retcon_impact": "get_retcon_impact",
            "story_cold_reader_at": "cold_reader_at",
            "story_audit_reveal_fairness": "audit_reveal_fairness",
            "story_get_reader_expectations": "get_reader_expectations",
            "story_get_dramatic_irony": "get_dramatic_irony",
            "story_get_writer_model": "get_writer_model",
            "story_explain_writer_preference": "explain_writer_preference",
            "story_run_editorial_council": "run_editorial_council",
            "story_list_lenses": "list_story_lenses",
            "story_get_lens": "get_story_lens",
            "story_run_lens": "run_story_lens",
            "story_get_reader_experience": "get_reader_experience",
            "story_get_reader_experience_timeline": "get_reader_experience_timeline",
            "story_explore": "explore_story",
            "story_get_scene_semantics": "get_scene_semantics",
            "story_audit_continuity": "audit_continuity",
            "story_get_character_arc": "get_character_arc",
            "story_get_relationship_arc": "get_relationship_arc",
            "story_audit_ending_integrity": "audit_ending_integrity",
            "story_get_project_health": "get_project_health",
            "story_get_index_status": "get_index_status",
            "story_run_wow_acceptance": "run_wow_acceptance",
            "story_get_performance_report": "get_performance_report",
            "story_get_security_audit": "get_security_audit",
            "story_get_model_fingerprint": "get_model_fingerprint",
            "story_get_acceptance_metrics": "get_acceptance_metrics",
            "story_get_retrieval_capabilities": "get_retrieval_capabilities",
            "story_explain_record": "explain_story_record",
            "story_run_operational_acceptance": "run_operational_acceptance",
            "story_get_release_validation": "get_release_validation",
            "story_get_recovery_status": "get_recovery_status",
            "story_get_observability_report": "get_observability_report",
            "story_get_resource_policy": "get_resource_policy",
            "story_get_path_resilience": "get_path_resilience",
            "story_get_offline_readiness": "get_offline_readiness",
            "story_get_compatibility_status": "get_compatibility_status",
            "story_run_release_candidate_acceptance": "run_release_candidate_acceptance",
        }
        tool_id = mapping.get(name)
        if tool_id is None:
            raise ValueError(f"unknown story tool: {name}")
        arguments = {key: value for key, value in args.items() if key != "project_root"}
        return call_story_tool(root, tool_id, arguments, initialize=False)
    if name == "prose_diagnose":
        overrides = args.get("overrides") if isinstance(args.get("overrides"), dict) else None
        return run_pipeline(
            RunRequest(
                text=args["text"],
                profile=args.get("profile", config.DEFAULT_PROFILE),
                mode="diagnose",
                persist=_strict_bool(args, "persist"),
                overrides=overrides,
            )
        )
    if name == "prose_rewrite":
        mode = str(args.get("mode", "rewrite"))
        if mode not in REWRITE_MODES:
            raise ValueError("unsupported rewrite mode")
        return run_pipeline(
            RunRequest(
                text=args["text"],
                profile=args.get("profile", config.DEFAULT_PROFILE),
                mode=mode,
                passes=_validate_passes(args.get("passes", 1)),
                persist=_strict_bool(args, "persist"),
            )
        )
    if name == "prose_deslop":
        return run_pipeline(
            RunRequest(
                text=args["text"],
                profile=args.get("profile", config.DEFAULT_PROFILE),
                mode="deslop",
                aggressiveness=args.get("aggressiveness", "medium"),
                persist=_strict_bool(args, "persist"),
            )
        )
    if name == "prose_compare":
        return compare_texts(
            args["before"],
            args["after"],
            args.get("profile", config.DEFAULT_PROFILE),
            persist=_strict_bool(args, "persist"),
        )
    if name == "prose_build_voice_profile":
        return build_voice_profile(args["samples"], args["name"])
    if name == "prose_quality_timeline":
        return read_project_timeline(args["project"])
    if name == "prose_lens_baselines":
        return {"name": args["name"], "baselines": load_lens_baselines(args["name"])}
    if name == "prose_list_profiles":
        return {"profiles": list_profiles()}
    if name == "prose_get_run":
        return load_run(args["run_id"])
    if name == "prose_analyze_manuscript":
        return analyze_manuscript(
            args["documents"],
            args.get("profile", config.DEFAULT_PROFILE),
            project=args.get("project"),
            persist=_strict_bool(args, "persist"),
        )
    if name == "prose_calibrate_corpus":
        return calibrate_corpus(
            args["samples"],
            args["name"],
            args.get("reference_samples", []),
        )
    raise ValueError(f"unknown tool: {name}")


def respond(msg_id: Any, result: Any = None, error: Any = None) -> None:
    payload = {"jsonrpc": "2.0", "id": msg_id}
    if error is not None:
        payload["error"] = {"code": -32000, "message": str(error)}
    else:
        payload["result"] = result
    body = json.dumps(payload, ensure_ascii=False, allow_nan=False)
    if len(body.encode("utf-8")) > config.MAX_RESPONSE_BYTES:
        body = json.dumps(
            {
                "jsonrpc": "2.0",
                "id": msg_id,
                "error": {"code": -32001, "message": "response exceeds configured limit"},
            }
        )
    print(body, flush=True)


def main() -> int:
    reader = getattr(sys.stdin, "buffer", sys.stdin)
    while True:
        raw = reader.readline(config.MAX_FRAME_BYTES + 1)
        if not raw:
            break
        raw_size = len(raw.encode("utf-8")) if isinstance(raw, str) else len(raw)
        if raw_size > config.MAX_FRAME_BYTES:
            respond(None, error="request exceeds configured limit")
            return 1
        if isinstance(raw, str):
            line = raw
        else:
            try:
                line = raw.decode("utf-8")
            except UnicodeDecodeError as exc:
                respond(None, error=exc)
                continue
        if not line.strip():
            continue
        msg_id: Any = None
        try:
            msg = json.loads(line, parse_constant=_reject_json_constant)
            if not isinstance(msg, dict):
                raise ValueError("JSON-RPC message must be an object")
            method = msg.get("method")
            msg_id = msg.get("id")
            if method == "initialize":
                respond(
                    msg_id,
                    {
                        "protocolVersion": "2024-11-05",
                        "capabilities": {"tools": {}},
                        "serverInfo": {"name": "thothpad", "version": config.ENGINE_VERSION},
                    },
                )
            elif method == "tools/list":
                respond(msg_id, {"tools": TOOLS})
            elif method == "tools/call":
                params = msg.get("params", {})
                result = tool_call(params.get("name"), params.get("arguments", {}))
                respond(
                    msg_id,
                    {
                        "content": [
                            {
                                "type": "text",
                                "text": json.dumps(result, ensure_ascii=False, allow_nan=False),
                            }
                        ]
                    },
                )
            elif method == "notifications/initialized":
                continue
            else:
                respond(msg_id, error=f"unsupported method: {method}")
        except Exception as exc:
            respond(msg_id, error=exc)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
