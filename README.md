# ThothPad

**A prose-aware writing studio.**

**See what your prose is doing.**

ThothPad is a cross-platform Markdown editor with optional, local prose-awareness tools. It is based on KDE Ghostwriter and integrates ThothPad Engine as a private sidecar.

The editor can surface grammar and mechanics, filter words, likely adverbs, cliches, repeated language, formulaic cadence, stock body language, vague abstraction, and profile-defined general rules. These are observations controlled by the writer, not automatic corrections or claims about authorship. Harper provides the bundled offline grammar pass; manual reports can instead use a configured LanguageTool or ProWritingAid account.

## Current development layout

- `src/`: native Qt editor and Prose Awareness interface
- `writer-engine/`: Python analyzers, profiles, reports, CLI, MCP, browser interface, and desktop protocol
- `quality-gates/`: independent review records for implementation slices
- `resources/`: application resources and platform metadata

## Privacy model

Local analysis needs no API key and performs no network request. Optional model-backed explanations and rewrites require explicit action and show the provider and text scope before transmission. See [PRIVACY.md](PRIVACY.md).

## Building

The native application requires CMake, Ninja, Qt 6.5 or newer, KDE Frameworks 6, ECM, Sonnet, Qt WebEngine, and QtKeychain. On Windows, KDE Craft with MSVC 2022 is the supported toolchain.

The engine build requires Python 3.11 or newer and Rust 1.95:

```powershell
cd writer-engine
python -m pip install -e ".[dev]"
python -m pytest -q
```

Source provenance and upstream attribution are recorded in [PROVENANCE.md](PROVENANCE.md). ThothPad is licensed under GPL-3.0-or-later; bundled components retain their own compatible licenses.

Detailed native, sidecar, and release instructions are in [BUILDING.md](BUILDING.md). Third-party attribution is collected in [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).
The product name, tagline, icon usage, and palette are defined in [BRAND.md](BRAND.md).
