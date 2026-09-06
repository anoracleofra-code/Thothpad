# Provider sign-in and model discovery

These capabilities are user-independent. No account, API key, OAuth client ID,
client secret, or subscription token is shipped with ThothPad. Existing API-key
and local-provider choices remain available.

Each provider has separate saved endpoint, model, temperature, maximum tokens,
rewrite passes and timeout settings. Switching providers restores that provider's
choices or starts with its default endpoint, a blank model and default generation
settings. Unsaved non-secret edits are remembered while switching within the same
dialog; Save persists the selected provider, and Cancel discards unsaved edits.
Previously saved providers remain available after closing/reopening the dialog.
Pending keys and sign-in state never transfer between providers. Saved credential
references remain provider/origin/model scoped and secrets stay in secure storage.

## OpenAI / ChatGPT subscription

Select **OpenAI — Codex / ChatGPT sign-in**, then **Sign in**. Install the official
Codex CLI first if prompted. ThothPad uses the documented app-server protocol:
managed ChatGPT login, `model/list`, and ephemeral writing conversations. It does
not put a ChatGPT token into the regular OpenAI API field or read another app's
authentication file. Choose **Update**, select an available model, then **Save**.
Usage follows the user's Codex allowance and model availability; this is not
general OpenAI API credit. Codex controls generation limits, so temperature and
maximum-token controls are disabled for this provider.

The runtime gets a ThothPad-specific authentication home under its application
data directory, OS-keychain credential storage, and an empty temporary working
directory. Shell, browser, app/plugin, multi-agent and image tools are disabled;
turns request read-only permissions and never grant tool/permission approvals.
Project instructions are disabled. Writing results still go through ThothPad's
existing consent, review and editing paths. Disconnect signs out only this
ThothPad-owned Codex session. No CLI is silently downloaded or installed.

Reference: [Codex App Server](https://learn.chatgpt.com/docs/app-server).

## OpenRouter

Choose **OpenRouter**, then enter an API key or use **Sign in**. Browser sign-in
uses authorization code + S256 PKCE with a random loopback callback path. The
resulting user-controlled API key goes to the existing OS credential store when
Save succeeds. Its public catalog refreshes automatically on opening Model Settings
or switching to OpenRouter; Update can refresh it again. No key is needed for
catalog refresh. IDs ending in `:free` appear first;
newest models appear first within the free and remaining groups.

Cancelling the settings dialog does not revoke an authorization already granted
on OpenRouter. Disconnect removes the selected model's saved local key; users can
also revoke that key on OpenRouter's website.

Reference: [OpenRouter OAuth](https://openrouter.ai/docs/guides/overview/auth/oauth).

## Google Gemini OAuth

Choose **Google Gemini — OAuth**, then **Sign in** and import a Google **Desktop
app** OAuth client JSON. The implementation supports any correctly registered
client; it contains no personal or shared hard-coded registration. Registration
is an external prerequisite, not something endpoint discovery can supply:

1. Enable the Generative Language API in a Google Cloud project.
2. Configure its OAuth consent screen and audience/test users as appropriate.
3. Create a Desktop app OAuth client and download its JSON.
4. Import it in ThothPad, complete browser consent, Update models, choose one and Save.

For distribution with a shared application registration, the publisher must
register and verify that application with Google. Until then, this build accepts
user-supplied Desktop client registrations. A Gemini consumer subscription is not
automatically Gemini API credit; project quotas and billing apply.

The flow uses PKCE and OAuth state validation. Client configuration and the
refresh token are stored together only in the OS credential store, never in
QSettings or a checked-in file. Requests refresh access tokens through Google's
official token endpoint and use bearer authentication plus the project header.
OAuth credentials cannot be redirected to a custom inference endpoint.
Disconnect forgets the selected model's local credential; provider-side revocation
is available in the Google account's connected-app settings.

Reference: [Google Gemini OAuth](https://ai.google.dev/gemini-api/docs/oauth).

## Other providers and Update

Update is available for OpenAI, OpenAI-compatible services, Gemini, Anthropic,
Ollama, LM Studio and llama.cpp. It queries the appropriate documented model-list
route, including paginated Gemini/Anthropic catalogs. Gemini listings exclude
models that do not support generateContent. Local services list models they
actually expose/install, not every downloadable model on the internet.

Cloud lists generally require the provider's API key; the dialog explains missing
credentials and preserves the previous list/model on failure. Discovery for these providers is
explicit, sends no manuscript, refuses redirects and insecure remote HTTP, and
caches by provider and full endpoint. Provider/endpoint changes cancel pending
requests and clear pending credentials. Existing credentials remain model-scoped:
changing a saved model can require entering the key or signing in again.

## OpenCode Zen and Go

OpenCode Zen and OpenCode Go are separate provider choices with separate saved
settings. Their official live catalogs are fetched automatically when Model
Settings opens on that provider or the user switches to it; Update refreshes the
catalog again. Catalog requests send neither credentials nor manuscript text.
Free-priced Zen model IDs (`-free`, plus the currently documented Big Pickle)
appear first. The live catalog remains authoritative because free offers change.

Inference uses OpenCode's official endpoint and the wire format documented for
the chosen model family: OpenAI-compatible chat, OpenAI Responses, Anthropic
Messages, or Gemini generateContent. It never retries a prompt through a different
provider or model. Enter an OpenCode API key and Save before chatting, including
for free-priced models. Go additionally requires an active subscription; its
included usage is not presented as a free tier. Keys stay in the operating-system
credential store and remain provider/origin/model scoped.

References: [OpenCode Zen](https://opencode.ai/docs/zen),
[OpenCode Go](https://opencode.ai/docs/go/).

Anthropic subscription OAuth is deliberately not offered as a replacement for
API authentication. [Anthropic's credential rules](https://code.claude.com/docs/en/legal-and-compliance)
restrict third-party collection/intermediation of Claude.ai subscription tokens.
Custom local-server OAuth cannot be inferred merely from an OpenAI-compatible URL.

## Verification

Tests cover PKCE, loopback callbacks, state rejection, cancellation, secret
storage boundaries, Google refresh, provider-specific discovery/pagination,
cache isolation, stale replies, free-first sorting, and Codex RPC ordering and
permission refusal. The installed Codex runtime was checked using a fresh,
signed-out test profile. Actual account authorization and billable inference
require the user to sign in; automated tests do not use personal accounts.
