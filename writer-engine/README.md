# ThothPad Engine

Local prose harness for diagnosing, rewriting, and comparing AI-assisted prose.

## What It Does

- Uses `Stop-Slop-v2` style positive routing for rewrite prompts.
- Uses original `stop-slop` references as diagnostics, not generation context.
- Runs analyzer passes for binary contrast, negative listing, triads, metaphor pileups, body cliches, cinematic fog, false agency, vague abstraction, rhythm, concrete anchoring, and slop-score hits.
- Detects more than 800 general cliches plus genre, self-help, corporate, wordiness, and redundancy phrase packs.
- Measures MATTR, MTLD, HD-D, burstiness, sentence/paragraph variation, readability, and repeated n-grams.
- Analyzes whole manuscripts for repeated lemma families, sentence openings, paragraph endings, imagery, chapter consistency, and cross-file pattern hotspots.
- Exposes the same core pipeline through FastAPI, CLI, and MCP.
- Exposes a production desktop sidecar over Content-Length framed JSON on stdin/stdout.
- Returns UTF-16 diagnostic offsets and supports editor-provided exclusion ranges.
- Keeps analysis ephemeral unless the caller explicitly requests persistence.

## Start

```powershell
.\start-thothpad.ps1
```

Then open:

```text
http://127.0.0.1:8789
```

## CLI

```powershell
python -m backend.cli diagnose .\chapter.md --profile fiction-gritty
python -m backend.cli rewrite .\chapter.md --profile fiction-gritty --passes 2
python -m backend.cli deslop .\chapter.md --profile creative-default
python -m backend.cli compare .\draft_ai.md .\draft_clean.md
python -m backend.cli manuscript .\Novel\Chapters --profile fiction-gritty --project "Novel A"
python -m backend.cli calibrate .\model-outputs --reference .\human-samples --name local-model-fiction
python -m backend.cli serve --host 127.0.0.1 --port 8789
```

Add `--save-run` to diagnose, rewrite, deslop, line-edit, compare, or manuscript when you
want readable run artifacts. These operations are non-persistent by default.

## Desktop sidecar

ThothPad launches the engine without a listening port:

```powershell
python -m backend.sidecar
```

Messages are UTF-8 JSON framed as `Content-Length: N\r\n\r\n<body>`. Protocol 1.3 supports
initialize, capabilities, profile management, region/document/manuscript analysis, rewrite,
compare, cancellation, and shutdown. Cancellation is cooperative: the supervisor flags the
request and the persistent report worker aborts at its next checkpoint without restarting,
so warm analyzer state survives; process-tree termination is the bounded last resort for a
worker that misses every checkpoint. Region analysis uses the fast live preset and accepts at
most 8,000 characters. Full reports retain every finding in an expiring local snapshot; clients
retrieve bounded pages with `query_findings`, so a page size is never an analysis limit. Desktop
offsets are UTF-16 code units.

Writable profiles, runs, and projects use the current platform's application-data directory.
Set `THOTHPAD_DATA_DIR` to override that location for portable or test installations.

## MCP

Run the MCP server with:

```powershell
python -m backend.mcp_server
```

Or from anywhere on the machine, point an agent at the portable launcher:

```text
<path-to-thothpad>\thothpad-mcp.cmd
```

MCP config shape:

```json
{
  "mcpServers": {
    "thothpad": {
      "command": "C:\\path\\to\\thothpad\\thothpad-mcp.cmd"
    }
  }
}
```

The launcher changes into the ThothPad folder before starting the server, so the calling agent can be in any working directory.

Tools:

- `prose_diagnose`
- `prose_rewrite`
- `prose_deslop`
- `prose_compare`
- `prose_build_voice_profile`
- `prose_list_profiles`
- `prose_get_run`
- `prose_analyze_manuscript`
- `prose_calibrate_corpus`
- `prose_quality_timeline`
- `prose_lens_baselines`
- `story_project_understanding`
- `story_resolve_entity`
- `story_find_evidence`
- `story_query_claims`
- `story_get_context`
- `story_get_character_knowledge`
- `story_get_character_beliefs`
- `story_get_reader_state`
- `story_query_timeline`
- `story_get_world_state`
- `story_where_is_entity`
- `story_who_has_object`
- `story_list_threads`
- `story_list_reader_questions`
- `story_list_dramatic_promises`
- `story_trace_causality`
- `story_get_decision_history`
- `story_get_opposition_state`
- `story_get_scene_contract`
- `story_get_author_decisions`
- `story_audit_scene`
- `story_audit_chapter`
- `story_list_branches`
- `story_compare_branch`
- `story_get_retcon_impact`
- `story_cold_reader_at`
- `story_audit_reveal_fairness`
- `story_get_reader_expectations`
- `story_get_dramatic_irony`
- `story_get_writer_model`
- `story_explain_writer_preference`
- `story_run_editorial_council`
- `story_list_lenses`
- `story_get_lens`
- `story_run_lens`
- `story_get_reader_experience`
- `story_get_reader_experience_timeline`
- `story_explore`
- `story_get_scene_semantics`
- `story_audit_continuity`
- `story_get_character_arc`
- `story_get_relationship_arc`
- `story_audit_ending_integrity`
- `story_get_project_health`
- `story_get_index_status`
- `story_run_wow_acceptance`

Story MCP tools are read-only and operate only on project roots the writer has already initialized in ThothPad. Naming an arbitrary machine directory over MCP does not create or authorize a Story Project.

## Analysis Policy

ThothPad does not claim to identify authorship and does not optimize for AI-detector evasion.
Rules are classified as hard, strong, contextual, or taste findings. A cliche in dialogue is
downgraded because it may belong to the speaker. Statistical uniformity is reported only as a
craft signal and never as proof that a model wrote the passage.

## Grammar and Optional Prose Stack

The native engine has no NLP dependency beyond the main FastAPI package. Install the extended
Python stack when you want Proselint and spaCy:

```powershell
py -3.11 -m pip install -e ".[prose]"
py -3.11 -m spacy download en_core_web_sm
```

The desktop sidecar bundles Harper as its default private grammar and mechanics provider. It is
available during live analysis without an API key. LanguageTool and ProWritingAid are optional
manual-report providers; cloud use requires explicit consent and credentials supplied transiently
by the desktop application's secure keychain. Vale and Proselint remain optional external tools.

See [THIRD_PARTY.md](THIRD_PARTY.md) for licenses and source attribution.

## LLM Provider

The default OpenAI-compatible endpoint is:

```text
http://127.0.0.1:1234/v1
```

Set these environment variables to override:

```powershell
$env:OPENAI_BASE_URL = "https://openrouter.ai/api/v1"
$env:OPENAI_API_KEY = "..."
$env:THOTHPAD_MODEL = "your-model"
```

Secrets are scrubbed from saved run config files.
