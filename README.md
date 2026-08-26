# ThothPad

**A prose-aware writing studio. See what your prose is doing.**

ThothPad is a **fork of [KDE Ghostwriter](https://invent.kde.org/office/ghostwriter)** — the beloved distraction-free Markdown editor — rebuilt into a full prose-aware writing studio. Everything that made Ghostwriter great is still here: the quiet interface, the focus modes, the Markdown preview, the themes. Wrapped around it now sits a local analysis engine that reads your draft the way an editor does.

---

## Why ThothPad

Every writer has tics. The crutch word you lean on in every tense scene. The sentence rhythm that flatlines for three pages. The cliché you didn't notice you borrowed. The dialogue scene that's all talk and no grounding. You can't see these things while you're writing — and you shouldn't have to. **That's what revision is for.**

ThothPad is built for revision. It puts a lens over your prose and shows you what's actually on the page:

- **Repetition detection** — repeated words, phrases, sentence openings, and paragraph endings, with both occurrences highlighted in the text and per-chapter counts in the report
- **Crutch-word curves** — your most overused words, charted across the whole manuscript so you can see the spike in chapter nine
- **Cliché and formulaic-prose detection** — stock phrases, borrowed cadences, and the "it wasn't X, it was Y" tic, flagged where they occur
- **Echo detection** — image families and phrasings that repeat when you didn't mean them to
- **Pacing heatmap** — the outline tinted by section rhythm: amber where dialogue accelerates, blue where narration thickens
- **Breath map** — a sentence-rhythm strip beside the editor that flags monotonous same-length runs before your reader does
- **Dialogue balance** — span counts, dialogue-word ratio, and tag-verb histograms, per scene and per manuscript
- **Chapter-opener audit** — flags the chapter beginnings that all start the same way
- **Grammar and mechanics** — a bundled offline grammar pass via [Harper](https://github.com/automattic/harper), or bring your own LanguageTool / ProWritingAid account
- **Genre calibration** — compare your lens densities against a reference corpus you calibrate yourself, so "too many adverbs" means *too many for your genre*, not some universal guess

Every finding is an **observation, not an order**. ThothPad never auto-corrects, never rewrites you, and makes no claims about who wrote what. Lenses toggle on and off. Findings are color-coded, navigable, and yours to ignore.

## Local-first, always

Your words never leave your machine. The analysis engine runs as a **private local sidecar** — no cloud, no telemetry, no account. Connect an LLM provider only if you explicitly want AI-assisted rewriting, and even then the choice is per-action and yours.

## The engine

**ThothPad Engine** is a Python sidecar that speaks a framed JSON protocol to the editor. It runs the analysis stack — spaCy-powered part-of-speech tagging, WordNet lemma grouping, a persistent Harper grammar session, and a corpus-calibration system — with cooperative cancellation, incremental document patching, and warm-process reuse, so analysis stays responsive while you type.

The engine also exposes the same analysis over **MCP** (Model Context Protocol), so you can drive prose diagnostics from any MCP-capable assistant.

## More from the Ghostwriter side

- Full Markdown with live HTML preview (MathJax, cmark-gfm)
- Focus modes: sentence, paragraph, and typewriter scrolling; a fullscreen **Reader Mode** for cold reads
- Themes — including the Kanagawa Lotus palette — plus full dark-mode support
- Document statistics: word counts, reading time, session goals, and a statistics indicator you can point at whatever you care about
- Cheatsheet, outline navigator, and HUD
- Cross-platform: Windows, macOS, Linux

## Building

See [BUILDING.md](BUILDING.md). In short: Qt 6, CMake, and a Python 3.11+ environment for the engine. The engine's floors are enforced — `ruff`, `mypy`, and a pytest suite — and the editor ships a ctest suite of its own.

## License & attribution

ThothPad is a fork of KDE Ghostwriter by Megan Conkle and the KDE community, and remains free software under **GPL-3.0-or-later**. All license texts live in [`LICENSES/`](LICENSES), with third-party notices in [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).

Standing on the shoulders of: [Ghostwriter](https://github.com/KDE/ghostwriter) · [Harper](https://github.com/automattic/harper) · [spaCy](https://github.com/explosion/spaCy) · [cmark-gfm](https://github.com/github/cmark-gfm) · [Qt](https://www.qt.io) · [WordNet](https://wordnet.princeton.edu)

---

*ThothPad — because the second draft deserves better tools than the first.*
