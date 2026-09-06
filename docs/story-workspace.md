# Story Workspace

Story Intelligence now has an optional **Workspace…** editor above the chat box.
The regular writing workflow stays available without creating a project folder.

## Start here

1. Use **Scene Context → pencil** to fill in setting, goal, POV, location, time,
   conflict, stakes, intended outcome, established facts and notes.
2. Use **Characters → +** to create a character. The pencil edits the selected
   character; with none selected, it opens the agent library.
3. In **Workspace → Characters & agents**, create, edit, duplicate, archive or
   import profiles. Templates include Character, Co-Writer, Scene Architect,
   Continuity Editor and Custom Agent.
4. Under **Chapters & scenes**, choose a heading and **Assign agents** to set its
   co-writer and cast. **Inherit context** restores context inheritance.
5. Select **Manuscript**, **Current chapter**, or **Current scene** above chat.
   Chapter/scene context follows the cursor. Clicking a character card switches
   the speaking persona; clicking it again returns to the co-writer.

The highest heading level used in the document defines chapters. Current scene
uses the deepest enclosing heading. Headings inside fenced code are excluded by
the existing Markdown parser. A changed heading keeps its identity when its
opening passage uniquely matches; deleted/unmatched headings keep their records
as “needs relinking.” Use **Relink** to associate those records with another heading.

## Agents and model settings

Profiles have editable identity, soul/instructions, voice, knowledge/secrets,
goals, boundaries and canon reference notes. Model overrides are optional.
A model-only override uses the current provider. A provider override also needs
an endpoint and model; credentials are looked up in the OS credential store for
that exact provider/host/model identity. Configure those credentials in Model
Settings. API keys and executable configuration are excluded from agent exports.

The default permission is **Read, navigate and suggest**. You can enable
**Request edits with confirmation** for an agent. Native permissions, exact-text
checks, recovery checkpoints and Undo remain authoritative. Imported souls cannot
grant themselves additional permissions or run shell commands.

## Memory and conversations

Every chat is saved locally. The dropdown below **Co-Writer Chat** lists the
manuscript's conversations with their title, heading and agent. **New** starts
another independent session, including for the same chapter and agent; empty
sessions are saved too. The first message supplies the conversation's title.
Selecting a session restores its history and scope, navigating to that heading.
Returning to a chapter restores its last selected conversation. A missing heading
or changed agent assignment must be restored in Workspace before resuming that
conversation, so private history cannot silently pass to a different agent.
**Workspace → Saved conversations** also exports to Markdown or deletes chats.
The scope dropdown keeps stable Manuscript / Current chapter / Current scene
choices. The heading beneath it follows the cursor and identifies the actual
chapter or scene. A scene is a heading nested below a chapter. If there is no
scene heading at the cursor, the panel explicitly says so and shows the chapter
or manuscript context being used; it does not label a chapter as a scene.
The model receives the latest 16 messages; the saved conversation retains all
messages within the workspace size limit.

Each chat message has **Copy**, **Edit**, **Retry/regenerate**, and **Delete**
controls. Editing a user prompt resends it; editing an AI response saves your
version without calling the model. Edits and retries create a named branch in
the conversation picker, keeping the original conversation intact. Regeneration
uses history before the selected prompt, not later replies. The same engine,
provider credentials and manuscript-edit approval rules apply to these requests.
Failed requests can be retried from their error card or the user prompt.
Deletion asks for confirmation, updates saved history and removes marks owned
by that message; it does not undo applied manuscript edits or approved memories.
Mutation controls are disabled while a response is in progress. A save conflict
leaves the original history unchanged instead of silently overwriting the file.

**Save as memory…** under a message opens a review form. AI-generated memory,
scene and character proposals also have review buttons. Nothing proposed by the
model becomes approved memory automatically.

Memory types: canon, core identity, private knowledge, author preference and
session note. Records have proposed/approved/rejected status. The Memories tab
lets you edit the owner and manuscript/chapter/scene scope. Only approved records
from the active scope and its ancestors are supplied to the selected agent.
Private records require an owner. Session notes only apply to their conversation.
Switching agents starts a fresh conversation so private chat history is not
silently transferred. The cast supplies public identity/voice information;
another character's private knowledge is not included in the speaker's profile.

## Manuscript references and rewrites

AI annotations produce clickable passage links in chat and manuscript mark cards.
The native app verifies quoted text and UTF-16 offsets before navigation.
**Apply** opens an original/replacement comparison; acceptance uses the existing
checkpointed transaction and standard Undo.

Marks are saved with the manuscript content hash. If the text changes, the mark
is retained as stale and its navigation/apply actions are disabled. Undoing back
to the identical manuscript can restore a mark's validity; a new AI review can
supply fresh marks. Dismiss and Clear marks remove saved marks without altering
the manuscript.

The agent can request bounded native tools to inspect the active story context,
list headings, read a scope in pages, search exact manuscript phrases, navigate,
inspect prose lenses, and request allowed edits. Tool failures and denied edits
remain explicit execution results.

## Sharing with Buzz users

**Import** accepts unlocked Buzz v1 `.agent.json`, `.agent.png` cards containing
the `buzz_agent_snapshot` metadata chunk, and Markdown souls such as `SOUL.md`.
Imported fields open in an editable preview. Optional imported memories start as
proposals. Locked/encrypted cards must first be exported unlocked from Buzz.

**Export** writes Buzz-compatible `.agent.json`. Choose no memory (default), core
memory, or all approved memories owned by that agent. The export contains plaintext
profile/memory content. Structured ThothPad character fields are appended to the
portable system prompt. Buzz runtime/process settings and credentials are not
imported. No personal soul templates are bundled into ThothPad.

## Storage and recovery

For `novel.md`, metadata lives beside it in
`.thothpad/novel.md.story.json` (schema version 2). Move this sidecar with the
manuscript to move its workspace. Save As copies the workspace when the
destination has no workspace; an existing destination workspace is preserved.
Unsaved drafts use an application-data `story-workspaces/draft-<id>.json` file.

The optional project folder supplies reference retrieval and existing recovery
checkpoint context. It is not required to edit scene/character information.
Legacy project scene/character metadata is imported into the first manuscript
workspace without rewriting the legacy file.

Writes use QSaveFile and a content-hash conflict check. Unsupported/corrupt
workspace files are preserved and made read-only. Conflicting external changes
are reported rather than overwritten. Workspace files are capped at 32 MB;
agent imports at 16 MB. Export/remove old conversations if the workspace fills.

The left observation toolbar is pinned below its scrollable content. Lens and
finding lists adapt to available height; long explanations scroll inside their
card. On unusually short windows the content can still scroll while action
buttons remain visible.

## Verification

Native workspace tests cover migration, atomic writes, conflict/corrupt-file
handling, heading identity, inheritance, memory isolation, Buzz JSON/PNG imports
and scene/agent forms. Integrated Qt tests exercise real controller wiring,
agent/session isolation, chapter context, model proposals, saved conversations,
Save As and checkpointed rewrite/Undo. Visual tests exercise the themed layout
and both resizable columns. Python tests verify prompt propagation, credential
exclusion, private-memory filtering and proposal validation without contacting
a live provider.
