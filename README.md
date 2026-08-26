# ThothPad

**A prose-aware writing studio. See what your prose is doing.**

ThothPad is a **local-first Markdown writing and revision studio** built from [KDE Ghostwriter](https://invent.kde.org/office/ghostwriter). It keeps Ghostwriter's distraction-free editor, preview, focus tools, themes, statistics, and cross-platform desktop experience, then adds a dedicated prose-analysis engine for examining a sentence, chapter, or entire manuscript.

ThothPad is not an auto-corrector and it is not an authorship detector. Its job is to surface patterns that are easy to miss while drafting so the writer can decide what matters.

> **The design rule:** findings are observations, not orders. ThothPad never requires you to flatten your voice to satisfy a score.

---

## What ThothPad can do

| Area | Capabilities |
| --- | --- |
| **Write** | Full Markdown editor, live HTML preview, outline navigation, themes, fullscreen writing, sentence/paragraph focus, typewriter scrolling, Reader Mode |
| **Live prose lenses** | Repetition, clichés, formulaic constructions, filter words, possible adverbs/adjectives/verbs, body clichés, negative listing, triad cadence, cinematic fog, rules-library matches, and other fast diagnostics while you work |
| **Deep prose analysis** | Rhythm, stylometry, lexical diversity, readability, concrete-vs-abstract language, metaphor density, vague abstraction, false agency, slop/formulaic-prose scoring, profile calibration, and detailed finding metadata |
| **Manuscript analysis** | Cross-file repetition, repeated lemma families, repeated sentence openings and paragraph endings, imagery reuse, chapter consistency, and manuscript-wide pattern hotspots |
| **Grammar & mechanics** | Private bundled Harper grammar checking, plus optional LanguageTool, ProWritingAid, Vale, and Proselint integrations |
| **Compare & revise** | Diagnose drafts, compare versions, line-edit, deslop, and optionally request model-assisted rewrites through an explicitly configured provider |
| **Style profiles** | Tunable thresholds, rule levels, dialogue exclusions, custom patterns, genre/project profiles, and corpus calibration against writing you choose |
| **Automation / agents** | The same engine is available through desktop IPC, CLI, FastAPI, and MCP, so prose diagnostics can be used by scripts or MCP-capable assistants |
| **Privacy** | Local analysis by default, no account requirement, no telemetry, no listening port for desktop analysis, and explicit consent before optional external services are used |

---

## Prose analysis in detail

### Repetition, echoes, and overused language

ThothPad can surface repetition at several levels instead of treating every repeated token as the same problem:

- repeated words and phrases
- repeated n-grams
- repeated lemma families, so inflected forms can be grouped
- repeated sentence openings
- repeated paragraph endings
- recurring imagery and image families
- filter words and other habitual diction
- manuscript-wide cross-file hotspots

This is useful both for obvious crutch words and for subtler structural echoes that are difficult to notice across a long draft.

### Formulaic and cliché detection

The engine includes dedicated analyzers for patterns such as:

- binary contrast constructions
- negative listing
- triadic cadence
- metaphor pileups and metaphor density
- body-language clichés
- cinematic-fog phrasing
- false agency
- vague abstraction
- formulaic / "AI-ish" prose patterns
- general cliché libraries
- genre, corporate, self-help, wordiness, and redundancy phrase packs

The current cliché/rule stack contains **hundreds of phrase patterns**, including a general library of more than 800 cliché entries.

These findings are craft signals, not evidence that a passage was written by AI. ThothPad deliberately does **not** claim to identify authorship and does not optimize for AI-detector evasion.

### Diction and parts of speech

The desktop lenses can identify likely:

- filter words
- adverbs
- adjectives
- verbs

Profiles can enable, disable, or raise the confidence threshold for individual analyzers and rules, so a deliberately ornate fantasy voice does not have to use the same standards as spare noir prose.

### Rhythm, texture, and stylometry

Deep analysis can measure prose shape rather than only matching phrases. Current metrics include:

- sentence-length variation
- paragraph-length variation
- burstiness
- readability
- MATTR
- MTLD
- HD-D
- lexical diversity
- repeated n-grams
- rhythm diagnostics
- concrete anchoring versus vague abstraction

The goal is to make patterns visible: a chapter whose rhythm suddenly flattens, a passage that becomes unusually abstract, or a model-generated section that is statistically much more uniform than the surrounding manuscript.

### Dialogue-aware analysis

Analyzers can be configured to ignore findings inside dialogue when appropriate. A cliché spoken by a character, for example, may belong to the character rather than the narrator. Profile-level dialogue exclusions let those cases be treated differently instead of blindly scoring every span the same way.

---

## Manuscript-scale revision

ThothPad is not limited to the paragraph currently on screen.

A full manuscript report can examine chapters together and find patterns that local editors usually miss, including:

- a phrase recurring twenty chapters apart
- the same type of sentence opening across multiple chapter starts
- repeated paragraph-ending cadences
- repeated imagery or lemma families
- chapter-to-chapter consistency shifts
- cross-file pattern concentrations

Full reports retain their complete diagnostic set in an **expiring local SQLite snapshot**. The desktop requests findings in bounded pages while preserving exact totals, so large manuscripts are not silently truncated by a UI result limit.

---

## Fast while you type, deep when you ask

The desktop editor and analysis engine use two different lanes:

**Live analysis** examines the edited paragraph and nearby context using a fast analyzer preset. Markdown parsing is debounced and diagnostic overlays are replaced in batches so typing remains responsive.

**Full reports** run outside the typing path and can use the complete analyzer stack. Long-running reports support cooperative cancellation, while the persistent worker stays warm so expensive analyzer state does not need to restart after every request.

This separation lets ThothPad behave like an editor first and an analysis suite second instead of freezing the writing surface every time a large report runs.

---

## Style profiles and corpus calibration

There is no universal correct density of adverbs, sentence fragments, metaphors, or clichés. ThothPad therefore supports **profiles** rather than pretending one house style fits everyone.

Profiles can control:

- enabled analyzers
- individual rule thresholds
- minimum confidence
- finding severity levels
- dialogue exclusions
- custom phrase/pattern rules
- project or genre-specific expectations

The engine can also **calibrate against a reference corpus you provide**. That makes it possible to compare a draft or model output against actual prose from the genre, author set, or house style you care about instead of relying entirely on generic writing advice.

---

## Grammar and mechanics

ThothPad's default desktop grammar provider is [Harper](https://github.com/automattic/harper), bundled as a private local process. No API key is required for normal local grammar analysis.

Optional integrations can extend that stack:

- **LanguageTool**
- **ProWritingAid**
- **Vale**
- **Proselint**
- optional spaCy-based prose analysis

External/cloud providers are not contacted automatically. They require an explicit operation and, where necessary, credentials supplied by the user.

---

## Optional model-assisted revision

ThothPad can work entirely without an LLM. The deterministic prose analyzers, manuscript reports, profiles, grammar tooling, and statistics do not depend on a generative model.

When you *do* want model assistance, the engine can perform operations such as:

- rewrite
- deslop
- line-edit
- compare drafts
- build or use voice/style profiles

The provider is configurable through an OpenAI-compatible endpoint, which means it can point at a **local model server** or an explicitly selected remote provider. Model use is opt-in per operation rather than part of the normal typing/analysis path.

---

## Local-first architecture

The Qt desktop application owns the document, editing state, undo history, highlighting, settings, revisions, consent, and credential storage.

**ThothPad Engine** is a persistent Python sidecar that owns prose rules, reports, profile semantics, offset conversion, grammar integration, model adapters, and manuscript analysis.

For desktop use:

- the app starts the engine with `QProcess`
- communication uses Content-Length-framed JSON over stdin/stdout
- no analysis server port is opened
- live analysis is local and non-persistent
- manuscript text and credentials are not logged
- API keys are passed only for explicitly requested external/model operations

See [ARCHITECTURE.md](ARCHITECTURE.md) for the trust boundary and process model.

---

## More than a desktop UI

The same core engine can be used several ways.

### Desktop sidecar

The native ThothPad application talks directly to the private engine process. This is the normal user experience.

### CLI

The engine includes commands for diagnosing chapters, comparing drafts, rewriting, analyzing manuscripts, and calibrating corpora.

Examples:

```powershell
python -m backend.cli diagnose .\chapter.md --profile fiction-gritty
python -m backend.cli compare .\draft-a.md .\draft-b.md
python -m backend.cli manuscript .\Novel\Chapters --profile fiction-gritty
python -m backend.cli calibrate .\model-outputs --reference .\human-samples --name local-fiction
```

### MCP

ThothPad exposes prose tools over the **Model Context Protocol**, including diagnosis, rewrite, deslop, comparison, voice-profile construction, manuscript analysis, and corpus calibration. An MCP-capable assistant can therefore use ThothPad's deterministic analysis engine instead of inventing prose criticism from scratch.

### FastAPI / browser interface

The Python engine can also run as a local service for development, scripting, or browser-based workflows.

See [writer-engine/README.md](writer-engine/README.md) for engine-specific commands and interfaces.

---

## The Ghostwriter foundation

ThothPad retains the core writing experience that made Ghostwriter useful:

- full Markdown editing
- live HTML preview
- cmark-gfm rendering
- MathJax support
- document outline / navigator
- document and session statistics
- focus modes
- typewriter scrolling
- fullscreen writing
- Reader Mode for cold reads
- themes and dark-mode support
- cheatsheet and HUD tools
- Windows, macOS, and Linux support

ThothPad's analysis stack is wrapped around that editor rather than replacing it with a browser-only writing interface.

---

## Design philosophy

ThothPad follows a few rules that matter more than any individual analyzer:

1. **The writer remains the authority.** A flag is evidence to inspect, not an instruction to obey.
2. **Analysis should explain itself.** Findings identify the span, analyzer, severity, and relevant metric rather than hiding everything behind one magic score.
3. **Context matters.** Dialogue, genre, project profile, and intentional repetition can change whether a pattern is a problem.
4. **Local should be the default.** Core writing and analysis must not require a cloud account.
5. **AI assistance is optional.** Deterministic diagnostics remain useful with no model configured at all.
6. **No fake authorship certainty.** Statistical or formulaic patterns are craft signals, not proof of who or what wrote a passage.

---

## Building

See [BUILDING.md](BUILDING.md) for the full native and engine build instructions.

At a high level, ThothPad uses:

- Qt 6
- CMake
- C++ for the desktop application
- Python 3.11+ for ThothPad Engine
- Harper for the default local grammar pass

The engine is covered by `ruff`, `mypy`, and pytest gates, while the desktop application has its own CTest/native CI coverage.

---

## Project documentation

- [ARCHITECTURE.md](ARCHITECTURE.md) — process model, trust boundary, analysis lanes, report storage
- [BUILDING.md](BUILDING.md) — build and development instructions
- [BRAND.md](BRAND.md) — product identity and visual language
- [CONTRIBUTING.md](CONTRIBUTING.md) — contribution guidance
- [writer-engine/README.md](writer-engine/README.md) — engine CLI, MCP, sidecar, grammar, and provider details

---

## License & attribution

ThothPad is a fork of KDE Ghostwriter by Megan Conkle and the KDE community and remains free software under **GPL-3.0-or-later**. License texts live in [`LICENSES/`](LICENSES), with third-party notices in [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).

Standing on the shoulders of: [Ghostwriter](https://github.com/KDE/ghostwriter) · [Harper](https://github.com/automattic/harper) · [spaCy](https://github.com/explosion/spaCy) · [cmark-gfm](https://github.com/github/cmark-gfm) · [Qt](https://www.qt.io) · [WordNet](https://wordnet.princeton.edu)

---

*ThothPad — because the second draft deserves better tools than the first.*
