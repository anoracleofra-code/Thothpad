# Universal Story Engine — Phases 36–45

Status: implemented and green as of 2026-09-08.

This tranche is the release-hardening extension after Phase 35. It does not add
new story-authority semantics. Instead, it proves that the Story Engine can be
operated, recovered, observed, packaged, upgraded, and exercised under hostile
or unusual real-world conditions without surrendering the architectural
invariants in `ARCHITECTURE.md`.

## Phase 36 — Real-Project Validation Corpus

`ReleaseProjectValidator` evaluates a live normalized Story Project against hard
release gates rather than a prose-quality score. It requires normalized sources,
anchored Story Units, zero alternate-branch contamination, respected Context
Compiler budgets, and portable project-relative paths. The validator is layout
agnostic and records source-format/nesting coverage without assigning semantic
meaning to folder names.

Read-only tool: `get_release_validation`.

## Phase 37 — Crash / Recovery Integrity

Recovery-sensitive operations can record an atomic local recovery journal. On
recovery, the SQLite Story index is treated as disposable: ThothPad discards the
possibly partial cache and rebuilds it from current source files plus durable
writer-owned Story State. Source files are never rewritten by recovery.

The writer-facing recovery operation requires explicit confirmation. Tests
inject cache-only writer state after a simulated crash and prove that recovery
removes it while preserving durable writer truth.

Desktop operation: `story_recover`. Read-only status: `get_recovery_status`.

## Phase 38 — Privacy-Safe Observability

Story Engine operational observations are local, bounded, and content-free.
Counters/timings do not record manuscript prose, prompts, source paths,
credentials, or model payloads. No telemetry upload is required for diagnostics.

Read-only tool: `get_observability_report`.

## Phase 39 — Accessibility / Keyboard Story Lab

Story Lab's release/recovery controls now carry explicit accessible names and
descriptions, keyboard mnemonics, deterministic tab order, and focusable native
controls. A Qt regression test asserts those accessibility contracts in the real
native dialog rather than relying on documentation alone.

The Advanced workspace includes explicit writer-facing Backup, Recover, and
Restore controls. These operations remain outside the AI/MCP mutation surface.

## Phase 40 — Cancellation / Resource Budgets

Long-running Story operations can share a real `StoryResourceBudget` enforcing
bounded records, bounded characters, wall-clock deadlines, and cooperative
cancellation checkpoints. Story Explorer consumes this budget while traversing
normalized Story Model state instead of merely truncating its final response.

Read-only policy inspection: `get_resource_policy`.

## Phase 41 — Unicode / Cross-Platform Path Resilience

Path validation detects portability hazards before they become broken projects:
Unicode normalization/casefold collisions, traversal, control characters,
Windows-reserved names, trailing dot/space segments, and excessive path
segments. The engine continues to support valid Unicode filenames and project
roots.

Read-only tool: `get_path_resilience`.

## Phase 42 — Offline / Egress Enforcement

The deterministic Story Engine declares and tests that indexing, state queries,
release validation, recovery diagnostics, and local normalized analysis require
no network access. Model routing remains independently governed by the existing
Local Only / Prefer Local / Allow Remote policy. Tests disable socket creation
and require deterministic Story operations to remain functional.

Read-only tool: `get_offline_readiness`.

## Phase 43 — Reproducible Packaging / SBOM Attestation

Release evidence can cryptographically bind:

- the exact source commit;
- the locked toolchain document;
- the generated CycloneDX SBOM;
- the normalized frozen-candidate manifest; and
- executable test evidence.

The existing packaging reproducibility/SBOM suite remains the platform packaging
authority. The Story Engine milestone additionally attests the frozen Windows
sidecar candidate itself. This does not pretend that a Clang development build
is the separate MSVC installer/signing pipeline; installer production remains a
platform release process.

## Phase 44 — Upgrade / Rollback Compatibility

Story Project and durable Story State schema handling now fails closed when a
future unsupported schema is encountered. Older/current state remains
migratable. Writer-owned Story State can be content-addressed into local backups
and restored only after explicit writer confirmation.

Rollback is authoritative: the compiled SQLite cache is deleted and rebuilt from
the selected durable backup. A regression test caught and fixed the earlier
additive-restore defect where newer cache-only writer claims could survive an
older durable restore.

Desktop operations: `story_state_backup`, `story_state_restore`.
Read-only status: `get_compatibility_status`.

## Phase 45 — Release-Candidate Certification

`run_release_candidate_acceptance` composes ten engine-level release checks:

1. representative normalized project validation;
2. crash-recovery state is inspectable;
3. observability is local/content-free;
4. native accessibility has an executable Qt gate;
5. Story operations publish bounded resource policy;
6. project paths are cross-platform portable;
7. deterministic Story Engine behavior is offline-capable;
8. packaging/SBOM attestation has an executable packaging gate;
9. Story State compatibility is fail-closed and backup-capable; and
10. release-candidate composition preserves writer authority.

The frozen protocol-1.9 sidecar passes all ten against a live Unicode-path
project, then independently passes writer-confirmed backup/restore and simulated
crash recovery.

## Native, MCP, and authority boundary

Protocol version: **1.9**.

Desktop-only writer operations added in this tranche:

- `story_recover`
- `story_state_backup`
- `story_state_restore`

They require native writer intent/confirmation as appropriate and are not MCP
mutations. New release diagnostics are R0 and available to author-omniscient
Story Intelligence; restricted epistemic modes still cannot bypass the Context
Compiler into raw Story Model state.

The synchronized MCP surface contains **72 read-only tools**.

## Certification evidence

Final 2026-09-08 gates:

- `ruff`: PASS;
- `mypy`: PASS across **127** backend source files;
- full Python suite: **540 passed, 3 platform-specific skips, 0 failures**;
- dedicated Phase 36–45 engine suite: **9/9 PASS**;
- Phase 36–45 protocol/security slice: **51/51 PASS**;
- packaging suite: **49 passed, 1 skip, 6 subtests PASS**;
- release-attestation unit suite: **2/2 PASS**;
- full native application build/link: PASS;
- native CTest: **100% — 23 passed, 1 intentional benchmark-collector skip, 0 failures**;
- Story Lab accessibility regression gate: PASS;
- locked Rust **1.95.0** / Harper + PyInstaller frozen Windows sidecar build: PASS; and
- frozen Story protocol 1.9 release acceptance:
  **`PACKAGED_PROTOCOL_1_9_ACCEPTANCE=PASS`**, including **10/10** release-candidate
  steps, backup/restore, simulated crash recovery, Unicode project paths, and
  offline/content-free observability.

No independent worker/subagent grade is claimed. Executable regression, native,
packaging, and frozen-sidecar gates are the completion authority.
