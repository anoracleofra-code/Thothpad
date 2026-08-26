# Third-Party Components

## Princeton WordNet 3.1

ThothPad bundles the adjective, adverb, and verb index and morphology exception files from Princeton WordNet 3.1. They are used only to validate contextual part-of-speech predictions. The license is preserved at `backend/data/wordnet/LICENSE` and in the headers of each index file.

ThothPad combines original orchestration and analyzers with selected open-source data and ideas.
Detector findings are editorial evidence, not proof of human or AI authorship.

## Included

- **Slop Score** by Sam Paech, MIT. Vendored under `vendor/slop-score`. Its word,
  bigram, trigram, contrast, lexical-diversity, readability, and repetition metrics
  inform the native `slop_score` analyzer.
- **Slop Forensics** by Sam Paech, MIT. Vendored under `vendor/slop-forensics`.
  Its corpus-overrepresentation approach informs `writer calibrate`.
- **Stop Slop** and **Stop-Slop-v2**, MIT. Vendored under `vendor/` and copied as
  active skill references under `skills/`.
- **Slopless** by websmasher, MIT. Selected phrase data is included under
  `backend/data/slopless` with its license. ThothPad applies its own
  profile-aware severity and dialogue-context policy.
- **wordfreq data**, under the upstream licenses documented in the vendored projects.
- **Harper 2.5.0**, Apache-2.0. Its Rust grammar engine is compiled into the
  desktop sidecar as the private, offline Grammar and Mechanics provider.

## Optional Integrations

- **Proselint**, BSD-3-Clause: traditional editorial linting.
- **spaCy**, MIT: optional lemmatization and future named-entity-aware analysis.
- **LexicalRichness**, MIT: reference implementation for MATTR, MTLD, and HD-D.
  ThothPad ships native equivalents so these metrics work without the dependency.
- **Vale**, MIT: optional markup-aware style packs.
- **LanguageTool**, LGPL-2.1-or-later: optional multilingual grammar service.

## Architectural References

- **Humanizer** by Brandon Wise, MIT: statistical uniformity, cross-file hotspots,
  and reliability framing.
- **AutoNovel** by Nous Research: canon, voice fingerprint, chapter ledger, and
  manuscript review concepts.
- **Auto-Antislop** by Sam Paech: future local-model calibration and fine-tuning path.

Project links and setup notes are maintained in `README.md`.
