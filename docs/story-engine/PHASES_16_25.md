# Universal Story Engine — Phases 16–25

Status: implemented and green as of 2026-09-07.

This document records the ten-phase extension after the original Phase 0–15
roadmap. The extension does not change the authority model frozen in
`ARCHITECTURE.md`: retrieval is not canon, missing metadata is not a prose
failure, alternate-branch state cannot contaminate mainline, and AI-facing
tools cannot silently mutate writer-owned story truth.

## Phase 16 — Story Explorer

The Story Explorer provides bounded cross-state discovery across normalized
entities, claims, story units, evidence, threads, promises, decisions,
relationships, and registered dependency edges. Search terms are matched
independently so a multi-term query can recover related nodes without requiring
the literal phrase to exist. The result explicitly records that retrieval is
bounded and is not itself a semantic conclusion.

Surface:

- Story Engine R0 tool: `explore_story`
- read-only MCP tool: `story_explore`
- Story Lab Advanced workspace

## Phase 17 — Scene Semantics

Scene Semantics reports only mechanically grounded scene state:

- characters whose indexed mentions overlap the exact story-unit span;
- explicitly tracked locations;
- reviewed scene contracts;
- tracked decisions and opposition;
- grounded claims associated with the unit.

An omitted item means **untracked**, not absent from the prose.

Surface: `get_scene_semantics` / `story_get_scene_semantics` and Story Lab.

## Phase 18 — Continuity and Knowledge-Access Audit

The continuity auditor composes explicit conflict records, overlapping tracked
world-state values, and character knowledge-access gaps. A missing knowledge
record is emitted as `UNTRACKED_ACCESS_CANDIDATE`; the result states that this
is a review candidate and not proof of an omniscience or continuity leak.

Surface: `audit_continuity` / `story_audit_continuity` and Story Lab.

## Phase 19 — Character and Relationship Arcs

Arc intelligence orders already-tracked changes rather than manufacturing an
interpretation. Character arcs may contain decisions, knowledge/belief changes,
and relationship changes. Relationship arcs contain explicit state
transitions between two resolved entities. The engine does not infer
psychological growth, emotional meaning, or thematic significance merely to
complete an arc.

Surface:

- `get_character_arc` / `story_get_character_arc`
- `get_relationship_arc` / `story_get_relationship_arc`
- Story Lab

## Phase 20 — Ending Integrity / Backpropagation

Ending Integrity audits tracked open threads, reader questions/promises, and
causal prerequisites at a selected ending. Backward prerequisites are
diagnostic only. They remain suggestions for author review and cannot become
canon through the audit.

Surface: `audit_ending_integrity` / `story_audit_ending_integrity` and Story Lab.

## Phase 21 — Adapter Expansion

The generic arbitrary-folder ingestion path now has concrete extraction for:

- Markdown / Markdown-like text;
- plain text / RST;
- DOCX;
- Fountain;
- HTML / HTM; and
- RTF.

The generic folder adapter remains the baseline. Format adapters contribute
text/structure/link observations; they do not grant semantic authority.

## Phase 22 — Project Health Metrics

Project Health reports engineering and coverage state, including:

- source classification coverage;
- grounded versus ungrounded material claims;
- stale evidence;
- open conflicts;
- alternate-branch contamination checks;
- alias ambiguity;
- SQLite foreign-key integrity;
- dependency record integrity; and
- Context Compiler budget compliance.

It deliberately exposes no universal story-quality score.

Surface: `get_project_health` / `story_get_project_health` and Story Lab.

## Phase 23 — Index Lifecycle

The SQLite Story Engine database is now operationally disposable, matching the
architecture contract. Index status reports cache size, schema version, FTS
availability, source/chunk/unit/claim counts, stale evidence, and foreign-key
issues.

Rebuild is a desktop-only, writer-confirmed operation:

1. persist durable writer-owned Story State;
2. verify the cache path remains inside the Story Project metadata root;
3. remove only the SQLite cache;
4. re-ingest project sources; and
5. hydrate durable writer state into the rebuilt cache.

`story_index_rebuild` is intentionally not exposed through MCP or to the model.

## Phase 24 — Portable Story Project Exchange

Portable Story Project bundles contain project rules and durable writer-owned
Story State. They do **not** contain manuscript/source bytes, credentials, or
absolute paths. Relative source rules and paths are validated on export/import.

Import is desktop-only and requires explicit writer confirmation. Imported
writer claims are rebound to the destination project's project ID; source files
are neither copied nor overwritten. Native export uses `QSaveFile` for atomic
replacement.

Desktop operations:

- `story_project_export`
- `story_project_import`

These operations are intentionally absent from MCP/model mutation surfaces.

## Phase 25 — Wow Acceptance Certification

The executable acceptance harness verifies ten product promises against a live
normalized Story Project:

1. the arbitrary project can be understood;
2. the current story position is computationally legible;
3. character knowledge continuity is queryable;
4. reader perspective is hard-bounded against future information;
5. live promises/questions are queryable;
6. alternate continuity remains isolated;
7. selective branch promotion is transaction-tracked;
8. retcon dependencies are available;
9. the Writer Model is inspectable/rejectable; and
10. the exact compiled context — “WHAT THE AI SEES” — is inspectable.

The acceptance report is read-only and cannot change canon.

## Native and model security boundary

The nine new analysis tools are R0. In native AI chat they are exposed only in
`author_omniscient` mode. Character, Reader, Cold Reader, Current POV, and other
restricted epistemic modes receive only the bounded Context Compiler query and
cannot route around it to raw Story Model state.

Model-supplied branch IDs are discarded. The native controller supplies only
its trusted active alternate branch; mainline remains implicit.

## Certification evidence

Final 2026-09-07 gates:

- `ruff`: PASS;
- `mypy`: PASS across 108 backend source files;
- Python: 517 passed, 2 platform-specific skips;
- dedicated Phase 16–25 tests: 11/11 passed;
- native Full/Release application build: PASS;
- native CTest: 100%, 23 passed, 1 intentional benchmark-collector skip;
- source sidecar smoke: PASS;
- locked Rust/Harper + PyInstaller frozen Windows sidecar build: PASS;
- frozen executable contextual-POS smoke: PASS; and
- frozen Story protocol 1.7 lifecycle/exchange acceptance:
  `FROZEN_STORY_PROTOCOL_1_7_PASS`.

The Phase 6 10,000-source Context Compiler gate remains part of the inherited
0–15 certification.

Worker/subagent grading was requested but could not be delivered because the
worker orchestration channel returned `WORKER_IDENTITY_LOST` on both final
attempts. No independent worker grade is claimed; executable gates above are
the completion authority for this milestone.
