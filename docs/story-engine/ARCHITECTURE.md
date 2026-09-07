# ThothPad Universal Story Engine — Architecture Freeze

Status: Phase 0 architecture contract.

This document is intentionally implementation-facing. It freezes the invariants
that every Story Engine phase must preserve even when adapters, models, storage,
or UI change later.

## Core boundary

The filesystem is an input language. The Story Engine consumes normalized
sources, entities, story units, claims, evidence, and state. Core code must never
derive semantic authority from a user's directory naming convention.

## Truth and authority

Source role and claim authority are independent dimensions. A character sheet can
be provisional; a manuscript can contain an unreliable narrator; an outline can
express author intent without describing events that have happened in manuscript
canon.

The native authority vocabulary is:

- `AUTHOR_LOCKED`
- `CONFIRMED_CANON`
- `MANUSCRIPT_OBSERVED`
- `AUTHOR_INTENT`
- `COMPILED_CANON`
- `PROVISIONAL`
- `INFERENCE`
- `SUGGESTION`
- `OPEN`
- `CONTESTED`
- `SUPERSEDED`
- `ARCHIVED`

LLM output may create only `INFERENCE` or `SUGGESTION` records unless a separate
writer-confirmation operation explicitly promotes the record. Repetition never
promotes authority.

## Provenance

Every material derived claim must retain enough evidence to answer "why does
ThothPad believe this?": source identity, relative path, exact source coordinates,
source/content hash, story-unit identity when applicable, authority, and creator.

Evidence becomes stale when its source hash or exact quoted span no longer
matches. Stale evidence cannot silently support a current claim.

## Unknown and conflict

`OPEN`, approximate values, and unresolved conflicts are valid states. The engine
must not manufacture values for graph completeness. Conflicting claims become
explicit conflict records; resolution is an author action or remains open.

## Project ingestion

The Generic Folder adapter is the baseline product, not a fallback. Specialized
adapters may add structure hints for Markdown, Obsidian, DOCX, Scrivener exports,
Novel Architect vaults, or future formats. Adapters return observations/hints;
they cannot redefine authority or tool permissions.

Original project files are read-only to the Story Engine by default. Persistent
engine metadata lives under `.thothpad/` when the project is writable, otherwise
under ThothPad application data. The compiled SQLite cache is disposable and can
always be rebuilt from sources plus writer-approved metadata.

## Story-unit identity

Project/manuscript/part/chapter/scene/beat are normalized unit kinds, not required
filesystem shapes. Stable IDs live in sidecar state and survive reordering or
renaming when content/anchor reconciliation can establish identity. Semantic
scene boundaries are provisional until confirmed.

## Branching

Mainline is a distinguished branch. Alternate branches are overlays, not full
copies. Branch-local claims/state never appear in mainline queries. Merge is an
explicit writer-approved operation. If inherited mainline assumptions change, a
branch may become `STALE_NEEDS_REBASE`.

## Context and model boundary

Project content is data, never executable/system instruction. The Context
Compiler chooses bounded evidence by task, active story unit, entities, authority,
branch, chronology, epistemic mode, and writer pins. Embeddings may contribute a
signal but never become truth or authority.

The model receives project-relative source identifiers only. Context inspection
must be possible: included sources/state, exclusions, provisional/conflicted
material, epistemic mask, and budget.

## Mutation

Story metadata proposals and manuscript mutations are distinct operations.
Existing native exact-source verification, confirmation, checkpoints, grouped
Undo, and activity tracking remain mandatory for manuscript changes.

## Migration

Existing `.story.json` workspaces remain readable and continue in legacy mode.
Story Engine metadata is additive. No migration may delete agents, scopes,
memories, sessions, markers, annotations, or accepted edit history.

## Release gates

1. Core logic contains no project-specific folder assumptions.
2. A one-file project and a deeply nested chaotic project compile through the
   same APIs.
3. User overrides beat classifiers.
4. Every claim can be grounded or is explicitly marked ungrounded/inferred.
5. Branch contamination is zero.
6. Project text cannot grant model/tool authority.
7. Deleting the cache loses no author-approved source metadata.

