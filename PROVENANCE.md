# ThothPad Provenance

This document records where ThothPad comes from, which third-party components are bundled or linked, and how release builds preserve dependency identity. It complements `THIRD_PARTY_NOTICES.md`, `writer-engine/THIRD_PARTY.md`, the license texts under `LICENSES/`, and the generated release SBOM.

## Project lineage

ThothPad is a GPL-3.0-or-later fork of [KDE Ghostwriter](https://invent.kde.org/office/ghostwriter). Ghostwriter source files retain their original copyright and SPDX notices. ThothPad-specific editor, prose-analysis, packaging, and integration work is layered on top of that codebase and remains distributed under the repository's GPL-3.0-or-later terms unless a file carries a more specific compatible notice.

The repository keeps upstream attribution in source headers, `COPYING`, `LICENSES/`, `THIRD_PARTY_NOTICES.md`, and the engine-specific `writer-engine/THIRD_PARTY.md`.

## Native application provenance

| Component | Role in ThothPad | Version basis | Upstream | License / notice |
| --- | --- | --- | --- | --- |
| KDE Ghostwriter | Original editor codebase and UX foundation | Repository lineage | https://invent.kde.org/office/ghostwriter | GPL-3.0-or-later; original notices retained |
| Qt 6 | Desktop UI, threading, SVG, widgets, preview plumbing | Minimum 6.5; reviewed Windows baseline 6.11.1 | https://www.qt.io/ | Upstream LGPL/GPL/commercial terms as applicable |
| KDE Frameworks 6 / ECM | Config, Sonnet, XML GUI, build integration and related desktop services | Minimum KF6/ECM 6.0; reviewed Windows baseline KF6 6.28 | https://invent.kde.org/frameworks | Upstream LGPL/GPL terms as applicable |
| QtKeychain | Native credential storage integration | Resolved by the platform package manager / Craft; exact release identity is captured by the release SBOM | https://github.com/frankosterfeld/qtkeychain | BSD-2-Clause |
| cmark-gfm | Markdown parsing/rendering | Vendored source under `3rdparty/cmark-gfm`; the repository commit identifies the exact source tree | https://github.com/github/cmark-gfm | BSD-2-Clause |

The top-level CMake project expresses the build-time requirements. Release packaging additionally records the exact staged native libraries and hashes in a CycloneDX SBOM.

## ThothPad Engine provenance

The Python engine is ThothPad-specific orchestration and analysis code with selected third-party libraries, data, and reference implementations.

### Runtime and build dependencies

| Component | Version used by the source tree | Role | Upstream | License |
| --- | --- | --- | --- | --- |
| Python | 3.11+; release lock uses 3.11.9 | Engine runtime/build environment | https://www.python.org/ | PSF License |
| FastAPI | 0.141.1 | Optional local HTTP/API surface | https://github.com/fastapi/fastapi | MIT |
| Uvicorn | 0.52.4 | Optional local ASGI server | https://github.com/encode/uvicorn | BSD-3-Clause |
| Pydantic | 2.13.4 | Validation and API models | https://github.com/pydantic/pydantic | MIT |
| PyInstaller | 6.22.2 | Frozen desktop sidecar build | https://github.com/pyinstaller/pyinstaller | GPL-2.0-or-later with the PyInstaller bootloader exception |
| spaCy | 3.8.15 | Optional/contextual NLP and desktop POS support | https://github.com/explosion/spaCy | MIT |
| `en_core_web_sm` | 3.8.0 | Bundled spaCy English model in the desktop sidecar | https://github.com/explosion/spacy-models | Upstream model license |
| Proselint | 0.16.0 | Optional editorial lint integration | https://github.com/amperser/proselint | BSD-3-Clause |
| LexicalRichness | 0.5.1 | Optional/reference lexical-diversity implementation | https://github.com/LSYS/LexicalRichness | MIT |
| Harper | 2.5.0 | Private offline grammar/mechanics provider | https://github.com/Automattic/harper | Apache-2.0 |

Exact Python and Rust dependency closure for release artifacts is derived from the engine lockfiles and recorded in the generated SBOM rather than maintained manually in this document.

### Bundled data

- **Princeton WordNet 3.1** adjective/adverb/verb index and morphology exception data is stored under `writer-engine/backend/data/wordnet`. The Princeton WordNet license is preserved with the data. Upstream: https://wordnet.princeton.edu/
- **Slopless-derived phrase data** is stored under `writer-engine/backend/data/slopless`. Its imported MIT license and original `websmasher` copyright notice are preserved at `writer-engine/backend/data/slopless/LICENSE`.

### Analysis references

Some ThothPad analyzers were informed by published/open-source approaches without requiring those projects as runtime dependencies. Those references are credited so that the conceptual lineage is visible:

- **slop-score** by Samuel J. Paech — lexical over-representation, repetition, contrast-pattern and prose-shape metrics. Upstream: https://github.com/sam-paech/slop-score
- **slop-forensics** by Samuel J. Paech — corpus comparison and over-representation workflows. Upstream: https://github.com/sam-paech/slop-forensics
- **auto-antislop** by Samuel J. Paech — iterative anti-slop analysis/fine-tuning concepts. Upstream: https://github.com/sam-paech/auto-antislop

Reference status does not mean the complete upstream repository is bundled. If third-party code or data is copied into the tree, its license and source attribution must travel with that copy.

## Browser/development interface provenance

The optional engine frontend uses the versions declared in `writer-engine/frontend/package.json`, currently including React 19, React DOM 19, Vite 6, TypeScript 5.7, and Lucide React 0.468. These packages are development/browser-interface dependencies; they are not required to compile the native Qt editor itself.

Upstream projects:

- React / React DOM: https://github.com/facebook/react — MIT
- Vite and `@vitejs/plugin-react`: https://github.com/vitejs/vite — MIT
- TypeScript: https://github.com/microsoft/TypeScript — Apache-2.0
- Lucide: https://github.com/lucide-icons/lucide — ISC

## Build and release provenance

`packaging/toolchain-lock.json` is the reviewed native build-input contract. Release jobs record toolchain and dependency identities, source revisions where locked, package manifests, staged-file hashes, and a CycloneDX 1.5 SBOM.

The release SBOM is the authoritative inventory for an individual binary package because transitive dependencies can differ by operating system and package profile. `PROVENANCE.md` documents project-level lineage; the SBOM documents the exact contents of a particular build.

## Redistribution

Anyone redistributing ThothPad source or binaries should preserve:

1. the GPL license and applicable source copyright notices;
2. `THIRD_PARTY_NOTICES.md` and `writer-engine/THIRD_PARTY.md`;
3. relevant license texts from `LICENSES/` and licenses stored beside bundled datasets/components;
4. source availability obligations imposed by the licenses of the redistributed build; and
5. the generated SBOM/provenance artifacts for public release packages where practical.

This document is an engineering provenance record, not legal advice. When adding a dependency or imported dataset, update the relevant notice, preserve its upstream license, and make the source/version discoverable before release.
