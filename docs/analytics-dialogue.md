# Analytics and Dialogue

The native writing sidebar has **Tools**, **Analytics**, and **Dialogue** buttons in its header. Tools is a plain tab; right-click it for the existing profile, export and settings menu. The observation detail area uses the available height, and its compact action toolbar stays visible. Long lists and explanations scroll within their own areas.

## Analytics

Choose the whole manuscript, the current chapter/scene, or a specific heading. Chapters and scenes use the same heading hierarchy as Story Intelligence. A missing scene is explicitly shown as missing and produces no scene results; it is not relabelled as a chapter.

Reports include word counts, dialogue proportion, unknown speakers, explicit tags, estimated sentence lengths and their distribution, English Coleman–Liau readability, long-word proportion, repeated words and three-word phrases, chapter comparisons, and character dialogue measurements. Expand a character to compare average line lengths between chapters and inspect repeated vocabulary. These are descriptive measurements, not a universal quality score or a diagnosis of voice inconsistency. Readability is withheld below 100 words.

Double-click a sentence, a repetition occurrence, or a chapter row to select its manuscript passage. Navigation checks that the document still matches the analyzed snapshot. Repeated phrases exclude punctuation boundaries; repetition drilldowns show up to 200 occurrences per phrase. Rankings show the 40 most frequent entries.

Lens Summary explicitly displays the latest **document** lens counts from Tools, not scoped Analytics counts. Run Scan document to update those counts. Other reports run locally without an API key or a running prose engine. Markdown headings and fenced/inline code are excluded from the new prose word counts, so these counts can differ from the editor's whole-document status bar.

Snapshot stores the current scope's measurements for revision comparison (up to 200 snapshots per workspace). Export writes the scoped report, including passage offsets and dialogue records, as JSON.

## Dialogue

Dialogue supports straight/curly single and double quotation marks and excludes contractions, fenced code and inline code. English speech tags are matched against saved character names and user-entered aliases, including intervening adverbs and narration. Named action beats and same-paragraph continuation can infer a speaker; the tooltip identifies inference separately from explicit tags. Unregistered names found in narration appear as **Detected** speakers and can be saved as character cards. Names inside dialogue are not used as speaker evidence. Blank lines and indented ebook paragraphs end a turn, while physical line wrapping does not. Pronoun-only or conflicting evidence stays Unknown. This remains a deterministic aid, not perfect coreference resolution: confirm uncertain speakers yourself.

Filter by scope, character, or search text. Each row shows full wrapped dialogue. Its pencil opens an inline editor with Save/Cancel; the magnifying glass selects the exact quote in the manuscript. Click the speaker name to correct its assignment; hover for attribution status. There is no duplicate manuscript pane. Choose a saved character and use Aliases to add short names or nicknames used in tags. An inline draft remains visible if the manuscript changes, but saving is blocked until you cancel and refresh.

Chat context offers only Manuscript and Current chapter. Older scene conversations remain saved in Workspace; start a chapter conversation to use the new context selection. Manually authored setting/context forms are retained. Model/API settings and project-folder selection occupy separate cards.

Edit line changes only the words inside its quotation marks. Find / replace in these lines previews case-sensitive changes in the currently filtered dialogue. Applying a replacement uses the existing native transaction system: exact source checks, a recovery checkpoint, read-only protection, and one grouped Undo operation. If the document changes while an edit or preview is open, the operation is rejected. Manual speaker assignments are carried over when editing through Dialogue and retained for Undo.

Assignments and aliases live in the existing manuscript `.story.json` workspace, alongside characters and scene context. Assignment anchors include nearby text, so unrelated edits can preserve them; ambiguous duplicate anchors are never used to assign multiple lines accidentally. Edits to surrounding context outside Dialogue may require reconfirmation. Character creation, assignments and snapshots use the workspace's existing conflict-aware atomic save.

## Verification

Native tests cover UTF-16 offsets (including emoji), quote conventions, code exclusions, scope separation, speaker inference and correction, ambiguous anchors, scoped measurements, tab navigation, aliases on disk, character-filtered bulk editing, grouped Undo and stale-edit rejection. Visual integration tests render the themed Tools, Analytics and Dialogue surfaces.

Genre/author corpus comparisons and automatic AI voice critiques are not provided by these local reports. They require a separately chosen reference corpus or an explicit model request through Co-Writer Chat.
