# Third-Party Notices

ThothPad is a GPL-3.0-or-later fork of [KDE Ghostwriter](https://invent.kde.org/office/ghostwriter). Original Ghostwriter copyright/SPDX notices are retained in inherited source files, and the project license is preserved in `COPYING` and `LICENSES/`.

This file is a human-readable overview of major linked, bundled, and source-build dependencies. [`PROVENANCE.md`](PROVENANCE.md) records project lineage and version/source rationale in more detail. Release packages also include a generated CycloneDX SBOM, which is the authoritative inventory for the exact transitive dependencies present in a particular binary build.

## Native application

| Component | Role | Version basis | Upstream | License / terms |
| --- | --- | --- | --- | --- |
| KDE Ghostwriter | Original editor codebase and UX foundation | Project lineage | https://invent.kde.org/office/ghostwriter | GPL-3.0-or-later |
| Qt 6 | Desktop UI, widgets, threading, SVG and optional WebEngine preview | Minimum 6.5; reviewed Windows baseline 6.11.1 | https://www.qt.io/ | Upstream LGPL/GPL/commercial terms as applicable |
| KDE Frameworks 6 / ECM | Config, Sonnet, XML GUI and KDE build integration | Minimum 6.0; reviewed Windows baseline KF6 6.28 | https://invent.kde.org/frameworks | Upstream LGPL/GPL terms as applicable |
| QtKeychain | Operating-system credential storage | Exact staged version recorded by release SBOM | https://github.com/frankosterfeld/qtkeychain | BSD-2-Clause |
| cmark-gfm | Markdown parser/renderer | Vendored source under `3rdparty/cmark-gfm`; repository commit identifies exact tree | https://github.com/github/cmark-gfm | BSD-2-Clause |

## ThothPad Engine

Direct Python package versions are declared in `writer-engine/pyproject.toml`; the release dependency closure is locked separately and represented in the SBOM.

| Component | Version in source configuration | Role | Upstream | License |
| --- | --- | --- | --- | --- |
| Python | 3.11+; release lock uses 3.11.9 | Engine runtime/build environment | https://www.python.org/ | PSF License |
| FastAPI | 0.141.1 | Optional local API surface | https://github.com/fastapi/fastapi | MIT |
| Uvicorn | 0.52.4 | Optional local ASGI server | https://github.com/encode/uvicorn | BSD-3-Clause |
| Pydantic | 2.13.4 | Validation and API models | https://github.com/pydantic/pydantic | MIT |
| PyInstaller | 6.22.2 | Frozen desktop sidecar build | https://github.com/pyinstaller/pyinstaller | GPL-2.0-or-later with the PyInstaller bootloader exception |
| spaCy | 3.8.15 | Optional/contextual NLP and production desktop POS support | https://github.com/explosion/spaCy | MIT |
| `en_core_web_sm` | 3.8.0 | spaCy English model bundled into the desktop sidecar | https://github.com/explosion/spacy-models | Upstream model license |
| Proselint | 0.16.0 | Optional editorial lint integration | https://github.com/amperser/proselint | BSD-3-Clause |
| LexicalRichness | 0.5.1 | Optional/reference lexical-diversity implementation | https://github.com/LSYS/LexicalRichness | MIT |
| Harper | 2.5.0 | Private offline grammar/mechanics provider | https://github.com/Automattic/harper | Apache-2.0 |

The Harper bridge itself is built from `writer-engine/harper-bridge/` using the locked Rust dependency graph in `Cargo.lock`.

## Bundled data

- **Princeton WordNet 3.1** adjective/adverb/verb index and morphology exception files are bundled under `writer-engine/backend/data/wordnet`. Their Princeton WordNet license is preserved with the data. Upstream: https://wordnet.princeton.edu/
- **Slopless-derived phrase data** is bundled under `writer-engine/backend/data/slopless`. The imported MIT license and `websmasher` copyright notice are preserved at `writer-engine/backend/data/slopless/LICENSE`.

Additional analyzer data sources, optional integrations, and conceptual references are documented in `writer-engine/THIRD_PARTY.md`.

## Optional browser/development frontend

The engine's browser/development interface under `writer-engine/frontend/` is not required to compile the native Qt application. Its source configuration currently declares:

- React / React DOM 19 — https://github.com/facebook/react — MIT;
- Vite 6 and `@vitejs/plugin-react` — https://github.com/vitejs/vite — MIT;
- TypeScript 5.7 — https://github.com/microsoft/TypeScript — Apache-2.0; and
- Lucide React 0.468 — https://github.com/lucide-icons/lucide — ISC.

## License preservation

Reusable license texts are stored under `LICENSES/`, while component-specific licenses that must travel with imported data/code are also kept beside those assets when appropriate.

CMake installs `COPYING`, `PROVENANCE.md`, this file, `writer-engine/THIRD_PARTY.md`, and the `LICENSES/` directory with the application. Public release packaging additionally generates an SBOM so platform-specific and transitive dependencies are represented precisely instead of relying only on this manually maintained summary.
