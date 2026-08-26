# ThothPad Architecture

ThothPad keeps document ownership and presentation in the Qt application. A bundled Python process owns prose rules, analysis, reports, and optional model requests.

## Trust boundary

The desktop app starts the engine with `QProcess` and communicates through Content-Length framed JSON over standard input and output. The desktop integration opens no listening port. Live analysis is local, deterministic, and non-persistent.

API keys are required only for explicit model operations. The native app stores them in the operating system credential store and passes a key to the engine only for the lifetime of the requested operation. Manuscript text and credentials are not logged.

## Ownership

The Qt application owns editing, files, undo, highlighting, settings presentation, revisions, cancellation, consent, and credential storage. `writer-engine/` owns analyzers, profile semantics, offset conversion, report generation, model adapters, and the desktop protocol.

The existing browser, CLI, and MCP interfaces call the same Python engine. They are not used as desktop IPC.

## Analysis lanes and scale

Live analysis is limited to the edited paragraph and adjacent context. Markdown parsing is
debounced, document statistics update only affected blocks, and diagnostic overlays are replaced
as a single batch. Full document reports run outside the typing path. Cancelling a report
signals the persistent engine worker cooperatively and keeps its warm state; killing the
worker process tree is the bounded last resort.

Full reports store complete diagnostics in an expiring SQLite snapshot under the platform cache
directory. The sidebar requests the selected lens in 250-row pages while displaying exact totals.
There is no per-analyzer occurrence ceiling; the 500-row protocol maximum applies only to one
response page.
