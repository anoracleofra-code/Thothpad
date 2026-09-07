# Custom lens lists

Click **Edit lists…** beside Lenses, or right-click a lens and choose **Edit this
lens's lists…**. Select any of the 12 lenses in the editor.

- **List name** labels the list and its custom observations.
- **Flag these phrases** adds literal words/phrases, one per line.
- **Ignore these matches** suppresses exact flagged phrases for that lens,
  including built-in matches. It does not suppress a paragraph merely because
  the paragraph contains an ignored word.
- **Keep built-in checks** adds your phrases to the existing detectors. Uncheck
  it for custom-list-only findings. Analytical lenses (metaphor density, echoes,
  parts of speech, etc.) are not finite editable dictionaries; added phrases are
  literal matches, not grammatical or literary classifications.
- **Ignore quoted dialogue** excludes dialogue matches for the list. Existing
  analyzer-level dialogue exclusions still apply to built-in findings.

Matching is case-insensitive and whole-word bounded, with flexible whitespace.
Regular expressions are never evaluated. Ignore entries take precedence over
added entries. Exact duplicate built-in/custom spans are shown only once.
Lists still obey the lens's enabled state, timing mode, and rule thresholds.

Click **Save** to apply changes to the current profile. Change **Save profile as**
to a new name to save a separate collection; choose it later with the sidebar's
**Profile** selector. Names use 1–64 ASCII letters, digits, hyphens, or underscores.
The Writing tab edits the existing strong/soft profile-phrase lists and writing
preferences. Saving does not edit the manuscript.

**Export list…** saves just the selected lens list as versioned JSON; **Load
list…** imports it into the matching lens, with a replacement confirmation. This
only changes the editor draft until the profile is saved. To share/load an entire
collection, use **Tools → Export profile / Import profile**. Replacing an existing
named profile requires confirmation. JSON export is local; nothing is uploaded.
Recipients need a ThothPad version with custom-list support.

Each include/exclude list supports 500 entries of up to 256 characters. List
names support 128 characters; the full profile/import file limit is 256 KiB.
Oversized or invalid data is rejected without silently truncating editor text.
All writes use the existing atomic profile/file paths. API keys and manuscript
text are not added to list files.

Implementation: `lens_lists` in a profile maps lens IDs to `name`, `include`,
`exclude`, `use_builtin`, and `ignore_dialogue`. Single-list files wrap one entry
with `format: "thothpad-lens-list"`, `version: 1`, `lens`, and `list`.

Verification covers all 12 categories, literal metacharacters, Unicode/UTF-16,
exclusions, dialogue, score semantics, save/load/share round-trips, malformed
imports, oversized edits, and snapshot counts/findings/overlay consistency.
