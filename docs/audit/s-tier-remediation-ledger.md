# S-tier remediation ledger

This ledger is the judge output from the independent audit passes. It is intentionally written before production-code remediation. Findings are accepted only where a concrete source-level failure mode or invariant violation was established.

## Accepted findings

### P0 — Save transaction integrity can lose unsaved work

**Sources:** `src/documentmanager.cpp`, `src/editor/asynctextwriter.cpp`

Several individually dangerous behaviors combine in the save/close path:

1. `DocumentManagerPrivate::saveFile()` marks the document unmodified before the asynchronous write has succeeded.
2. `writeError` clears `saveInProgress` and shows an error, but does not restore the document's modified state.
3. `checkSaveChanges()` treats `save()` / `saveAs()` returning `true` as permission to continue even though those methods only started an asynchronous write. `close()` can then wait for the failed write and still clear the document because the decision to proceed has already been made.
4. `AsyncTextWriterPrivate::writeToDisk()` calls `QSaveFile::commit()` but ignores its Boolean return value, so a commit failure can be emitted as `writeComplete`.
5. Read-only overwrite handling removes the original file before the replacement write is known to have succeeded.
6. Draft Save As removes the autosaved draft before the destination write succeeds.

**Impact:** a failed disk write can be represented as a successful/clean save state, and subsequent close/open flows can discard the only in-memory copy. Read-only overwrite and draft conversion can also destroy the previous on-disk recovery copy before replacement is durable.

**Remediation contract:** make close/open/save-before-destructive-operation wait for the outstanding write result; preserve dirty state on error; treat `QSaveFile::commit()` failure as an error; never delete the original read-only file or draft before a successful replacement; add regression coverage for write failure semantics.

### P1 — Persisted diagnostics with extra spans reconstruct as malformed objects

**Source:** `writer-engine/backend/analysis_store.py`

New-format persisted rows use `payload_json` to store `extra_spans_utf16`. `_diagnostic_from_row()` currently returns any non-empty decoded payload as though it were a complete legacy diagnostic. A repetition diagnostic with extra spans can therefore be returned as only `{"extra_spans_utf16": ...}` and lose analyzer, rule, severity, primary range, message, and other fields.

**Remediation:** distinguish legacy full-object payloads from new auxiliary payload metadata, reconstruct the normalized row, and attach extra spans. Add a persisted repetition/supplemental-span regression test.

### P1 — Lexical analysis can be disabled while the desktop presents legitimate zero counts

**Sources:** `src/prose/prosecontroller.cpp`, `writer-engine/backend/desktop_engine.py`

The desktop automatically guesses language and sends it with the mirrored document. The engine deliberately disables the lexical analyzer stack for detected non-English text and returns `lexical_rules_enabled=false`, but the desktop ignores that response state and can present every prose lens as a valid zero-result analysis.

**Remediation:** surface analysis-disabled state and detected language in the desktop instead of silently presenting zero findings. Preserve the engine's intentional language gate; do not force English without an explicit product decision.

### P2 — LanguageTool offsets are interpreted with the wrong coordinate system

**Source:** `writer-engine/backend/analyzers/external_tools.py`

LanguageTool offsets are UTF-16 code units. This integration directly slices Python code-point strings with those values, unlike the main grammar integration which correctly converts through `Utf16Index`.

**Remediation:** convert external LanguageTool offsets through `Utf16Index`; add an astral-Unicode/emoji regression fixture.

### P2 — Concrete-anchor detection is defeated by ordinary sentence capitalization

**Source:** `writer-engine/backend/analyzers/concrete_anchor.py`

The analyzer treats any `\b[A-Z][a-z]{2,}\b` token as a concrete proper-name anchor. The first word of a normal English paragraph commonly satisfies this test, making abstract paragraphs appear concretely anchored.

**Remediation:** exclude ordinary sentence-initial capitalization from the proper-name signal and cover both abstract and genuinely anchored paragraphs.

### P2 — Rhythm abstract-mic-drop detector uses substring membership

**Source:** `writer-engine/backend/analyzers/rhythm.py`

The detector checks whether strings such as `thing` occur anywhere in the lower-cased sentence. `Nothing.` therefore contains `thing` and can be misclassified as an abstract mic drop.

**Remediation:** use token-level membership; add a regression for `Nothing.` and a positive abstract-token case.

### P2 — Manuscript genre comparison undercounts analyzers with multiple finding types

**Source:** `writer-engine/backend/manuscript.py`

Genre comparison takes only the first hotspot row for each analyzer while baseline density counts all flags from that analyzer. The current manuscript density is therefore systematically low for analyzers producing multiple flag types.

**Remediation:** sum all hotspot matches by analyzer before per-1k normalization; add multi-flag-type coverage.

### P2 — Presentation-only lens changes can permanently shadow upgraded built-in profiles

**Sources:** `src/prose/prosecontroller.cpp`, `writer-engine/backend/profiles.py`

User profile files override bundled profiles by stem. The desktop's presentation-change timer writes the entire active profile back through `save_profile`, so changing a lens color/mode/visibility can create a user `creative-default.json` that permanently shadows later bundled `creative-default` updates.

**Remediation:** keep presentation state in desktop settings; reserve full profile persistence for explicit profile editing/import/save actions. Preserve intentional user-created profile overrides.

### P2 — Frozen/package validation does not prove the built-in profile set exists

**Sources:** `writer-engine/backend/build_sidecar.py`, package acceptance scripts

The build checks that the profiles directory exists, while `load_profile()` can manufacture an emergency `creative-default` fallback. A package can therefore lose expected built-in profile JSONs while basic engine smoke still succeeds.

**Remediation:** validate the expected built-in profile names (`creative-default`, `fiction-gritty`, `essay-direct`, `business-clean`) in source and frozen smoke paths.

### P2 — Installed-package acceptance proves process startup, not semantic prose analysis

**Sources:** `packaging/windows/verify-package.ps1`, `packaging/tests/verify_unix_package.py`

Acceptance verifies application startup, sidecar process presence, Unicode file preservation, process-tree behavior, and package layout, but it does not prove a packaged desktop/sidecar can load profiles and produce expected prose findings.

**Remediation:** add a semantic engine/package canary that checks profile enumeration and deterministic findings on a tiny fixture without weakening the existing launch/network/package checks.

### P2 — Tab-width setter validates the current value instead of the requested value

**Source:** `src/settings/appsettings.cpp`

`setTabWidth(int width)` checks `d->tabWidth` against bounds before assigning `width`. A currently valid setting therefore accepts an invalid new width; once invalid, the setter can refuse to recover.

**Remediation:** validate `width` itself and add/extend settings coverage if a suitable test harness exists.

### P2 — Backup files collide for documents sharing a basename

**Source:** `src/documentmanager.cpp`

Backups are stored as `<backup directory>/<filename>.backup`. Documents in different directories with the same filename overwrite the same backup target.

**Remediation:** derive collision-resistant backup identity from the canonical source path while retaining a human-recognizable basename. Migration/backward compatibility should not delete existing backups.

### P2 — Explicit non-loopback HTTP serving lacks an acknowledgement/auth boundary

**Sources:** `writer-engine/backend/cli.py`, `writer-engine/backend/main.py`

The normal server default is loopback, but the CLI accepts an arbitrary host such as `0.0.0.0` while the HTTP API itself has no authentication. This requires explicit user configuration, so it is not a default remote-exposure vulnerability, but it is an unsafe sharp edge for a local-first product.

**Remediation:** require an explicit remote-serving acknowledgement for non-loopback binds (or equivalent authenticated design) and keep the default loopback-only behavior.

### P2 — Corrupt user calibration data can fail otherwise valid analysis requests

**Source:** `writer-engine/backend/analyzers/calibration.py`

Baseline loading directly parses the user calibration JSON. A truncated/corrupt calibration file can propagate a JSON exception into analysis instead of degrading to unavailable calibration.

**Remediation:** fail closed to an empty/unavailable calibration baseline with a bounded diagnostic/log signal; do not corrupt or silently rewrite the user's source file.

### P3 — Bounded integer validation accepts booleans as integers

**Source:** `writer-engine/backend/validation.py`

Python's `int(True) == 1`, so Boolean JSON values can pass integer fields. Other validators in the same module reject Boolean-as-number behavior more explicitly.

**Remediation:** reject booleans in bounded integer validation and add schema regression coverage.

### P3 — Sentence/paragraph length statistics tokenize each segment twice

**Sources:** `writer-engine/backend/text_utils.py`, `writer-engine/backend/metrics.py`

Length comprehensions call `words(segment)` both in the condition and again for `len(...)`. Segment strings are not the root request string, so the root feature cache does not remove this duplicate regex work.

**Remediation:** tokenize each segment once. Keep only if benchmark/tests show no regression.

### P3 — Duplicate Windows supervisor implementation remains dead in `sidecar.py`

**Sources:** `writer-engine/backend/sidecar.py`, `writer-engine/backend/process_supervisor.py`

`sidecar.py` contains Windows Job Object helper definitions and later imports the same names from `process_supervisor`, replacing the local definitions. The duplicate implementation is dead and creates divergence risk.

**Remediation:** remove the dead duplicate after process-supervisor tests cover the retained implementation.

## Judge-rejected / not-yet-proven findings

The following are intentionally **not** implementation targets without stronger evidence:

- Broad metaphor, cliché, false-agency, or cinematic-fog heuristics being "too subjective." ThothPad's contract is observational, and false-positive taste disagreements are not automatically correctness defects.
- QProcess `write()` being partial in the current framed client. Qt buffers process writes and no concrete truncation path was established.
- The sidecar restart count not resetting after a successful restart. A one-restart-per-client policy can be an intentional crash-loop guard.
- Computing analyzers for hidden/report-only lenses. This may be deliberate precomputation for instant toggling; optimize only with measured evidence.
- Automatically forcing English when language detection is uncertain. That would replace one silent-error class with another and needs a product-level language override design.
- Removing the emergency `creative-default` runtime fallback outright. Stronger package validation and explicit health reporting are safer first steps.

## Deferred design work (not required for the first correctness remediation pass)

- Full profile schema versioning/migration instead of permissive JSON shape validation.
- Authentication design for intentionally remote HTTP deployments beyond the explicit-bind safety guard.
- A user-facing manual document-language override.
- Broader analyzer precision tuning based on a labeled human corpus rather than hand-picked examples.
- Refactoring all inherited Ghostwriter code solely for style/modernization when no defect or measured performance benefit is present.

## Implementation order

1. P0 save transaction integrity and tests.
2. P1 persisted diagnostic reconstruction and desktop analysis-disabled observability.
3. Deterministic P2 analyzer/UTF-16/manuscript correctness fixes.
4. Profile/presentation and packaging semantic-health fixes.
5. Data-recovery, remote-bind, calibration, settings, and low-risk robustness fixes.
6. Measurable P3 performance/dead-code cleanup.
7. Full exact-head CI and package/freeze gates.
8. Final diff-only judge pass; remove unnecessary changes and publish any remaining debt explicitly.
