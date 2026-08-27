# Story Intelligence — Implementation Plan

Status: active implementation branch (`feature/story-intelligence`)

## Product contract

ThothPad keeps a deliberate three-part mental model:

- **Left — Prose Intelligence:** deterministic observations about what the prose is doing.
- **Center — Manuscript:** the writer's source of truth.
- **Right — Story Intelligence:** project-aware, conversational, generative assistance that can observe and operate ThothPad through a bounded native tool harness.

The visual reference is the `thothpad-ui-redesign` shell supplied by the project owner. The native implementation reproduces its information architecture and visual rhythm in Qt rather than embedding the React shell.

Story Intelligence is not merely a chatbot beside the editor. **ThothPad itself is the agent harness.** The co-author can read structured editor/prose/project state and invoke allowlisted native operations: navigate to a finding, toggle dark mode, show/hide panes, select text, indent/unindent, run analyses, add annotations, and execute explicitly authorized editing transactions.

## Non-negotiable invariants

1. **The manuscript remains authoritative.** The agent may mutate text only inside a scoped, reversible transaction authorized by the current user request or by an explicit Apply/Confirm action. It may never silently edit in the background.
2. **Every agent text mutation is recoverable.** Before the first write in a transaction ThothPad creates a durable pre-edit checkpoint; the entire batch is also one normal editor undo step.
3. **Credentials never enter project files, logs, prompts, or plain QSettings.** API keys use the existing OS credential store.
4. **Project access is bounded to a folder explicitly selected by the user.** Retrieval must not escape that root through traversal or symlinks.
5. **Remote model use is explicit.** Local deterministic analysis remains available with no model configured.
6. **AI annotations are a separate overlay channel.** They must coexist with spelling/prose overlays and be disposable without modifying document text.
7. **Suggestions are revision-safe.** A proposed replacement records the source text/hash/revision and is rejected or revalidated if the document has changed.
8. **Character simulation is labeled as simulation.** Improvised character replies are not silently promoted to canon.
9. **The right rail must collapse independently from the left rail and HTML preview.** Editor width remains usable on smaller screens.
10. **No second heavy analysis sidecar.** Story Intelligence reuses the existing writer-engine process/client owned by the prose layer.
11. **Every model response is treated as untrusted data.** Structured envelopes and tool calls are validated and bounded before they reach native application state.
12. **The model never receives arbitrary Qt/object access.** It sees a declarative capability manifest and structured state snapshots; the desktop owns authorization and execution.
13. **Human corrections become agent feedback.** Undoing an agent transaction or materially editing/deleting one of its targets creates a bounded activity event that can be fed into later turns.
14. **Activity tracking is local and selective.** ThothPad must not flood prompts with raw keystrokes; it records meaningful edit events, hashes/ranges, and short excerpts only.

## Reference-shell mapping

| Reference shell | Native ThothPad implementation |
| --- | --- |
| 320 px right sidebar | `StoryIntelligenceWidget`, native right dock/pane matching the left content pane |
| Story Intelligence header | Native header frame with collapse control |
| Model / API Key | Existing provider settings + `CredentialStore`; provider summary in rail |
| Project folder / Open | Explicit folder picker, persisted project root, bounded project retrieval |
| Scene Context | Portable project metadata: setting, goal, POV, location/time, conflict, notes |
| Characters | Persistent character cards; active character may become chat persona |
| Co-Writer Chat | Structured Story Intelligence request envelope with bounded history and project context |
| Colored manuscript marks | Dedicated `story-intelligence` text-format overlay channel |
| Rewrite ideas | Structured annotations with optional replacement and transactional Apply |
| App manipulation | Native `StoryToolHarness` over allowlisted `QAction`s/editor/prose APIs |
| Undo awareness | `DocumentActivityTracker` + agent transaction journal |

## Architecture

```text
QMainWindow
├── Left Sidebar / Prose Intelligence
├── Center Workspace / MarkdownEditor
└── StoryIntelligenceWidget
        │
        └── StoryIntelligenceController
              ├── StoryToolHarness
              │    ├── read-only app/prose/editor snapshots
              │    ├── QAction-backed UI commands
              │    ├── navigation/selection commands
              │    ├── annotation commands
              │    └── transactional edit commands
              ├── AgentEditTransactionManager
              │    ├── durable pre-edit checkpoint
              │    ├── one undo edit block per transaction
              │    ├── source/hash/revision verification
              │    └── optional immediate autosave after success
              ├── DocumentActivityTracker
              │    ├── detects undo of agent actions
              │    ├── detects meaningful user changes to agent targets
              │    └── emits bounded structured feedback events
              ├── project metadata/retrieval state
              └── provider/chat state

writer-engine
└── Story Intelligence pipeline
      ├── receives app/prose/project snapshot + capability manifest
      ├── retrieves bounded relevant project snippets locally
      ├── calls configured provider
      ├── validates response/tool-call envelope
      ├── resolves exact quote spans to UTF-16 offsets
      └── returns message + annotations + requested native tool calls
```

The desktop is the authority for native actions. The engine/model may request a tool; only `StoryToolHarness` can decide whether it exists, whether it is allowed in the current request, and whether it requires checkpoint/confirmation.

## Native tool contract

The model receives a small declarative manifest rather than raw implementation names. Each tool has an ID, schema, risk class, side-effect description, and whether a direct user instruction may authorize it.

### Risk classes

- **R0 — observe:** no mutation. Examples: editor state, selection, cursor, prose counts/findings, current theme/pane state.
- **R1 — reversible UI/navigation:** no manuscript mutation. Examples: dark/light mode, collapse/show panes, focus editor, navigate/select a range.
- **R2 — annotate/analyze:** transient overlays or analysis operations. Examples: highlight a passage, run prose scan, run grammar scan, navigate to a prose finding.
- **R3 — bounded edit:** manuscript mutation over an exact verified range. Requires pre-edit checkpoint and one grouped undo command. Examples: replace selected sentence, correct a verified grammar finding.
- **R4 — bulk edit:** many ranges or whole-document structural changes. Requires explicit scope in the user's current instruction or an additional confirmation; always checkpointed and grouped into a single undo transaction.
- **R5 — destructive/external:** deleting project files, arbitrary shell commands, arbitrary network/filesystem access. Not exposed in the first production release.

### Initial tool families

**Application/UI**
- `get_app_state`
- `set_theme(light|dark)`
- `set_panel_visible(left|story|preview, bool)`
- `focus_editor`

**Editor/navigation**
- `get_editor_state`
- `get_selection`
- `select_range`
- `go_to_range`
- `indent_selection`
- `unindent_selection`
- `indent_document` / `unindent_document` (R4)

**Prose Intelligence**
- `get_prose_summary`
- `get_prose_findings(category?, limit?)`
- `run_prose_scan(scope)`
- `go_to_prose_finding(id)`
- `set_prose_lens_enabled(category, bool)`

**Grammar/revision**
- `run_grammar_review(scope)`
- `apply_verified_replacements` (R3/R4 depending count/scope)
- `replace_verified_range`

**Story Intelligence presentation**
- `add_ai_annotations`
- `clear_ai_annotations`
- `select_annotation`

**Project/story**
- `search_project`
- `read_project_note`
- `get_scene_context`
- `get_character`
- `propose_scene_context_update`
- `propose_character_update`

## Agent request/response loop

A single user turn may require more than one native operation. The loop is bounded (default maximum four tool rounds):

1. Desktop captures a structured snapshot of current app/editor/prose/project state.
2. Engine sends snapshot + user request + tool manifest to the model.
3. Model returns prose plus zero or more validated tool requests.
4. Desktop authorizes and executes allowlisted tools.
5. Tool results are appended as structured observations.
6. If the model requested continuation and the round limit is not reached, a follow-up model turn receives those results.
7. Final response is displayed in chat with a concise action summary.

Example:

> **User:** “Highlight and go to the section where you said the pacing dropped and tell me what I should do.”

The model can use the already supplied prose snapshot or call `get_prose_findings`, request `add_ai_annotations` and `go_to_range`, then explain the issue after receiving the tool results.

Example:

> **User:** “Correct all the grammar mistakes and tell me what you did.”

The direct imperative authorizes the requested grammar-edit scope. ThothPad runs grammar review, verifies every source range, writes a durable checkpoint, applies accepted machine-verifiable fixes from the end of the document backward inside one edit block, schedules a safe save, and returns a structured change summary. One Undo reverses the entire agent transaction.

## Edit transaction and recovery design

Agent writes use a dedicated transaction manager instead of directly calling `QTextCursor::insertText` ad hoc.

Before mutation:

- capture document path, revision, cursor/selection and SHA-256;
- verify all target quotes/ranges against the current document;
- write a durable pre-edit snapshot atomically;
- record operation/tool IDs and a human-readable summary.

Mutation:

- sort independent replacements from highest offset to lowest;
- wrap the whole operation in `QTextCursor::beginEditBlock()` / `endEditBlock()`;
- reject overlapping or stale edits;
- preserve normal QTextDocument undo/redo semantics.

After mutation:

- record after-hash/revision and affected ranges;
- if normal autosave is enabled, schedule an immediate post-transaction save rather than waiting for the one-minute timer;
- retain the pre-edit checkpoint even if autosave subsequently writes the changed document;
- add an activity event summarizing what the agent changed.

Recovery checkpoints are rolling and bounded by count/size. They are separate from normal file backups so a rapidly autosaved bad AI edit cannot erase the last known-good in-memory state.

## Human activity feedback

`DocumentActivityTracker` keeps a rolling local text snapshot and listens to document changes. It does **not** serialize every keystroke into chat.

High-value events include:

- `USER_UNDID_AGENT_TRANSACTION` — current hash matches the pre-edit hash of a recent agent transaction;
- `USER_REDID_AGENT_TRANSACTION`;
- `USER_EDITED_AGENT_TARGET` — human edit overlaps a recent AI replacement/annotation;
- `USER_DELETED_TEXT` — meaningful deletion above a configurable length threshold;
- `USER_REPLACED_TEXT` — meaningful replacement with short before/after excerpts;
- `USER_REJECTED_SUGGESTION` / `USER_ACCEPTED_SUGGESTION`.

Events carry document revision, line/range where practical, short bounded excerpts, and the related agent operation ID. They can appear as subtle event chips in the Story Intelligence transcript and are included in later model context so the co-author learns from the writer's decisions.

The agent should interpret these as evidence of preference, not absolute global rules. Example: undoing one adverb-deletion pass does not mean “never remove adverbs again”; it means the previous action was rejected and future suggestions should account for that.

## Phase 1 — Native shell and layout

- Add `StoryIntelligenceWidget` with the reference hierarchy: provider card, project card, scene context, characters, manuscript marks, chat history, fixed composer.
- Add `StoryIntelligenceController` and project metadata model.
- Use an independent right-side native pane/dock so the existing center editor/preview splitter stays stable.
- Persist visibility and project folder.
- Extend `widgets.qss` so the right rail uses the same theme variables and visual grammar as Prose Awareness.

Acceptance:
- Reference proportions are preserved at desktop widths.
- Toggling left/right rails or preview does not lose the editor.
- Theme changes update both rails.

## Phase 2 — Secure provider and project state

- Reuse provider settings and OS credential store.
- Add Gemini as a first-class provider alongside OpenAI-compatible, Anthropic, Ollama, LM Studio, llama.cpp, etc.
- Persist scene and character metadata under `.thothpad/story-intelligence.json` with atomic writes; never store credentials there.
- Load/save project metadata only within the selected root.

Acceptance:
- API key is stored only through QtKeychain.
- Reopening a project restores scene/character state.
- Corrupt project metadata degrades safely and does not block editing.

## Phase 3 — Project-aware chat and structured output

Story Intelligence currently routes through the stable existing writer-engine request channel with a validated Story Intelligence envelope rather than creating a second sidecar or exposing Qt to Python.

Request context contains:

- user prompt;
- current document text/hash/revision;
- bounded chat/activity history;
- selected project root;
- scene context;
- character records;
- optional active character/persona;
- app/prose state snapshot;
- capability manifest;
- provider config plus credential supplied only for the explicit operation.

The engine retrieves only relevant text files under the selected root using bounded lexical scoring. It caps file count, per-file bytes, total retrieved bytes, and extensions. Symlink/path escape is rejected.

Response envelope may contain:

```json
{
  "message": "...",
  "annotations": [
    {
      "quote": "exact text from current document",
      "category": "continuity|voice|pacing|idea|rewrite|research",
      "comment": "...",
      "replacement": "optional replacement"
    }
  ],
  "tool_calls": [
    {
      "id": "go_to_range",
      "arguments": {"start_utf16": 120, "end_utf16": 180}
    }
  ],
  "continue": false,
  "scene_context_proposal": {},
  "character_proposals": []
}
```

For manuscript annotations, the model supplies exact quotes where possible; the engine/desktop—not the model—owns authoritative span validation.

Acceptance:
- A chat message can use current-document, app/prose and relevant project context.
- Remote calls happen only for explicit Story Intelligence/model operations.
- Invalid/oversized model output or tool requests are rejected without crashing the desktop.

## Phase 4 — Manuscript annotations and suggestions

- Dedicated overlay channel `story-intelligence`.
- Map categories to reference colors: amber, rose, blue, green plus theme-safe variants.
- Tooltip contains agent/category/comment and optional proposed replacement.
- Right rail exposes annotation cards and navigation.
- Replacement is applied only through the transaction manager and only if source preconditions still match.
- Clearing a conversation may optionally clear its annotations without changing text.

Acceptance:
- AI highlights coexist with spelling and prose lenses.
- Editing the document does not cause stale replacements to be applied to shifted text.
- One normal Undo reverses an accepted agent replacement/batch.

## Phase 5 — Native app tool harness

- Build `StoryToolHarness` as the sole executor of model-requested native commands.
- Reuse `AppActions` for commands already represented by the UI so human and AI paths have identical behavior.
- Add read-only snapshot adapters for `ProseController`, editor state, application state and Story Intelligence state.
- Add R0–R4 authorization policy and bounded multi-round tool execution.
- Never expose arbitrary action names, QObject methods, paths or shell execution to the model.

Acceptance:
- “Switch to dark mode” uses the normal app action/settings path.
- “Collapse the left panel” changes the same state as the human control.
- “Show me the adverb problem” can read prose counts/findings, navigate and highlight without editing.
- “Indent the entire document” is one checkpointed/undoable R4 transaction.

## Phase 6 — Agent edit safety and human feedback

- Add durable `AgentEditTransactionManager` checkpoints.
- Group every AI mutation into one undo command.
- Immediate post-agent autosave when normal autosave is enabled.
- Add `DocumentActivityTracker` and bounded agent-event journal.
- Emit visible transcript events for undo/redo/rejection of agent actions; keep ordinary keystrokes internal unless materially relevant.
- Feed recent events into future model context.

Acceptance:
- A bad whole-document agent edit can be reversed with one Undo.
- A pre-edit snapshot remains recoverable even if autosave writes the bad result.
- Agent is informed when its recent edit is undone/rejected.
- Meaningful user edits can inform later suggestions without uploading raw edit history.

## Phase 7 — Character agents and story state

- Character card may be selected as the active persona.
- Persona prompt is grounded in the stored character record plus retrieved project facts.
- UI visibly labels character replies as simulation.
- Agent may propose (not silently commit) scene/character metadata updates.
- Add contextual actions: ask character, check voice, check knowledge, inspect selected passage.

Acceptance:
- Switching persona changes only subsequent requests.
- Generated claims do not become canon until accepted into project metadata.

## Phase 8 — Hardening, tests, and release documentation

Native tests:
- layout/visibility persistence;
- metadata load/save and corrupt-file handling;
- credential ID/provider summary behavior;
- tool manifest/risk authorization;
- app-action routing;
- checkpoint creation/pruning;
- grouped undo and stale-edit rejection;
- activity-event detection;
- annotation span safety and replacement preconditions.

Engine tests:
- project-root containment/symlink escape;
- retrieval caps;
- story response/tool-call schema validation;
- duplicate/ambiguous quote handling;
- Gemini request/response parsing;
- remote-consent policy;
- UTF-16 annotation offsets;
- tool-round and output-size bounds.

Release gates:
- Python `ruff`, `mypy`, pytest;
- native CTest Core + Full;
- exact-head GitHub Actions matrix;
- no new plaintext-secret paths;
- no unrestricted filesystem/shell/native-object tool;
- docs/third-party notices updated for any new runtime dependency (the native implementation does not require the React shell or `@google/genai`).

## Deliberate deferrals

These are valuable but should not block the first production-ready Story Intelligence slice:

- true inline ghost-text widgets rendered between document lines;
- arbitrary autonomous project-file mutation;
- embedding/vector database dependency (bounded lexical retrieval ships first);
- background agent actions while the user is not explicitly interacting;
- automatic canon mutation;
- unrestricted shell/terminal/code-execution tools.

The architecture keeps richer agent behavior possible without sacrificing the manuscript-first safety model.