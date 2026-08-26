# ThothPad S-tier comprehensive sweep

## Objective

Perform a repository-wide, evidence-driven stabilization and optimization pass over ThothPad. The goal is not to maximize the number of changes. The goal is to eliminate validated correctness defects, reduce avoidable performance and cross-platform risk, strengthen test coverage around critical invariants, and leave the project in a state where CI results are trustworthy and release behavior is predictable.

## Review model

This sweep emulates independent specialist reviewers followed by a separate judge. Each specialist pass must produce source-backed findings with a concrete failure mode. The judge rejects duplicate, speculative, or low-value findings before remediation.

### Reviewer A — architecture and state flow
- Desktop/editor -> ProseController -> WriterEngineClient -> sidecar -> analyzer -> snapshot store -> UI
- ownership, lifetimes, initialization/shutdown, request correlation, stale-result rejection
- profile/lens state and configuration precedence

### Reviewer B — concurrency and IPC
- framing, buffering, cancellation, deadlines, request races, worker lifecycle
- thread safety, warm-worker lifetime, dispose/restart behavior
- partial reads/writes and shutdown/error recovery

### Reviewer C — analyzer correctness
- analyzer registry/presets/profile enablement
- offset correctness, UTF-16 conversions, dialogue/range handling
- false zeroes, duplicate findings, category mapping, count aggregation
- live vs document/manuscript capability consistency

### Reviewer D — performance and resource behavior
- typing-path latency and unnecessary whole-document work
- repeated parsing/tokenization/model initialization
- memory growth, snapshot retention, SQLite/query behavior
- child-process count and avoidable serialization/copies

### Reviewer E — cross-platform and packaging
- Windows/macOS/Linux path/process/environment behavior
- frozen resource discovery and bundled data validation
- installer/release acceptance and executable sidecar behavior

### Reviewer F — security and privacy
- subprocess/environment boundaries
- filesystem/path validation and archive/import/export handling
- network-capable grammar/LLM adapters, redirects, credentials, logging
- unsafe deserialization/injection and local data exposure

### Reviewer G — tests, CI, and release gates
- flaky/racy tests
- test-to-production invariant gaps
- semantic smoke tests for installed/frozen packages
- branch/release workflow correctness and duplicated or misleading gates

### Reviewer H — maintainability and dead-code debt
- duplicated platform abstractions
- unreachable or contradictory code
- stale feature flags/documentation drift
- exception swallowing and silent fallback behavior

## Judge

A finding is accepted only if all of the following are true:
1. A specific source path and control/data flow support it.
2. The failure or inefficiency is reproducible by reasoning, a test, or an invariant violation.
3. The proposed fix is narrower and safer than leaving the defect in place.
4. Existing behavior relied on by the product is preserved unless intentionally corrected.
5. The finding is not merely stylistic cleanup.

Severity:
- P0: data loss, corruption, severe security issue, or core engine unusable.
- P1: major feature silently wrong/unavailable, cross-platform breakage, serious race/resource leak.
- P2: meaningful correctness, performance, packaging, or observability defect.
- P3: maintainability/test debt with plausible future failure cost.

## S-tier acceptance rubric

The sweep earns S-tier only if:
- zero unresolved validated P0/P1 findings remain in reviewed first-party code;
- every changed behavior has regression coverage where practical;
- Linux/macOS/Windows engine CI is green on the final head;
- lint, typecheck, benchmark, freeze/package, and S-efficiency gates are green;
- critical engine startup/profile/analyze/result/cancel/shutdown flows have semantic coverage;
- no high-confidence security finding remains unaddressed;
- no known critical UI state can silently present "0 findings" when analysis actually failed or was disabled;
- performance changes do not regress the existing benchmark contract;
- the final judge reviews the complete diff for contradictions and unnecessary changes.

Grades below S:
- A: no P0/P1, but important P2/test or observability debt remains.
- B: stable main path but material cross-platform/performance/correctness gaps remain.
- C or lower: known major failure modes remain.

## Implementation sequence

1. Inventory first-party source, tests, workflows, profiles, resources, and packaging inputs.
2. Run independent static review passes without editing implementation code.
3. Judge and deduplicate findings; create a ranked remediation ledger.
4. Fix P0/P1 issues first in small commits with regression tests.
5. Fix high-confidence P2 issues that materially improve correctness/performance/portability.
6. Add semantic release/engine tests for gaps exposed by the review.
7. Run the full CI matrix on the exact final head and inspect every failure.
8. Perform a final diff-only judge pass and remove unnecessary changes.
9. Publish the remaining deferred P2/P3 items explicitly rather than claiming perfection.

## Scope

First-party ThothPad code, tests, build/release logic, profiles, engine resources, and integration glue are in scope. Vendored third-party libraries are not line-by-line re-audited unless ThothPad-specific integration or configuration creates a risk.
