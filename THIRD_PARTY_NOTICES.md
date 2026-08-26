# Third-Party Notices

ThothPad is a GPL-3.0-or-later fork of KDE Ghostwriter. The original copyright and license notices are retained in source files, `COPYING`, and `LICENSES/`.

Major bundled or linked components include:

- Qt and KDE Frameworks, under their upstream LGPL/GPL terms.
- QtKeychain, BSD-2-Clause.
- cmark-gfm, BSD-2-Clause.
- Python, PSF License.
- FastAPI, Pydantic, React, and spaCy, MIT.
- `en_core_web_sm`, distributed under its upstream model license.
- Princeton WordNet 3.1 lexical index and morphology exception files, under the bundled Princeton WordNet license.
- Lucide, ISC.
- Harper 2.5.0, Apache-2.0.

Analyzer datasets, optional integrations, and architectural references are listed in `writer-engine/THIRD_PARTY.md`. A release SBOM must be generated from the staged application so transitive native and Python dependencies are represented precisely.
