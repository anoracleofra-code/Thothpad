# Universal Story Engine — Phases 46–55

Status: implemented and green as of 2026-09-08.

This tranche extends the Story Engine from release-hardening into release-readiness evidence. It does not add new story-authority semantics. Instead, it makes long-run stability, supportability, interface compatibility, network behavior, performance budgets, relocation safety, quality-gate evidence, package/update integrity, and outstanding external release obligations explicit and testable.

## Phase 46 — Real-Project / Soak Replay

`run_soak_replay` repeatedly reopens the same Story Project through fresh store handles and verifies that deterministic reads preserve the same semantic fingerprint, project identity, source count, Story Unit count, and durable writer state. It is a read-only stability probe: no replay result can become canon.

Read-only tool: `run_soak_replay`.

## Phase 47 — Privacy-Safe Support Bundle

`get_support_bundle` emits a bounded diagnostic object suitable for support/debugging without manuscript prose, prompts, credentials, model payloads, or source paths. It includes only normalized engineering state such as compatibility, recovery status, local observability counters, offline readiness, project health, and query-plan/performance metadata.

Read-only tool: `get_support_bundle`.

## Phase 48 — Wire / API Compatibility Fingerprint

`get_interface_fingerprint` hashes the desktop Story protocol version, sidecar operations, read-only Story Tool IDs/risk classes, Story-index schema, Story Project schema, and durable Story State schema into one deterministic interface fingerprint. Tests prove that contract drift changes the fingerprint.

Read-only tool: `get_interface_fingerprint`.

## Phase 49 — Deterministic Network-Capture Certification

Release tooling validates concrete process-tree network-capture evidence rather than merely trusting the offline declaration. The Windows runtime evidence for this milestone monitored the local Release ThothPad runtime plus frozen Story sidecar across 20 TCP samples (~16.9 seconds), observed up to 17 processes, and recorded **zero TCP connections**.

The evidence is explicitly labeled as a local Release runtime capture, not as a clean-machine installer test. Clean-install network capture remains a distinct external production-release obligation.

## Phase 50 — Performance Budget Regression

`get_performance_budget` turns performance observations into an explicit machine-local regression contract. The hard gate remains structural: normalized core queries must use SQLite indexes and perform zero project-filesystem rescans. A generous local source-lookup observation budget catches catastrophic regressions without pretending one machine's timing is universal.

Read-only tool: `get_performance_budget`.

## Phase 51 — Backup / Restore Relocation Matrix

`get_relocation_readiness` verifies that portable Story metadata contains no manuscript bytes or absolute paths and that writer-owned state/rules can be rebound to another initialized project root. Relocation remains metadata/state portability, not source-file copying.

Read-only tool: `get_relocation_readiness`.

## Phase 52 — Quality-Gate Evidence Aggregator

Release evidence can be aggregated into required and optional gates without allowing optional or missing external evidence to suppress a required failure. Required failures keep the aggregate red; external platform actions remain visible as pending instead of silently disappearing.

The local Phase 46–55 release evidence aggregation passed.

## Phase 53 — Clean-Install / Package Contract Verification

Package-contract verification binds a normalized candidate manifest and CycloneDX SBOM to the expected application/package identity and validates that both artifacts are present, internally coherent, and checksum-addressable. The real frozen Story sidecar candidate and SBOM from the Phase-45 committed build pass this contract.

This phase verifies the package contract available in this Windows checkout. It does **not** claim that a signed Windows installer, Linux package, or notarized macOS bundle has been clean-installed here.

## Phase 54 — Release-Channel / Update Manifest Safety

Update manifests are explicit checksum/size/version/channel contracts. Artifact verification is fail-closed on SHA-256 or size mismatch. The milestone's development-channel manifest uses a deliberately non-routable example endpoint and is marked contract-only/not-published; no update service deployment is implied.

The real frozen Story sidecar artifact passed update-manifest generation and verification.

## Phase 55 — Final Release-Readiness Certification

`get_release_readiness` composes internal Story Engine release-hardening evidence while explicitly enumerating external production-release evidence that this workstation cannot legitimately provide:

- clean-machine installation of a packaged application;
- Windows production signing;
- Linux packaged runtime certification;
- macOS packaged runtime/notarization;
- and independent release-review evidence where required by the release process.

The **implementation/readiness contracts are green**. The report intentionally does not equate that with "production release shipped". Missing external platform/signing evidence remains visible and cannot be transformed into a fake pass.

Read-only tool: `get_release_readiness`.

## Native, MCP, and authority boundary

The six safe Phase 46–55 Story diagnostics are available in Story Lab Advanced and Author-Omniscient Story Intelligence:

- `run_soak_replay`
- `get_support_bundle`
- `get_interface_fingerprint`
- `get_performance_budget`
- `get_relocation_readiness`
- `get_release_readiness`

They are R0/read-only. Restricted Current POV / Character / Reader / Cold Reader modes still cannot use them to bypass the epistemically bounded Context Compiler. Model-supplied alternate branch IDs remain discarded in favor of trusted native branch context.

The synchronized MCP surface contains **78 read-only tools**. Packaging/network/update validators remain release tooling rather than model capabilities.

## Certification evidence

Final 2026-09-08 gates:

- dedicated Phase 46–55 engine/package suite: **10/10 PASS**;
- touched Phase 46–55 Ruff surface: PASS;
- `mypy`: PASS across **134** backend source files using the reviewed `writer-engine/pyproject.toml` policy;
- full writer-engine suite: **547 passed, 3 expected platform skips, 0 failures**;
- packaging suite: **52 passed, 1 expected platform skip, 6 subtests PASS**;
- combined writer-engine + packaging behavior: **599 passed, 4 expected skips, 6 subtests PASS**;
- full native application build/link: PASS;
- native CTest: **100% — 23 passed, 1 intentional benchmark-collector skip, 0 failures**;
- Story Lab Phase 46–55 action-presence/native security regression: PASS;
- real runtime network observation: **20 TCP samples / ~16.9 s / 17 peak observed processes / 0 TCP connections**;
- Phase-53 real candidate/SBOM package contract: PASS;
- Phase-54 checksum-bound update-manifest contract over the frozen Story sidecar: PASS;
- local quality-gate aggregation: PASS; and
- inherited committed Phase-45 frozen candidate cryptographic attestation remains valid:
  `c18dc287e59e946e8ec90c0002880d27147e5674c2e57bce9aa7085f6f94f991`.

Phases 46–55 therefore close the Story Engine implementation/readiness ledger through Phase 55. Production signing, notarization, and clean-machine multi-platform installer validation remain explicit release operations outside this implementation milestone rather than hidden completion debt.
