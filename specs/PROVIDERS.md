# CURRENT_PROVIDERS.md — As-Is Intent Specification

## Purpose

This document describes what Octobatch's provider layer **should** do based on our design discussions. Written from intent, not code inspection. Gaps between this spec and the actual implementation are defects.

**Quality mapping:** Maps to QUALITY.md Scenario 3 (Laptop Sleep / Network Loss) for timeout handling, and Scenario 6 (Batch Mode Observability) for polling behavior.

---

## Architecture

The provider layer is the interface between Octobatch's orchestrator and external LLM APIs. It handles two execution modes (batch and realtime), three providers (Gemini, OpenAI, Anthropic), and a centralized model registry for pricing and configuration.

### Key Principle: Provider Agnosticism

Pipelines should work with any provider. The config can omit `api.provider` and `api.model` entirely, letting users choose at runtime. The three demo pipelines (Blackjack, NPC Dialog, Drunken Sailor) demonstrate this — they're provider-agnostic.

---

## Supported Providers

| Provider | Environment Variable | Default Model | Batch API | Realtime |
|----------|---------------------|---------------|-----------|----------|
| Gemini | `GOOGLE_API_KEY` | `gemini-2.0-flash-001` | ✓ | ✓ |
| OpenAI | `OPENAI_API_KEY` | `gpt-4o-mini` | ✓ | ✓ |
| Anthropic | `ANTHROPIC_API_KEY` | `claude-sonnet-4-20250514` | ✓ | ✓ |

**Standardized on `GOOGLE_API_KEY`** for all functional usage. A defensive reference remains in `gemini.py` (temporarily suppresses an SDK warning during client initialization) but is not used for authentication.

Only keys for providers you actually use need to be set. The TUI shows a non-blocking warning if common API keys are missing.

---

## Provider/Model Resolution

Priority order (highest wins):

1. **CLI flags** (`--provider`, `--model`) — Forces all steps to use this provider/model
2. **Per-step config** (`provider`/`model` on step definition in config.yaml)
3. **Global config** (`api.provider`/`api.model` in config.yaml)
4. **Registry default** (`default_model` from `scripts/providers/models.yaml`)

If no provider is specified anywhere, the CLI errors with a helpful message.

### CLI Override Tracking

When CLI flags override the config, this is recorded in the manifest metadata:
- `cli_provider_override: true/false`
- `cli_model_override: true/false`

This enables the TUI and monitoring tools to know whether the run used the pipeline's intended provider or was overridden for testing.

### Per-Step Overrides

Individual pipeline steps can specify their own provider and model:

```yaml
pipeline:
  steps:
    - name: generate
      provider: gemini              # Cheap and fast for generation
      model: gemini-2.0-flash-001
    - name: score
      provider: anthropic           # Accurate for scoring
      model: claude-sonnet-4-5-20250929
```

The `get_step_provider()` function resolves the provider for each step, respecting CLI overrides.

---

## The Model Registry

### Location

`scripts/providers/models.yaml`

### Structure

```yaml
providers:
  gemini:
    env_var: GOOGLE_API_KEY
    default_model: gemini-2.0-flash-001
    realtime_multiplier: 2.0
    models:
      gemini-2.0-flash-001:
        display_name: Gemini 2.0 Flash
        input_per_million: 0.075
        output_per_million: 0.3
        batch_support: true
```

### What the Registry Contains

- All supported models per provider
- Batch API pricing (input/output per million tokens) — these are already at the 50% batch discount
- `realtime_multiplier` per provider (typically 2.0)
- `default_model` per provider
- `display_name` for TUI dropdowns
- `batch_support` flag per model
- `env_var` — the environment variable name for each provider

### Registry as Single Source of Truth

All pricing comes from the registry. Pipeline configs must **not** include pricing. This centralization ensures:
- Cost calculations are consistent across all pipelines
- Price updates only need to happen in one place
- The TUI can display accurate cost estimates

### Updating the Registry

```bash
python scripts/maintenance/update_models.py
```

This script:
1. Fetches current model lists from each provider's API
2. Scrapes pricing pages for current pricing
3. Uses an LLM to extract structured pricing from page text
4. Merges changes into `models.yaml`, preserving existing structure
5. Never removes models — only warns about models no longer in the API
6. Converts standard/realtime prices to batch prices (50% discount)
7. Is idempotent — running twice with no changes reports "up to date"

Supports `--dry-run`, `--provider`, `--verbose`, and `--llm` flags.

---

## Batch Mode Provider Interface

### Gemini Batch API

The Gemini batch workflow:

1. **Prepare batch file** — Convert prompts to Gemini's batch request format (JSONL)
2. **Upload file** — Upload the batch file to Gemini's file API
3. **Create batch** — Submit the batch job referencing the uploaded file
4. **Poll for completion** — Check batch status periodically via `api.poll_interval_seconds`
5. **Download results** — On completion, download the results file
6. **Parse responses** — Extract individual responses from the results JSONL

**Gemini-specific behaviors:**
- Client timeout set to `120,000ms` (120 seconds) via `HttpOptions(timeout=120000)` — manually added to prevent infinite hangs. Before this was added, the Gemini client would hang indefinitely with 0% CPU on certain network conditions.
- Known transient error: `400 INVALID_ARGUMENT` on batch file upload, resolves on retry
- Batch jobs continue processing server-side even if the local process dies (closing laptop doesn't kill batch jobs)

### OpenAI Batch API

The OpenAI batch workflow:

1. **Prepare batch file** — Convert prompts to OpenAI's batch request format (JSONL with `custom_id`, `method`, `url`, `body`)
2. **Upload file** — Upload via Files API with `purpose: "batch"`
3. **Create batch** — Submit via Batch API referencing the file ID
4. **Poll for completion** — Check batch status periodically
5. **Download results** — Retrieve output file on completion
6. **Parse responses** — Extract responses matched by `custom_id`

### Anthropic Batch API

The Anthropic batch workflow:

1. **Prepare batch file** — Convert prompts to Anthropic's Message Batches format (JSONL with `custom_id` and `params`)
2. **Create batch** — Submit directly via Message Batches API (no separate file upload)
3. **Poll for completion** — Check batch status periodically
4. **Download results** — Retrieve results on completion
5. **Parse responses** — Extract responses matched by `custom_id`

### Common Batch Behaviors

All providers share these patterns:
- Batch ID stored in chunk's manifest entry for resumability
- Polling interval configurable via `api.poll_interval_seconds` (default 5)
- Max concurrent batches limited by `api.max_inflight_batches`
- Results are downloaded and validated through the same validation pipeline regardless of provider
- **Markdown block extraction must be applied to batch results** just as it is for realtime results, before passing to the validation pipeline. If a provider wraps batch responses in markdown fences, they must be stripped before Phase 1 schema validation.

**Verification note for Cursor:** This project started as a Gemini-specific batch wrapper and was later generalized to support OpenAI and Anthropic. Verify that `scripts/providers/` contains working batch clients for all three providers, not just Gemini. If OpenAI and/or Anthropic batch adapters are stubs or incomplete, document exactly what state they're in.

---

## Realtime Mode Provider Interface

### Implementation

`scripts/realtime_provider.py`

### Behavior

The realtime provider processes prompts synchronously:

1. Read prompts from input
2. For each prompt:
   a. Call the provider API with the rendered prompt
   b. Parse the response (extract JSON from markdown blocks if needed)
   c. Return the parsed result or error

### Markdown Block Extraction

LLMs frequently wrap JSON responses in markdown code fences. The realtime provider strips these before returning:
- `` ```json { ... } `` `` → `{ ... }`
- `` ``` { ... } `` `` → `{ ... }`
- Raw JSON → used as-is

### Rate Limiting and Retry

The realtime provider handles transient API errors:

- **429 (Rate Limit)** and **503 (Service Unavailable)**: Automatic retry with exponential backoff
- Backoff follows config: `initial_delay_seconds` × `backoff_multiplier^attempt`
- Default: 30s, 60s, 120s for 3 attempts

### Error Categorization

- **Rate limits (429), server errors (500, 503)**: Transient — retry with backoff
- **Auth/billing errors (400, 401, 403)**: Fatal — early abort, no retry. These indicate configuration problems (wrong API key, billing issue) that won't resolve on retry.

### Cost Cap Enforcement

In realtime mode, the accumulated cost is checked against `api.realtime.cost_cap_usd` after each unit. If exceeded, processing stops to prevent runaway spending during development.

---

## Cost Tracking

### Token Accounting

Tokens are tracked in the manifest at two levels:

**Per-chunk:**
- `input_tokens` — cumulative input tokens for this chunk
- `output_tokens` — cumulative output tokens for this chunk

**Aggregate (manifest metadata):**
- `initial_input_tokens` / `initial_output_tokens` — first-attempt tokens
- `retry_input_tokens` / `retry_output_tokens` — retry tokens

The separation enables accurate cost attribution — you can see how much retries are costing.

### Cost Calculation

```
cost = (input_tokens × input_price_per_million / 1,000,000) 
     + (output_tokens × output_price_per_million / 1,000,000)
```

For realtime mode, multiply by `realtime_multiplier` (typically 2.0).

Pricing comes from the model registry. The TUI's `compute_cost()` helper and the `get_run_cost_value()` utility both use this formula.

### Cost Display

- **TUI Run Stats panel:** Shows per-run cost
- **TUI Home Screen dashboard:** Shows aggregate cost across all runs
- **Console output:** Token counts logged with `[TOKENS]` prefix

---

## Provider-Specific Quirks and Known Issues

### Gemini

1. **Infinite hang without timeout** — The Gemini client (`google.genai`) has no default request timeout. Without explicitly setting `HttpOptions(timeout=120000)`, it can hang indefinitely at 0% CPU on network issues. This was discovered during a production run where the process appeared frozen.

2. **Transient 400 INVALID_ARGUMENT** — Occasionally occurs during batch file upload. The exact cause is unclear (possibly server-side rate limiting or file processing delays). Resolves on retry.

3. **Batch job persistence** — Gemini batch jobs run entirely on Google's servers. Closing your laptop or killing the local orchestrator doesn't affect them. The orchestrator can reconnect and poll for results on restart.

### OpenAI

1. **File upload size limits** — OpenAI has limits on batch file sizes. Very large chunks may need to be split.

2. **Batch processing time** — OpenAI batch jobs can take hours during peak periods. The 24-hour SLA means some runs take significantly longer than Gemini.

### Anthropic

1. **No separate file upload** — Anthropic's Message Batches API accepts the batch inline, unlike Gemini and OpenAI which require a separate file upload step. This simplifies the workflow but may limit batch sizes.

2. **Beta API status** — Anthropic's batch API was in beta during our implementation. The API surface may have changed.

### Cross-Provider

1. **No `.env` auto-loading** — Not implemented. API keys must be set as environment variables before running the orchestrator.

2. **No provider health checks** — The system doesn't verify API key validity or provider availability before starting a run. A bad API key only surfaces when the first batch submission or realtime call fails.

3. **Token counting differences** — Each provider counts tokens differently. Cost estimates are approximations, especially for input tokens where providers may count system prompts and formatting differently.

---

## Known Issues and Technical Debt

1. **No provider health check on startup** — Could validate API keys and provider availability before committing to a run, saving time on auth failures.

2. **No timeout on batch polling** — If a batch job never completes (provider-side bug), the orchestrator will poll forever. Should have a maximum batch age timeout.

3. **Gemini-centric realtime provider** — `realtime_provider.py` was originally built for Gemini using `google.genai`. The degree to which it's been generalized for OpenAI and Anthropic realtime calls should be verified.

4. **No provider-level rate limiting** — Rate limiting is handled per-request via retry/backoff, but there's no pre-emptive rate limiting (e.g., "only submit N requests per minute to this provider"). This relies entirely on the provider returning 429s.

5. **Batch result format differences** — Each provider returns batch results in a different format. The parsing logic for each provider's format should be verified for edge cases (empty responses, partial results, error responses mixed with successes).

6. **`update_models.py` requires LLM** — The model registry updater uses an LLM to extract pricing from scraped web pages. This is clever but fragile — if pricing page layouts change, the extraction may silently produce wrong numbers.
