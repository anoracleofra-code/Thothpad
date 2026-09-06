# OpenRouter model catalog

Opening Model Settings with OpenRouter selected, or switching to OpenRouter,
automatically fetches its live catalog. **Update** beside Model refreshes it again. The
editable dropdown supports searching by any part of the model ID. IDs ending in
`:free` appear first, with newer catalog entries first within each group; selecting
one and clicking Save chooses it for inference. Older cached lists are also
grouped free-first without changing their relative order within each group.
Refreshing preserves the current model, including a manually typed ID, and does
not save other pending settings. The last successful catalog is cached locally.

Each refresh requests `https://openrouter.ai/api/v1/models` using Qt's
asynchronous networking. It sends no API key or document content, does not follow
redirects, and has an inactivity timeout, total deadline, and response-size bound.
Requests bypass Qt's network cache and ask intermediaries to revalidate using
`Cache-Control: no-cache`. There is no hard-coded model catalog.
Switching provider or closing the dialog cancels the request. Errors leave the
previous catalog and selected model intact and explicitly label the list as cached,
not verified current. A selected ID absent from a successful response is flagged;
refresh never silently changes the user's chosen inference model. No background
polling occurs outside Model Settings.

The public endpoint was verified without authentication on 2026-09-04 (HTTP 200,
427 models). OpenRouter inference still requires a key, including for `:free`
models; free pricing does not mean unauthenticated access. The dialog explains
this and reflects the application's existing per-model secure-credential identity:
changing models may require entering the key again. Keys are never cached with
the catalog or copied between model identities.

`providersettingsdialogtest` covers automatic and manual refresh, sorting, custom selection, caching,
failures, oversize responses, cancellation, provider isolation, saving, and key
identity hints with deterministic network replies. Set
`THOTHPAD_TEST_LIVE_CATALOG=1` to additionally run the opt-in real-endpoint check.
Its optional `THOTHPAD_TEST_CATALOG_IMAGE` path renders the test dialog to a PNG.
Tests use temporary settings, not the user's provider preferences or credentials.
