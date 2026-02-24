# Providers Directory Context
> **File:** `scripts/providers/CONTEXT.md`

## 1. Purpose

This directory contains the provider abstraction layer for Octobatch. It provides a unified interface for interacting with three LLM providers (Gemini, OpenAI, Anthropic) through both batch and realtime APIs. All pricing and model metadata is centralized in a YAML registry.

## 2. Key Components

### base.py (~308 lines)
Abstract base class defining the unified LLM provider interface.

**Key Types:**
- `BatchStatus` — Enum: PENDING, RUNNING, COMPLETED, FAILED, CANCELLED
- `BatchStatusInfo` — TypedDict: status, progress, error, provider_status, timestamps
- `BatchResult` — TypedDict: unit_id, content, input_tokens, output_tokens, error
- `BatchMetadata` — TypedDict: total token counts, timestamps, provider, model
- `RealtimeResult` — TypedDict: content, input_tokens, output_tokens, finish_reason

**LLMProvider Abstract Methods:**

| Method | Purpose |
|--------|---------|
| `generate_realtime(prompt, schema)` | Single synchronous API call → RealtimeResult |
| `format_batch_request(unit_id, prompt, schema)` | Format unit for provider-specific batch JSONL |
| `upload_batch_file(file_path)` | Upload JSONL to provider → file_id |
| `create_batch(file_id)` | Create batch job → batch_id |
| `get_batch_status(batch_id)` | Poll batch status → BatchStatusInfo |
| `download_batch_results(batch_id)` | Download results → (list[BatchResult], BatchMetadata) |
| `cancel_batch(batch_id)` | Cancel running batch → bool |
| `estimate_cost(input_tokens, output_tokens, is_batch)` | Cost estimation in USD |

**Registry Methods (non-abstract):**
- `load_model_registry()` — Loads models.yaml
- `get_provider_models(provider_name)` — Models dict for a provider
- `get_provider_info(provider_name)` — Provider-level metadata (env_var, sdk, default_model)
- `get_default_pricing()` — Fallback pricing defaults

**Exception Classes:**
- `ProviderError` — Base exception for all provider errors
- `RateLimitError` — 429/quota errors (should be retried)
- `AuthenticationError` — Auth failures (should abort)

### __init__.py (~161 lines)
Module entry point with factory functions.

**Key Functions:**

| Function | Purpose |
|----------|---------|
| `get_provider(config)` | Factory: instantiate correct provider from config |
| `get_step_provider(config, step_name, manifest)` | Provider with per-step overrides applied |

**Provider Resolution Order:** CLI flags > Step config > Global config > Registry default

CLI overrides are detected via manifest metadata (`cli_provider_override`, `cli_model_override`).

### gemini.py (~603 lines)
Google Gemini API provider implementation.

**Key Details:**
- SDK: `google-genai`
- Env var: `GOOGLE_API_KEY`
- Default model: `gemini-2.0-flash-001`
- HTTP timeout: 120 seconds
- Batch format uses `"key"` field (not `custom_id`)
- Retry loop with exponential backoff on rate limits in `create_batch()`
- Status normalization is lenient — heuristic fallback for unknown state strings
- Handles both camelCase and snake_case timestamp attributes

**Quirk:** Temporarily unsets `GEMINI_API_KEY` during client init to suppress an SDK warning when both `GOOGLE_API_KEY` and `GEMINI_API_KEY` are set.

### openai.py (~585 lines)
OpenAI API provider implementation.

**Key Details:**
- SDK: `openai>=1.0.0`
- Env var: `OPENAI_API_KEY`
- Default model: `gpt-4o-mini`
- Batch completion window: 24 hours
- Batch format uses `"custom_id"` and HTTP-style request structure
- No explicit rate limit retry (SDK handles it internally)
- Timestamps are Unix epoch (converted to ISO 8601)
- Error results may be in a separate `error_file_id` (fallback download)

### anthropic.py (~565 lines)
Anthropic Claude API provider implementation.

**Key Details:**
- SDK: `anthropic>=0.30.0`
- Env var: `ANTHROPIC_API_KEY`
- Default model: `claude-sonnet-4-20250514`
- **No server-side file upload** — reads local JSONL in `create_batch()`
- Results via iterator pattern (not single download)
- Uses BETA namespace: `client.beta.messages.batches.*`
- Status `"ended"` requires checking `request_counts` to determine success vs failure
- JSON schema enforcement via prompt instructions (no native JSON mode)

### models.yaml (~302 lines)
Centralized model registry with pricing and provider metadata.

**Structure:**
```yaml
default_provider: gemini

providers:
  <provider_name>:
    env_var: <env_var_name>
    sdk: <sdk_package>
    default_model: <model_id>
    realtime_multiplier: 2.0
    models:
      <model_id>:
        display_name: <human_readable_name>
        input_per_million: <price_usd>
        output_per_million: <price_usd>
        batch_support: true

defaults:
  input_per_million: 1.0
  output_per_million: 2.0
  realtime_multiplier: 2.0
```

**Pricing Model:**
- All prices stored as **batch prices** (the discounted rate)
- Realtime pricing = batch price × `realtime_multiplier` (typically 2.0)
- Cost formula: `((input_tokens / 1M × input_rate) + (output_tokens / 1M × output_rate)) × multiplier`
- Registry lookup cascade: Model-specific → Provider defaults → Global defaults

## 3. Data Flow

```
Pipeline config (api.provider, api.model)
    │
    ▼
__init__.py:get_provider(config)
    │
    ├── gemini.py:GeminiProvider(config)
    ├── openai.py:OpenAIProvider(config)
    └── anthropic.py:AnthropicProvider(config)
         │
         ▼
    LLMProvider interface
         │
         ├── Realtime: generate_realtime(prompt) → RealtimeResult
         │
         └── Batch: format → upload → create → poll → download
                                                      │
                                                      ▼
                                              (results, metadata)
```

## 4. Batch vs Realtime Patterns

**Realtime:**
- Single synchronous request → immediate response
- Cost: 2× batch price
- Used for testing, small runs, immediate results

**Batch:**
1. `format_batch_request()` → provider-specific JSONL dict
2. `upload_batch_file()` → file_id
3. `create_batch(file_id)` → batch_id
4. `get_batch_status(batch_id)` → progress (poll loop)
5. `download_batch_results(batch_id)` → results + metadata
6. Optional: `cancel_batch(batch_id)`

## 5. Maintenance

### scripts/maintenance/update_models.py (~752 lines)

Maintenance tool to auto-update `models.yaml` with current model lists and pricing:
1. Fetches model lists from each provider's API
2. Fetches pricing pages (HTML scraping with BeautifulSoup)
3. Extracts pricing via LLM call (cheapest available model)
4. Merges into models.yaml with batch discount applied (50%)

**Usage:**
```bash
python scripts/maintenance/update_models.py --dry-run
python scripts/maintenance/update_models.py --provider gemini
```

## 6. Key Patterns

### Provider-Specific Status Normalization
Each provider maps its native status strings to the unified `BatchStatus` enum. Gemini is the most complex (heuristic fallback for unknown states). Anthropic requires checking `request_counts` when status is "ended".

### Error Categorization
- `RateLimitError` (429, quota) → Retry with backoff
- `AuthenticationError` (401, 403) → Abort run
- `ProviderError` (other) → Log and continue or retry depending on context

### Registry-Driven Pricing
No pricing in pipeline configs. All pricing from `models.yaml`. Provider instances load their model's pricing at init time and expose it via `estimate_cost()`.
