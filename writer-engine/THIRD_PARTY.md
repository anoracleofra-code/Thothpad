# ThothPad Engine — Third-Party Components

This file documents third-party code, data, integrations, and analysis references specific to `writer-engine/`. It should be read together with the repository-level `THIRD_PARTY_NOTICES.md`, `PROVENANCE.md`, and the generated release SBOM.

ThothPad does not claim to identify authorship and does not optimize for AI-detector evasion. Analyzer findings are editorial evidence and craft signals, not proof that a human or model wrote a passage.

## Bundled data and runtime components

### Princeton WordNet 3.1

ThothPad bundles adjective, adverb, and verb index files plus morphology exception files from Princeton WordNet 3.1 under `backend/data/wordnet`. They are used to validate contextual part-of-speech predictions.

- Upstream: https://wordnet.princeton.edu/
- License: Princeton WordNet license, preserved at `backend/data/wordnet/LICENSE`

### Slopless-derived phrase data

Selected phrase datasets are bundled under `backend/data/slopless` and are consumed by ThothPad's own profile-aware cliché/rules analysis.

- Original attribution: `websmasher`
- License: MIT, preserved at `backend/data/slopless/LICENSE`
- ThothPad applies its own severity, profile, dialogue-context, and reporting policy rather than presenting the imported phrase lists as a universal writing standard.

### Harper

Harper is the private offline Grammar and Mechanics provider used by the production desktop sidecar. The bridge in `harper-bridge/` pins the Harper Rust crates through `Cargo.toml` and `Cargo.lock`.

- Version: 2.5.0
- Upstream: https://github.com/Automattic/harper
- License: Apache-2.0

## Direct and optional Python dependencies

The authoritative direct versions are declared in `pyproject.toml`; release dependency closure is recorded by the lockfile/SBOM.

- **FastAPI 0.141.1** — optional HTTP/API surface — https://github.com/fastapi/fastapi — MIT
- **Uvicorn 0.52.4** — optional ASGI server — https://github.com/encode/uvicorn — BSD-3-Clause
- **Pydantic 2.13.4** — validation/API models — https://github.com/pydantic/pydantic — MIT
- **spaCy 3.8.15** — contextual NLP/POS support — https://github.com/explosion/spaCy — MIT
- **en_core_web_sm 3.8.0** — production desktop spaCy model — https://github.com/explosion/spacy-models — upstream model license
- **Proselint 0.16.0** — optional traditional editorial linting — https://github.com/amperser/proselint — BSD-3-Clause
- **LexicalRichness 0.5.1** — optional/reference MATTR, MTLD and HD-D implementation — https://github.com/LSYS/LexicalRichness — MIT
- **PyInstaller 6.22.2** — production sidecar freezer — https://github.com/pyinstaller/pyinstaller — GPL-2.0-or-later with the PyInstaller bootloader exception

ThothPad implements its core MATTR/MTLD/HD-D metrics natively so LexicalRichness is not required for those measurements in the normal engine path.

## Analysis references and conceptual lineage

The projects below informed analyzer design, calibration, or reliability framing. Reference status does **not** mean the complete upstream repository is bundled or required at runtime.

### slop-score — Samuel J. Paech

ThothPad's `slop_score` analyzer uses related ideas around over-represented words/phrases, contrast patterns, repetition, lexical diversity, readability, and prose-shape metrics, with ThothPad-specific weighting/reporting.

- Upstream: https://github.com/sam-paech/slop-score
- Upstream license: MIT for the primary slop-score code/data, with separately licensed wordfreq-derived components documented upstream

The current public ThothPad source tree does **not** vendor the complete upstream `slop-score` repository. `backend/config.py` supports an optional legacy data location under `writer-engine/vendor/slop-score/...`; when those optional lists are absent, the corresponding list-based hit sets are empty rather than becoming a source-build dependency.

### slop-forensics — Samuel J. Paech

Corpus-overrepresentation and comparison workflows informed ThothPad calibration/manuscript analysis concepts.

- Upstream: https://github.com/sam-paech/slop-forensics
- License: MIT

### auto-antislop — Samuel J. Paech

Referenced for iterative local-model calibration/fine-tuning concepts; it is not a ThothPad runtime dependency.

- Upstream: https://github.com/sam-paech/auto-antislop

### Other architectural references

- **Humanizer** by Brandon Wise — statistical uniformity, cross-file hotspot, and reliability framing.
- **AutoNovel** by Nous Research — canon/voice fingerprint, chapter-ledger, and manuscript-review concepts.

These references describe conceptual lineage only unless a separately identified file/dataset is actually copied into this repository.

## Optional external integrations

These tools/services are not required to compile or run the native editor's deterministic local analysis path:

- **LanguageTool** — optional multilingual grammar provider — https://github.com/languagetool-org/languagetool — LGPL-2.1-or-later
- **Vale** — optional markup-aware style/lint integration — https://github.com/errata-ai/vale — MIT
- **ProWritingAid** — optional user-configured external service; governed by its own service terms rather than an open-source dependency bundled by ThothPad

External providers are contacted only through explicit user-configured operations.

## Updating this file

When adding third-party code or data:

1. record the upstream project/source URL;
2. record the version, tag, commit, or import date where practical;
3. preserve the upstream copyright/license notice with copied material;
4. add any reusable license text to the repository's `LICENSES/` collection when appropriate; and
5. ensure release SBOM generation sees the component or imported asset.

Do not describe a project as "vendored" unless the corresponding source/data is actually present in the public tree.
