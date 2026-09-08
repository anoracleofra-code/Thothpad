# Universal Story Engine — Phases 26–35

Status: implemented and green as of 2026-09-07.

This document records the ten-phase production/trust extension after the Phase
16–25 milestone. These phases do not weaken the authority contract in
`ARCHITECTURE.md`: project content remains data, retrieval/metrics do not become
truth, alternate continuity remains isolated, writer-owned mutations require
explicit review, and AI-facing tools cannot silently mutate canon.

## Phase 26 — Legacy Migration & Project Binding

Legacy schema-2 `.story.json` workspaces can be additively bound to normalized
Story Units without rewriting or deleting the legacy workspace. Binding:

- requires explicit writer confirmation;
- only accepts a workspace inside the active Story Project;
- rejects oversized/non-UTF-8/non-schema-2 payloads;
- records project-relative paths and stable Story Unit links;
- preserves the legacy workspace byte hash before/after binding; and
- is idempotent for the same workspace/manuscript pair.

Desktop operation: `story_legacy_bind`. Read-only status is available through
`get_migration_status`.

## Phase 27 — Incremental / Resumable Background Indexing

Story Engine indexing now has durable project-relative checkpoints over the
existing content-hash incremental ingestor. One bounded batch can be processed
without temporarily tombstoning unvisited sources. Inventory changes invalidate
obsolete checkpoints automatically; global reconciliation occurs only after the
entire current inventory has been visited.

Desktop operation: `story_index_batch`. Read-only status is available through
`get_indexing_status`.

## Phase 28 — Huge-Project Performance

Core normalized lookups have explicit SQLite indexes and a query-plan probe.
Performance diagnostics report whether core queries use indexes, local latency
observations, and whether Story Model queries fall back to filesystem scans.
The 10,000-source Context Compiler acceptance gate from Phase 6 remains part of
the inherited scale contract.

Read-only tool: `get_performance_report`.

## Phase 29 — Security & Malformed-Input Hardening

The Story Project boundary is fail-closed for risky input classes. The security
audit makes symlink, archive, executable, and adapter boundaries inspectable.
DOCX extraction now bounds archive entry count, uncompressed XML size, and
compression ratio to reject decompression-bomb style inputs.

Read-only tool: `get_security_audit`.

## Phase 30 — Privacy / Egress Inspection

The Egress Inspector compiles the exact bounded Story context/state that would
be supplied to a selected remote model and reports project-relative sources,
character budgets, authority/roles, typed-state counts, and the active
epistemic boundary. Credentials and absolute paths are never included.

The writer may optionally inspect bounded text previews in Story Lab. This
surface is intentionally not exposed to the co-writer as a self-inspection
backdoor.

Read-only desktop tool: `get_egress_preview`.

## Phase 31 — Project-Agnostic Fixture Matrix

The normalized Story Model can emit a filesystem-path/UUID-independent semantic
fingerprint. Acceptance fixtures build the same logical story under unrelated
folder layouts and stable IDs and require equivalent normalized fingerprints.
This proves the engine is reasoning over normalized story concepts rather than
hard-coded folder conventions.

Read-only tool: `get_model_fingerprint`.

## Phase 32 — Acceptance Metrics / Benchmarking

Engineering acceptance metrics cover provenance completeness, alias ambiguity,
Story Unit anchor coverage, branch contamination, Context Compiler budget
compliance, indexed core queries, and writer correction counts. They explicitly
do not produce a prose, creativity, or universal story-quality score.

Read-only tool: `get_acceptance_metrics`.

## Phase 33 — Hybrid Retrieval Signals

Retrieval now has a pluggable fusion layer. Lexical/FTS retrieval remains the
default; optional local semantic signals may rerank bounded candidates but
cannot change authority, canon status, provenance, or evidence truth. No vector
database or remote embedding provider is mandatory.

Read-only tool: `get_retrieval_capabilities`.

## Phase 34 — Proposal Queue + Writer Review

The Story Engine has a durable non-canon proposal queue. A model or desktop
workflow may submit a bounded proposal, but submission changes no Story State.
Only explicit writer acceptance routes a supported mainline proposal through
the existing writer-owned mutation API. Rejection creates no Story State.
Alternate-branch proposals cannot be accepted through the mainline proposal
path; they must use the branch workflow.

Story Lab exposes proposal Refresh / Accept / Reject controls. Real mainline
co-writer scene and character proposals are mirrored into this governed queue
without gaining mutation authority.

Desktop operations:

- `story_proposal_submit`
- `story_proposal_review`

Read-only inspection: `list_story_proposals`.

## Phase 35 — “Ask ThothPad Why” Explainability & Operational Certification

`explain_story_record` explains why a normalized record exists using the stored
record, exact provenance/evidence, and upstream/downstream dependency edges.
The explanation is descriptive and cannot promote inference into canon.

A second executable ten-step harness (`run_operational_acceptance`) certifies:

1. legacy migration is additive/read-only;
2. indexing is resumable;
3. core normalized queries are indexed;
4. filesystem/adaptor safety is fail-closed;
5. remote egress is inspectable/path/credential-safe;
6. Story Model fingerprints are layout/ID independent;
7. engineering hard gates pass;
8. semantic retrieval cannot become truth authority;
9. proposals remain non-canon until writer review; and
10. “Ask ThothPad Why” is provenance-backed.

All ten steps pass in the dedicated acceptance fixture and in the frozen
protocol-1.8 certification project.

## Native and model authority boundary

Phase 26–35 diagnostics are divided by authority rather than convenience:

- Writer-facing Story Lab may run migration binding, indexing, egress preview,
  proposal review, security/performance/metrics, and explainability.
- AI chat receives only bounded R0 diagnostics in `author_omniscient` mode.
- Character/Reader/Cold Reader/Current POV and other restricted epistemic modes
  still receive only the Context Compiler query and cannot route around their
  information boundary.
- Model-supplied branch IDs are discarded; only the trusted native alternate
  branch is injected, and mainline remains implicit.
- Migration, indexing control, proposal review, and canon-changing mutations are
  absent from the AI mutation surface.

## Protocol / MCP surface

Desktop Story protocol: **1.8**.

New desktop-only operations include:

- `story_legacy_bind`
- `story_index_batch`
- `story_proposal_submit`
- `story_proposal_review`

The read-only MCP surface is synchronized at **64 tools**. Mutation/review
operations remain desktop-owned.

## Certification evidence

Final 2026-09-07 gates:

- `ruff`: PASS;
- `mypy`: PASS across **119** backend source files;
- full Python suite: **530 passed, 3 platform-specific skips, 0 failures**;
- dedicated Phase 26–35 regression suite: PASS (including Windows symlink-policy skip where applicable);
- full native Release/Full build: PASS;
- native CTest: **100% — 23 passed, 1 intentional benchmark-collector skip, 0 failures**;
- Story Intelligence widget/client/VisualShell/StoryWorkspace focused suites: PASS;
- source sidecar smoke: PASS;
- locked Rust **1.95.0** / Harper + PyInstaller frozen Windows sidecar build: PASS;
- frozen contextual-POS smoke: PASS; and
- frozen Story protocol 1.8 production/trust acceptance:
  **`FROZEN_STORY_PROTOCOL_1_8_PHASES_26_35_PASS`**.

The original interactive packaged harness stalled because `--thothpad-worker`
is intentionally a one-shot raw-JSON worker that reads until EOF; the harness
incorrectly kept stdin open and used framed multi-request semantics. The final
certification invokes one worker process per request, matching the packaged
worker contract, and passes the complete lifecycle above.

No independent worker/subagent grade is claimed for this milestone. Executable
lint/type/test/native/package gates above are the completion authority.
