# Story Intelligence — Implementation Plan

Status: active implementation branch (`feature/story-intelligence`)

## Product contract

ThothPad keeps a deliberate three-part mental model:

- **Left — Prose Intelligence:** deterministic observations about what the prose is doing.
- **Center — Manuscript:** the writer's source of truth.
- **Right — Story Intelligence:** project-aware, conversational, generative assistance that may annotate and propose changes but does not silently rewrite the manuscript.

The visual reference is the `thothpad-ui-redesign` shell supplied by the project owner. The native implementation reproduces its information architecture and visual rhythm in Qt rather than embedding the React shell.

## Non-negotiable invariants

1. **The manuscript remains authoritative.** AI output cannot mutate text without an explicit user action.
2. **Credentials never enter project files, logs, prompts, or plain QSettings.** API keys use the existing OS credential store.
3. **Project access is bounded to a folder explicitly selected by the user.** Retrieval must not escape that root through traversal or symlinks.
4. **Remote model use is explicit.** Local deterministic analysis remains available with no model configured.
5. **AI annotations are a separate overlay channel.** They must coexist with spelling/prose overlays and be disposable without modifying document text.
6. **Suggestions are revision-safe.** A proposed replacement records the source text/revision and is rejected or revalidated if the document has changed.
7. **Character simulation is labeled as simulation.** Improvised character replies are not silently promoted to canon.
8. **The right rail must collapse independently from the left rail and HTML preview.** Editor width remains usable on smaller screens.
9. **No second heavy analysis sidecar.** Story Intelligence reuses the existing writer-engine process/client owned by the prose layer.
10. **Every model response is treated as untrusted data.** Structured envelopes are validated and bounded before they reach the UI.

## Reference-shell mapping

| Reference shell | Native ThothPad implementation |
| --- | --- |
| 320 px right sidebar | `StoryIntelligenceWidget`, fixed preferred width matching the left content pane |
| Story Intelligence header | Native header frame with collapse control |
| Model / API Key | Existing provider settings + `CredentialStore`; provider summary in rail |
| Project folder / Open | Explicit folder picker, persisted project root, bounded project retrieval |
| Scene Context | Portable project metadata: setting, goal, POV, location/time, conflict, notes |
| Characters | Persistent character cards; active character may become chat persona |
| Co-Writer Chat | Structured `story_chat` engine operation with bounded history and project context |
| Colored manuscript marks | Dedicated `story-intelligence` text-format overlay channel |
| Rewrite ideas | Structured annotations with optional replacement; accept/reject remains user-controlled |

## Architecture

```text
QMainWindow
├── Left Sidebar (existing)
├── Center Workspace
│   ├── MarkdownEditor + BreathMap
│   └── optional HTML Preview
└── StoryIntelligenceWidget
        │
        └── StoryIntelligenceController
              ├── reuses ProseController::engineClient()
              ├── reuses ProseController::credentialStore()
              ├── project metadata persistence
              ├── provider/project state
              ├── chat request state
              └── AI overlay/suggestion state

writer-engine
└── story_chat
      ├── validates project root and request bounds
      ├── retrieves bounded relevant project snippets locally
      ├── builds context-aware prompt
      ├── calls configured provider
      ├── validates structured response envelope
      └── resolves exact quote spans to UTF-16 offsets
```

## Phase 1 — Native shell and layout

- Add `StoryIntelligenceWidget` with the reference hierarchy: provider card, project card, scene context, characters, chat history, fixed composer.
- Add `StoryIntelligenceController` and project metadata model.
- Change the main layout from `left | editor | optional preview` to `left | center-workspace | right`, where the center workspace owns editor + optional preview.
- Right rail collapse/restore is independent from the existing left rail.
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

## Phase 3 — Project-aware chat

Add `story_chat` to the sidecar protocol.

Request envelope contains:

- user prompt;
- current document text and revision;
- bounded chat history;
- selected project root;
- scene context;
- character records;
- optional active character/persona;
- provider config plus credential supplied only for the explicit operation.

The engine retrieves only relevant text files under the selected root using bounded lexical scoring. It must cap file count, per-file bytes, total retrieved bytes, and extensions. Symlink/path escape is rejected.

Response envelope:

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
  "scene_context_proposal": {},
  "character_proposals": []
}
```

The model supplies exact quotes; the engine—not the model—resolves them to UTF-16 offsets.

Acceptance:
- A chat message can use current-document and relevant project context.
- Remote calls happen only when the user sends a message.
- Invalid/oversized model output is rejected without crashing the desktop.

## Phase 4 — Manuscript annotations and suggestions

- Add dedicated overlay channel `story-intelligence`.
- Map categories to reference colors: amber, rose, blue, green plus theme-safe variants.
- Tooltip contains agent/category/comment and optional proposed replacement.
- Right rail exposes annotation cards and navigation.
- Replacement is applied only after explicit acceptance and only if the source span still matches the recorded source text/hash/revision.
- Clearing a conversation may optionally clear its annotations without changing text.

Acceptance:
- AI highlights coexist with spelling and prose lenses.
- Editing the document does not cause stale replacements to be applied to shifted text.
- Undo works for an accepted replacement through the editor's normal undo stack.

## Phase 5 — Character agents and story state

- Character card may be selected as the active persona.
- Persona prompt is grounded in the stored character record plus retrieved project facts.
- UI visibly labels character replies as simulation.
- Agent may propose (not silently commit) scene/character metadata updates.
- Add contextual actions: ask character, check voice, check knowledge, inspect selected passage.

Acceptance:
- Switching persona changes only subsequent requests.
- Generated claims do not become canon until accepted into project metadata.

## Phase 6 — Hardening, tests, and release documentation

Native tests:
- layout/visibility persistence;
- metadata load/save and corrupt-file handling;
- credential ID/provider summary behavior;
- annotation span safety and replacement preconditions.

Engine tests:
- project-root containment/symlink escape;
- retrieval caps;
- story response schema validation;
- duplicate/ambiguous quote handling;
- Gemini request/response parsing;
- remote-consent policy;
- UTF-16 annotation offsets.

Release gates:
- Python `ruff`, `mypy`, pytest;
- native CTest Core + Full;
- exact-head GitHub Actions matrix;
- no new plaintext-secret paths;
- docs/third-party notices updated for any new runtime dependency (the native implementation does not require the React shell or `@google/genai`).

## Deliberate deferrals

These are valuable but should not block the first production-ready Story Intelligence slice:

- true inline ghost-text widgets rendered between document lines;
- autonomous multi-step tool execution that can write many files;
- embedding/vector database dependency (bounded lexical retrieval ships first);
- background agent actions while the user is not explicitly interacting;
- automatic canon mutation.

The architecture keeps all of these possible without requiring them for the safe first release.
