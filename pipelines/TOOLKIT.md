# Octobatch Pipeline Creation Toolkit

> **Purpose:** This guide enables Claude Code to act as an IDE for creating Octobatch pipelines. Read this file to understand how to create, configure, and debug pipelines.

---

## 0. Claude Code Guardrails

**CRITICAL INSTRUCTION FOR AI CODING ASSISTANTS:**

When creating or editing Octobatch pipelines:

1. **Scope:** Only create/modify files within the `pipelines/` directory for the target pipeline.
2. **Hands Off System Code:** Do NOT modify Octobatch source files (`scripts/`, `ai_context/`, `requirements.txt`, etc.) even if you discover a bug.
3. **Bug Reporting:** If you encounter a bug in the orchestrator or tools, report it to the user. Do not attempt to patch system code yourself.

These constraints ensure pipeline development stays isolated from core system maintenance.

---

## 1. Pipeline Structure

Every pipeline lives in its own directory under `pipelines/`:

```
pipelines/
  MyPipeline/
    config.yaml       # Main configuration (required)
    items.yaml        # Input data (required, can also be .json)
    templates/        # Jinja2 prompt templates
      step_name.jinja2
    schemas/          # JSON schemas for output validation
      step_name.json
```

**Required files:**
- `config.yaml` - Pipeline configuration
- `items.yaml` (or `.json`) - Input data for unit generation
- At least one template in `templates/`
- At least one schema in `schemas/`

---

## 2. Config.yaml Reference

### Complete Structure

```yaml
# ============================================================
# PIPELINE DEFINITION
# ============================================================
pipeline:
  steps:
    - name: step_name              # Required: unique step identifier
      prompt_template: step.jinja2 # Required: template filename
      description: "Description"   # Optional: displayed in TUI Selected Step panel
      provider: anthropic          # Optional: per-step provider override
      model: claude-sonnet-4-5-20250929  # Optional: per-step model override

# ============================================================
# API CONFIGURATION
# ============================================================
api:
  # Provider and model (optional in config - can be specified via CLI or TUI)
  provider: gemini                 # gemini | openai | anthropic
  model: gemini-2.0-flash-001      # Model identifier (see scripts/providers/models.yaml)

  # Retry configuration for failed API calls
  retry:
    max_attempts: 3
    initial_delay_seconds: 30
    backoff_multiplier: 2.0

  # Batch polling interval
  poll_interval_seconds: 10
  max_inflight_batches: 5

  # Cost controls for realtime mode
  realtime:
    cost_cap_usd: 50.0
    auto_retry: true

# **Best Practice:** Omit `api.provider` and `api.model` to create
# provider-agnostic pipelines. Users specify the provider at runtime
# via `--provider` flag or TUI selection. This allows the same pipeline
# to run on Gemini, OpenAI, or Anthropic without config changes.
#
# **Per-step overrides:** Individual steps can specify `provider` and/or
# `model` to use a different provider for that step. Resolution order:
#   1. CLI flags (--provider/--model) — highest priority, applies to ALL steps
#   2. Step-level config (provider/model on the step definition)
#   3. Global config (api.provider/api.model)

# ============================================================
# PROCESSING CONFIGURATION
# ============================================================
processing:
  # Unit generation strategy
  strategy: permutation            # permutation | cross_product | direct

  # Batch size
  chunk_size: 100                  # Units per batch request

  # === For Monte Carlo simulations ===
  repeat: 1000                     # Run each unit N times
  expressions:                     # Dynamic values with seeded random
    card: "random.choice(['A','K','Q','J','10','9','8','7','6','5','4','3','2'])"
    dice: "random.randint(1, 6)"

  # === For permutation strategy ===
  positions:
    - name: slot_a
      name_field: id               # Field to use for unit_id generation
    - name: slot_b
      name_field: id

  # === For cross_product strategy ===
  positions:
    - name: character
      source_key: characters       # Key in items file for this position
    - name: situation
      source_key: situations       # Key in items file for this position

  # Item source configuration
  items:
    source: items.yaml             # Input data file
    key: items                     # Top-level key in source file
    name_field: id                 # Field to use for unit_id generation

  # Validation retry settings
  validation_retry:
    max_attempts: 3

# ============================================================
# PROMPT TEMPLATES
# ============================================================
prompts:
  template_dir: templates          # Directory containing templates
  templates:
    step_name: step_name.jinja2    # Map step names to template files
  global_context:                  # Variables available in all templates
    game_name: "My Game"
    style: "formal"

# ============================================================
# JSON SCHEMAS
# ============================================================
schemas:
  schema_dir: schemas              # Directory containing schemas
  files:
    step_name: step_name.json      # Map step names to schema files
  strict_mode: true                # Enforce strict validation
  log_validation_errors: true

# ============================================================
# VALIDATION RULES (REQUIRED!)
# ============================================================
# IMPORTANT: This section is REQUIRED for validation to work!
# Missing this causes "No validation config for step" errors.
validation:
  step_name:
    required:
      - field_name                 # Fields that must be present
    types:
      field_name: string           # string | number | boolean | object | array
    rules:                         # Optional: expression-based rules
      - name: rule_name
        expr: "len(items) >= 3"
        error: "Need at least 3 items"
        level: error               # error | warning

# ============================================================
# POST-PROCESSING (OPTIONAL)
# ============================================================
# Scripts to run automatically after pipeline completes
post_process:
  - name: "Strategy Analysis"        # Display name for logging
    script: "scripts/analyze_results.py"  # Path relative to project root
    args:                            # Additional arguments (run_dir auto-injected)
      - "--group-by"
      - "strategy"
      - "--count-field"
      - "result"
    output: "analysis.txt"           # Optional: Save stdout to this file in run_dir
```

---

### ⚠️ CRITICAL: The Step Name Link

To make a step work, you must link it in **FOUR places** using the **exact same step name** (e.g., `generate`):

| Location | Example |
|----------|---------|
| 1. Pipeline step | `pipeline.steps: - name: generate` |
| 2. Prompts mapping | `prompts.templates: { generate: "generate.jinja2" }` |
| 3. Schemas mapping | `schemas.files: { generate: "generate.json" }` |
| 4. Validation rules | `validation: { generate: { required: [...] } }` |

**If any of these keys do not match the step name exactly, the pipeline will fail** with errors like:
- "No template defined for step"
- "No validation config for step"
- "Failed to load schema"

> **Exception:** Expression-only steps (`scope: expression`) are exempt from the 4-Point Link rule. They only need the `pipeline.steps` entry with their `expressions:` block. They do NOT require entries in `prompts.templates`, `schemas.files`, or `validation:`.

> **Tip:** The `description` field in `pipeline.steps` is displayed in the TUI's Selected Step sidebar panel. Write clear, concise 1-2 sentence descriptions for each step so pipeline users can understand what each step does at a glance.

### ⚠️ CRITICAL: The Post-Processing Field Rule

Every field name used in `post_process` args (`--group-by`, `--count-field`, `--numeric-field`) **must exist as an output field of the final pipeline step's validated output**. The `analyze_results.py` script reads `*_validated.jsonl` files, so it can only group by and count fields that are present in those files.

In multi-step pipelines, this means the **last step's template must echo back identifying fields from earlier steps** so post-processing can access them. Earlier step outputs are accumulated in the unit context and available to templates, but they only appear in the final `_validated.jsonl` if the last step's schema and template explicitly include them.

**Common mistake:** Post-processing uses `--group-by strategy_name`, but the final step's template and schema don't include a `strategy_name` field. The grouping silently fails or produces empty results.

**How to avoid it:**

1. For each `--group-by` and `--count-field` in your post_process config, trace the field back to which step produces it
2. If the field comes from an earlier step or from the items file, the final step's template must echo it back: `"strategy_name": "{{ strategy_name }}"`
3. The final step's schema must include the field in its properties
4. The final step's validation must list it in `required` and `types`

**Example:** In a 3-step pipeline (deal → play → validate), if post-processing groups by `strategy_name` (from items) and counts `difficulty` (from validate step):
- The validate step template must include: `"strategy_name": "{{ strategy_name }}"`
- The validate step schema must include `strategy_name` in its properties
- The validate step validation must list `strategy_name` in `required` and `types`

This rule works alongside the 4-Point Link Rule. The Link Rule ensures steps are wired correctly. The Field Rule ensures post-processing can find the data it needs.

---

## 2b. Provider Configuration

Octobatch supports three LLM providers. Each requires an API key set as an environment variable.

### Supported Providers

| Provider | Environment Variable | Batch Support |
|----------|---------------------|---------------|
| Gemini | `GOOGLE_API_KEY` | ✓ |
| OpenAI | `OPENAI_API_KEY` | ✓ |
| Anthropic | `ANTHROPIC_API_KEY` | ✓ |

### Provider/Model Resolution

Provider and model can be specified in three places (highest priority wins):

1. **CLI flags**: `--provider gemini --model gemini-2.0-flash-001`
2. **Pipeline config**: `api.provider` and `api.model` in config.yaml
3. **Registry default**: If model not specified, uses provider's `default_model` from models.yaml

If no provider is specified anywhere, the CLI will error with a helpful message.

### Model Registry

Available models and their pricing are defined in `scripts/providers/models.yaml`. This centralized registry:
- Lists all supported models per provider
- Contains batch API pricing (input/output per million tokens)
- Defines each provider's default model
- Is used by the TUI to populate model dropdowns

To see available models, check the registry file or use the TUI's New Run modal.

### Provider Examples

**Gemini (default, cheapest for high-volume):**
```yaml
api:
  provider: gemini
  model: gemini-2.0-flash-001
```

**OpenAI:**
```yaml
api:
  provider: openai
  model: gpt-4o-mini
```

**Anthropic:**
```yaml
api:
  provider: anthropic
  model: claude-sonnet-4-20250514
```

### Per-Step Provider/Model Overrides

Individual pipeline steps can specify their own `provider` and/or `model`, enabling multi-provider pipelines where different steps use different LLMs:

```yaml
pipeline:
  steps:
    - name: generate
      prompt_template: generate.jinja2
      description: "Generate creative content"
      provider: gemini                    # Cheap and fast for generation
      model: gemini-2.0-flash-001

    - name: score
      prompt_template: score.jinja2
      description: "Score quality with a stronger model"
      provider: anthropic                 # Higher quality for scoring
      model: claude-sonnet-4-5-20250929

    - name: refine
      prompt_template: refine.jinja2
      description: "Refine based on scores"
      # No provider/model — inherits from global api: section or CLI flags
```

**Resolution order** (highest priority wins):
1. **CLI flags** (`--provider`, `--model`) — applies to ALL steps, overrides everything
2. **Step-level config** (`provider`/`model` on the step definition)
3. **Global config** (`api.provider`/`api.model` in config.yaml)
4. **Registry default** (provider's `default_model` from models.yaml)

**How it works:**
- At init time, `cli_provider_override` and `cli_model_override` booleans are stored in manifest metadata
- `get_step_provider()` checks these flags before applying step-level overrides
- If CLI flags were explicitly provided, step-level overrides are skipped (CLI wins)
- The TUI's "Use Pipeline Config" option passes `None` as the override, allowing per-step config to take effect

**Example: Provider-agnostic pipeline with step overrides**
```yaml
# No global provider — must be specified at runtime or per-step
api:
  retry:
    max_attempts: 3
  realtime:
    cost_cap_usd: 50.0

pipeline:
  steps:
    - name: generate
      prompt_template: generate.jinja2
      provider: gemini                    # This step always uses Gemini
      model: gemini-2.0-flash-001

    - name: evaluate
      prompt_template: evaluate.jinja2
      provider: openai                    # This step always uses OpenAI
      model: gpt-4o-mini
```

Running with `--provider anthropic` would override BOTH steps to use Anthropic (CLI wins).
Running without `--provider` would use the per-step providers (Gemini for generate, OpenAI for evaluate).

### Switching Providers at Runtime

You don't need to edit config.yaml to switch providers. Use CLI flags:

```bash
# Run a Gemini-configured pipeline with OpenAI instead
python scripts/orchestrate.py --init --pipeline MyPipeline --run-dir runs/test \
  --provider openai --model gpt-4o-mini --yes
```

Or use the TUI, which lets you select provider and model from dropdowns when starting a new run.

### Pipelines Without Provider Config

You can omit `api.provider` and `api.model` from config.yaml entirely. This creates a "provider-agnostic" pipeline that must be run with explicit `--provider` and `--model` flags (or via TUI selection):

```yaml
# Minimal api section - provider/model specified at runtime
api:
  retry:
    max_attempts: 3
    initial_delay_seconds: 30
  realtime:
    cost_cap_usd: 50.0
```

```bash
# Must specify provider when running
python scripts/orchestrate.py --init --pipeline MyPipeline --run-dir runs/test \
  --provider anthropic --model claude-haiku-4-5-20251001 --yes
```

---

### Automated Post-Processing

The `post_process` block runs scripts automatically after the pipeline completes. This is useful for:
- Generating summary reports from results
- Exporting data to CSV for analysis
- Running custom aggregation scripts

**Configuration:**

```yaml
post_process:
  - name: "Strategy Comparison"
    script: "scripts/analyze_results.py"
    args:
      - "--group-by"
      - "strategy"
      - "--count-field"
      - "result"
      - "--net-positive"
      - "player_wins"
      - "--net-negative"
      - "dealer_wins"
    output: "strategy_comparison.txt"

  - name: "Export CSV"
    script: "scripts/analyze_results.py"
    args:
      - "--group-by"
      - "strategy"
      - "--count-field"
      - "result"
      - "--output-format"
      - "csv"
    output: "results.csv"
```

**How it works:**
1. After all chunks complete, orchestrator runs each script in order
2. The `run_dir` is automatically passed as the first argument to each script
3. If `output` is specified, stdout is captured to that file in the run directory
4. If `output` is omitted, stdout is printed with `[POST-PROCESS]` prefix
5. Script failures are logged as warnings but don't fail the pipeline
6. Steps can be `type: script` (default) or `type: gzip` (built-in compression)

### Built-in Gzip Compression

Octobatch includes a built-in `type: gzip` post-processing step that compresses output files without requiring a separate script.

**Configuration:**

```yaml
post_process:
  - name: "Compress Results"
    type: gzip
    files:                          # Glob patterns relative to run_dir
      - "chunks/*/play_hand_validated.jsonl"
      - "combined_results.jsonl"
    keep_originals: false           # Optional (default: false) - delete after compression
```

**How it works:**
- Matches files using glob patterns against the run directory
- Compresses each file using gzip (creates .gz files)
- If `keep_originals` is false (default), deletes originals after successful compression
- Skips files that are already .gz
- Reports count of files compressed

**Example combining script and gzip steps:**

```yaml
post_process:
  - name: "Strategy Analysis"
    script: "scripts/analyze_results.py"
    args: ["--group-by", "strategy", "--count-field", "result"]
    output: "analysis.txt"

  - name: "Compress Raw Results"
    type: gzip
    files:
      - "chunks/*/*.jsonl"
    keep_originals: false
```

**Example output:**
```
[14:32:02] [POST-PROCESS] Running: Strategy Analysis...
[14:32:02] [POST-PROCESS] Strategy Analysis: Output written to runs/test/analysis.txt
[14:32:02] [POST-PROCESS] Compress Raw Results: Compressed 5 file(s)
```

> **Security note:** `type: script` steps execute arbitrary Python scripts. Only run pipelines from trusted sources. Review any `post_process` scripts before running a pipeline you didn't create.

---

## 2c. Unit Extraction & Cloud Upload

The `extract_units.py` script extracts validated units to individual files, with optional compression and cloud upload.

### Basic Configuration
```yaml
pipeline:
  steps:
    - name: generate
      prompt_template: generate.jinja2

    - name: extract_units
      scope: run
      script: scripts/extract_units.py
      output_dir: "outputs/units"
      filename_expression: "data['unit_id']"
      compression: gzip  # or "none"
```

### Configuration Options

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `output_dir` | string | `"outputs/units"` | Directory for extracted files (relative to run dir) |
| `filename_expression` | string | `"data['unit_id']"` | Python expression for filename (has access to `data` dict) |
| `content_expression` | string | `"data"` | Python expression for file content (default: full record) |
| `compression` | string | `"none"` | `"gzip"` or `"none"` |
| `upload` | object | none | Cloud upload configuration (see below) |

### Google Drive Upload

Upload extracted units to Google Drive automatically. Useful for long-running jobs on servers with limited storage.
```yaml
- name: extract_units
  scope: run
  script: scripts/extract_units.py
  output_dir: "outputs/units"
  filename_expression: "data['unit_id']"
  compression: gzip
  upload:
    provider: google_drive
    folder_id: "YOUR_SHARED_DRIVE_FOLDER_ID"
    credentials_env: GDRIVE_CREDENTIALS
    delete_local: true  # Delete local files after successful upload
```

**Setup Requirements:**

1. **Install dependencies** (not included in base requirements):
   ```bash
   pip install google-api-python-client google-auth
   ```

2. **Create a Google Cloud service account:**
   - Go to Google Cloud Console → IAM & Admin → Service Accounts
   - Create a service account and download the JSON key
   - Save the key file outside your repo (e.g., `~/.config/octobatch-gdrive.json`)

3. **Set the environment variable:**
   ```bash
   export GDRIVE_CREDENTIALS=~/.config/octobatch-gdrive.json
   ```

4. **Use a Shared Drive (required):**
   - Service accounts cannot upload to personal Drive folders
   - Create a Shared Drive (requires Google Workspace)
   - Add the service account email as a member with "Content Manager" role
   - Use the Shared Drive folder ID in your config

> **Note:** If you use a personal Drive folder instead of a Shared Drive, you'll see:
> ```
> Warning: Google Drive upload failed - Service accounts cannot upload to regular Drive folders.
> Solution: Use a Shared Drive (Google Workspace) instead of a personal Drive folder.
> ```
> Files will still be saved locally.

### Upload Options

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `provider` | string | required | Currently only `"google_drive"` supported |
| `folder_id` | string | required | Google Drive folder ID (from URL) |
| `credentials_env` | string | `"GDRIVE_CREDENTIALS"` | Environment variable containing path to service account JSON |
| `delete_local` | boolean | `false` | Delete local files after successful upload |

---

## 3. Unit Generation Strategies

### permutation (default)

All permutations without replacement from a single item pool.

**Use when:** Items are interchangeable and position matters.

**Example:** 22 cards placed in 3 positions (past/present/future)
- 22 × 21 × 20 = 9,240 unique units

```yaml
processing:
  strategy: permutation
  positions:
    - name: past_card
      name_field: name
    - name: present_card
      name_field: name
    - name: future_card
      name_field: name
  items:
    source: cards.yaml
    key: cards
    name_field: name
```

### cross_product

Cartesian product of items from different groups.

**Use when:** Combining distinct categories (e.g., characters × situations).

**Example:** 3 characters × 4 situations = 12 units

```yaml
processing:
  strategy: cross_product
  positions:
    - name: character
      source_key: characters       # Key in items.yaml
    - name: situation
      source_key: situations       # Key in items.yaml
  items:
    source: items.yaml
    name_field: id
```

Items file structure for cross_product:
```yaml
characters:
  - id: merchant
    name: "Village Merchant"
  - id: guard
    name: "Town Guard"

situations:
  - id: greeting
    context: "First meeting"
  - id: bargaining
    context: "Negotiating price"
```

### direct

Each item becomes one unit directly, no combination.

**Use when:** Items are already complete units (pre-defined scenarios, Monte Carlo simulations).

**Example:** 10 poker hands = 10 units (× repeat count if using Monte Carlo)

```yaml
processing:
  strategy: direct
  items:
    source: scenarios.yaml
    key: scenarios
    name_field: id
```

---

## 4. Template Syntax (Jinja2)

### Available Variables

```jinja2
{# === Unit Data === #}
{# All fields from your items are available directly #}
{{ unit_id }}
{{ name }}
{{ description }}

{# === Position Data (permutation/cross_product) === #}
{# Access fields via position name #}
{{ past_card.name }}
{{ past_card.description }}
{{ character.personality }}
{{ situation.context }}

{# === Expression Results === #}
{# If using expressions: in config #}
{{ random_card }}
{{ dice_roll }}

{# === Global Context === #}
{# From config.prompts.global_context #}
{{ game_name }}
{{ style }}

{# === Repetition Metadata (Monte Carlo) === #}
{# If using repeat: in config #}
{{ _repetition_id }}      {# 0 to N-1 #}
{{ _repetition_seed }}    {# Deterministic seed for this repetition #}

{# === Previous Step Outputs (multi-step pipelines) === #}
{# Output from step 1 is available in step 2 #}
{{ stories }}             {# Array from previous step #}
{{ score }}               {# Value from previous step #}
```

### Control Structures

```jinja2
{# Conditionals #}
{% if category == "special" %}
This is special handling.
{% else %}
Standard handling.
{% endif %}

{# Loops #}
{% for item in items %}
- Item {{ loop.index }}: {{ item.name }}
{% endfor %}

{# Filters #}
{{ name | upper }}
{{ description | truncate(100) }}
{{ items | length }}
```

### Output Format Instruction

Always tell the LLM exactly what JSON structure to return:

```jinja2
Respond with a JSON object in this exact format:
{
  "result": "your analysis here",
  "score": 1-10,
  "tags": ["tag1", "tag2"]
}

Do not include any text outside the JSON object.
```

---

## 5. Schema Format (JSON Schema Draft 2020-12)

### Basic Schema

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "type": "object",
  "required": ["field1", "field2"],
  "properties": {
    "field1": {
      "type": "string"
    },
    "field2": {
      "type": "integer",
      "minimum": 1,
      "maximum": 10
    }
  }
}
```

> ⚠️ **Important: Injected Fields**
>
> The orchestrator automatically injects `unit_id` and `_metadata` fields into every LLM response before schema validation. If you use `"additionalProperties": false` in your schema, validation will fail because these injected fields are "unexpected."
>
> **Solutions:**
> 1. **Recommended:** Omit `additionalProperties` entirely (the default allows extra fields)
> 2. **If you need strict validation:** Explicitly allow the injected fields:
>    ```json
>    "properties": {
>      "your_field": {"type": "string"},
>      "unit_id": {"type": "string"},
>      "_metadata": {"type": "object"}
>    }
>    ```

> **Schema Best Practices:**
> - **Never** use `"additionalProperties": false` — the orchestrator injects `unit_id` and `_metadata` into every response
> - **Avoid** overly restrictive numeric constraints (e.g., don't set `"maximum": 21` for a value that could exceed it)
> - **Use** `"minimum": 0` for counts and totals
> - **Prefer** loose validation — validate only what matters for your pipeline logic

### Common Patterns

**Enum (restricted values):**
```json
{
  "category": {
    "type": "string",
    "enum": ["option_a", "option_b", "option_c"]
  }
}
```

**Array with constraints:**
```json
{
  "items": {
    "type": "array",
    "minItems": 3,
    "maxItems": 5,
    "items": {
      "type": "object",
      "required": ["text"],
      "properties": {
        "text": {"type": "string", "minLength": 10}
      }
    }
  }
}
```

**Nested object:**
```json
{
  "metadata": {
    "type": "object",
    "required": ["author"],
    "properties": {
      "author": {"type": "string"},
      "timestamp": {"type": "string"}
    }
  }
}
```

**Boolean:**
```json
{
  "confirmed": {
    "type": "boolean"
  }
}
```

**Number with range:**
```json
{
  "score": {
    "type": "number",
    "minimum": 0,
    "maximum": 100
  }
}
```

---

## 6. Monte Carlo Simulation Features

For statistical simulations (gambling, probability, game theory):

### Configuration

```yaml
processing:
  strategy: direct
  repeat: 1000                    # Each scenario runs 1000 times
  expressions:
    # Random values evaluated fresh for each repetition
    dealer_card: "random.choice(['2','3','4','5','6','7','8','9','10','J','Q','K','A'])"
    player_hit: "random.choice(['2','3','4','5','6','7','8','9','10','J','Q','K','A'])"
    dice_roll: "random.randint(1, 6)"
  items:
    source: scenarios.yaml
    key: scenarios
    name_field: id
```

### Available Random Functions

| Function | Description | Example |
|----------|-------------|---------|
| `random.choice(seq)` | Pick random element | `random.choice(['A','K','Q'])` |
| `random.randint(a, b)` | Integer from a to b (inclusive) | `random.randint(1, 6)` |
| `random.random()` | Float 0.0 to 1.0 | `random.random()` |
| `random.uniform(a, b)` | Float from a to b | `random.uniform(0.5, 1.5)` |
| `random.sample(pop, k)` | Pick k unique elements | `random.sample(deck, 5)` |
| `random.gauss(mu, sigma)` | Gaussian distribution | `random.gauss(0, 1)` |

### Reproducibility

Each repetition gets:
- `_repetition_id`: Integer 0 to N-1
- `_repetition_seed`: Deterministic seed derived from unit_id

Same seed = same "random" results, enabling debugging of specific trials.

### Unit ID Format

With repeat, unit IDs become: `original_id__repNNNN`
- Example: `scenario_1__rep0042`

---

## 7. Multi-Step Pipelines

Steps execute in order. Each step's validated output becomes available to subsequent steps.

### Configuration

```yaml
pipeline:
  steps:
    - name: generate
      prompt_template: generate.jinja2
      description: "Generate initial content"

    - name: score
      prompt_template: score.jinja2
      description: "Score the generated content"

    - name: refine
      prompt_template: refine.jinja2
      description: "Refine based on scores"
```

### Accessing Previous Step Output

In step 2's template (`score.jinja2`):
```jinja2
{# The output from 'generate' step is available by field name #}
Review this story:

{{ story_text }}

Score it from 1-10 based on:
- Creativity
- Coherence
- Engagement
```

In step 3's template (`refine.jinja2`):
```jinja2
{# Both generate and score outputs are available #}
Original story: {{ story_text }}
Score received: {{ score }}
Feedback: {{ feedback }}

Please improve the story based on the feedback.
```

### Schema for Each Step

Each step needs its own schema:
- `schemas/generate.json`
- `schemas/score.json`
- `schemas/refine.json`

### Expression-Only Steps

Expression steps (`scope: expression`) perform data transformations without calling LLMs. They evaluate expressions for each unit and write results directly to output files. This is useful for:
- Computing derived values from previous step outputs
- Filtering or transforming data between LLM steps
- Adding calculated fields with seeded randomness

```yaml
pipeline:
  steps:
    - name: generate
      prompt_template: generate.jinja2
      description: "Generate content"

    - name: compute
      scope: expression
      description: "Compute derived values"
      expressions:
        word_count: "len(story_text.split())"
        is_long: "word_count > 500"
        random_score: "random.randint(1, 100)"
```

**Key characteristics:**
- No `prompt_template` required (and none used)
- No schema validation (expressions write directly to validated output)
- No API costs (tokens always 0)
- Uses seeded randomness via `_repetition_seed` for reproducibility
- Output includes all input fields plus computed expressions

**Expression context:**
- All input unit fields are available
- Previous step outputs merged into context
- Standard Python operators and functions
- `random` module with seeded state

**Example: Adding computed fields**
```yaml
- name: classify
  scope: expression
  description: "Classify by word count"
  expressions:
    word_count: "len(content.split())"
    length_class: "'short' if word_count < 100 else 'medium' if word_count < 500 else 'long'"
    sampled_value: "random.choice(['A', 'B', 'C'])"
```

### Looping Expression Steps

Expression steps can loop until a condition is met, enabling iterative simulations like dealing cards until bust or running trials until convergence.

```yaml
- name: deal_until_bust
  scope: expression
  description: "Deal cards until hand value exceeds 21"
  init:
    hand: "[]"
    total: "0"
  expressions:
    new_card: "random.choice(['A','2','3','4','5','6','7','8','9','10','J','Q','K'])"
    hand: "hand + [new_card]"
    total: "total + (10 if new_card in ['J','Q','K'] else 11 if new_card == 'A' else int(new_card))"
  loop_until: "total > 21"
  max_iterations: 100
```

**How it works:**

1. **`init` expressions** are evaluated once before the loop starts
2. **`expressions`** are evaluated on each iteration, updating the unit's state
3. **`loop_until`** condition is checked after each iteration
4. Loop exits when condition becomes `True` or `max_iterations` is reached

**Configuration:**

| Field | Required | Default | Description |
|-------|----------|---------|-------------|
| `init` | No | none | Expressions evaluated once before loop starts |
| `expressions` | Yes | — | Expressions evaluated each iteration |
| `loop_until` | No | none | Boolean expression; loop exits when True |
| `max_iterations` | No | 1000 | Safety limit to prevent infinite loops |

**Metadata added to output:**

| Field | Description |
|-------|-------------|
| `_metadata.iterations` | Number of iterations actually executed |

**Deterministic randomness:**

Each iteration uses a unique seed: `(base_seed + iteration) & 0x7FFFFFFF`. This ensures:
- Same unit + same iteration = same random values (reproducible)
- Different iterations = different random sequences (varied sampling)
- Results are fully reproducible given the same `_repetition_seed`

**Timeout Behavior:**

If a unit reaches `max_iterations` without satisfying the `loop_until` condition, it is treated as a **failure**:
- The unit is **not** included in the validated output
- It is logged as a timeout error
- It appears as a failure in the TUI and logs

If your simulation requires long loops, increase `max_iterations` in the step config.

---

## 8. Common Patterns

### Scoring/Rating Step

```yaml
# config.yaml
- name: score_quality
  prompt_template: score_quality.jinja2
```

```json
// schemas/score_quality.json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "type": "object",
  "required": ["score", "reasoning"],
  "properties": {
    "score": {
      "type": "integer",
      "minimum": 1,
      "maximum": 10
    },
    "reasoning": {
      "type": "string",
      "minLength": 20
    }
  }
}
```

```yaml
# config.yaml validation
validation:
  score_quality:
    required:
      - score
      - reasoning
    types:
      score: number
      reasoning: string
```

### Multiple Outputs Per Unit

```json
// Schema for generating multiple items
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "type": "object",
  "required": ["items"],
  "properties": {
    "items": {
      "type": "array",
      "minItems": 3,
      "maxItems": 5,
      "items": {
        "type": "object",
        "required": ["text", "category"],
        "properties": {
          "text": {"type": "string"},
          "category": {
            "type": "string",
            "enum": ["A", "B", "C"]
          }
        }
      }
    }
  }
}
```

### Classification Task

```jinja2
{# Template #}
Classify the following text into one of these categories:
- positive
- negative
- neutral

Text: {{ text }}

Respond with JSON:
{
  "classification": "positive|negative|neutral",
  "confidence": 0.0-1.0
}
```

```json
// Schema
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "type": "object",
  "required": ["classification", "confidence"],
  "properties": {
    "classification": {
      "type": "string",
      "enum": ["positive", "negative", "neutral"]
    },
    "confidence": {
      "type": "number",
      "minimum": 0,
      "maximum": 1
    }
  }
}
```

### Standard Post-Processing Stack

```yaml
# Standard post-processing for Monte Carlo simulations
post_process:
  # 1. Compress raw outputs (saves disk space)
  - name: "Compress Outputs"
    type: gzip
    files: ["chunks/*/*_validated.jsonl"]
    keep_originals: false

  # 2. Generate CSV for spreadsheet analysis
  - name: "Generate CSV"
    script: "scripts/analyze_results.py"
    args: ["--group-by", "YOUR_GROUP_FIELD", "--count-field", "YOUR_RESULT_FIELD", "--output-format", "csv"]
    output: "results.csv"

  # 3. Text summary grouped by key field
  - name: "Analysis Report"
    script: "scripts/analyze_results.py"
    args: ["--group-by", "YOUR_GROUP_FIELD", "--count-field", "YOUR_RESULT_FIELD"]
    output: "analysis.txt"
```

---

## 9. Debugging Failed Pipelines

### Understanding Failure Types

The TUI distinguishes two failure categories based on the `failure_stage` field in failure records:

| Category | `failure_stage` values | Color | Symbol | Retryable? |
|----------|----------------------|-------|--------|------------|
| **Validation failures** | `schema_validation`, `validation` | Yellow | ⚠ | Yes (R key) |
| **Hard failures** | `pipeline_internal` | Red | ✗ | No |

**Validation failures** mean the LLM returned output that didn't pass schema or business logic rules. These are retryable because a different LLM response may pass.

**Hard failures** mean records were lost in the pipeline (e.g., submitted to API but no response returned). These cannot be fixed by retrying.

> **Note:** API-level errors (rate limits, timeouts, auth failures) cause automatic chunk-level retries and do NOT appear as unit-level failures.

### Reading the TUI

**Home screen status column:**
- `complete` (green ✓) — all units validated, zero failures
- `complete ⚠ (N)` (yellow ⚠) — N validation failures (retryable)
- `failed` (red ✗) — run-level failure

**Pipeline funnel display (per-step throughput):**
```
┌──────────────────┐     ┌──────────────────┐     ┌──────────────────┐
│     GENERATE     │────▶│ SCORE COHERENCE  │────▶│  SCORE WOUNDS    │
│   ● 406/500      │     │   ● 262/406      │     │   ● 222/262  🏁  │
└──────────────────┘     └──────────────────┘     └──────────────────┘
```
Each step shows Valid/Input where:
- Step 1 input = global total_units (e.g., 500)
- Step N input = valid count from Step N-1 (e.g., 406, 262)
- 🏁 flag appears when valid == input (step fully complete)

**Pipeline step boxes (failure rows):**
```
⚠ 5 valid. fail    (yellow — schema/business logic failures)
✗ 2 failed          (red — lost records)
```

**Selected Step sidebar:**
```
Passed:        95
Validation:    5     (yellow)
Failed:        2     (red)
```

**Otto status narrator (below Otto animation):**
- "Waiting for Gemini..." — single Gemini provider active
- "Waiting for Claude..." — single Anthropic provider active
- "Otto is orchestrating..." — multiple providers or unknown
- "Otto finished the job!" — run complete
- "Otto hit a snag..." — run failed

**Retrying failures:**
1. Open run detail, switch to Unit View (V)
2. Press R to retry — only validation failures are retried
3. Hard failures are skipped (notification shown)
4. Behind the scenes: failures rotated to `.bak`, chunk state reset to PENDING
5. The `.bak` file prevents the orchestrator from SKIPping the retried step

### Using the Diagnostic Tool

1. Open run in TUI: `python scripts/tui.py`
2. Select the failed run
3. Press `D` for diagnostic
4. Review or paste diagnostic into Claude Code

### Common Errors and Fixes

| Error | Cause | Fix |
|-------|-------|-----|
| `'field' is a required property` | LLM didn't include required field | Make prompt clearer, add example JSON |
| `'value' is not one of ['a', 'b']` | LLM returned value not in enum | Update schema enum or prompt instructions |
| `'x' is not of type 'integer'` | Type mismatch (e.g., "5" vs 5) | Add explicit type instruction in prompt |
| `No validation config for step` | Missing `validation:` section | Add validation config to config.yaml |
| `Template not found` | Template path wrong | Check `prompts.templates` mapping |
| `Schema validation failed` | JSON structure mismatch | Compare LLM output to schema |

### Diagnostic Report Sections

The diagnostic (`D` key) generates:
- **Error Summary**: Groups errors by type with counts
- **Sample Failures**: Shows actual LLM response vs expected
- **Rendered Prompt**: What was sent to the LLM
- **Config Snapshot**: Current config, template, schema

### Iterative Debugging

1. Run with small max-units: `--max-units 5`
2. Check failures in TUI
3. Press `D` for diagnostic
4. Identify pattern (schema too strict? prompt unclear?)
5. Fix config/template/schema
6. Re-run test
7. Scale up when passing

---

## 10. Creating a New Pipeline

> **Implicit Defaults (don't specify unless overriding):**
> - `api.provider` / `api.model` — user specifies at runtime
> - `chunk_size: 100` — sensible default for most pipelines
> - `additionalProperties` in schemas — always allowed by default
> - Retry settings — defaults work for most cases

### Bootstrap Prompt

Before asking Claude Code to create a pipeline, initialize context with:

```
Read pipelines/TOOLKIT.md to understand Octobatch pipeline creation.
```

Then describe your pipeline requirements.

### Step-by-Step Process

1. **Create directory:**
   ```bash
   mkdir -p pipelines/MyPipeline/templates pipelines/MyPipeline/schemas
   ```

2. **Create items.yaml:**
   ```yaml
   items:
     - id: item_1
       name: "First Item"
       data: "..."
     - id: item_2
       name: "Second Item"
       data: "..."
   ```

3. **Create config.yaml** (see Section 2 for full reference)

4. **Create template** (`templates/generate.jinja2`):
   ```jinja2
   Process this item:
   Name: {{ name }}
   Data: {{ data }}

   Respond with JSON:
   {
     "result": "...",
     "success": true
   }
   ```

5. **Create schema** (`schemas/generate.json`):
   ```json
   {
     "$schema": "https://json-schema.org/draft/2020-12/schema",
     "type": "object",
     "required": ["result", "success"],
     "properties": {
       "result": {"type": "string"},
       "success": {"type": "boolean"}
     }
   }
   ```

6. **Test initialization:**
   ```bash
   python scripts/orchestrate.py --init --pipeline MyPipeline --run-dir runs/test --max-units 5 --yes
   ```

7. **Run in realtime mode:**
   ```bash
   python scripts/orchestrate.py --realtime --run-dir runs/test --yes
   ```

8. **Debug if needed:**
   ```bash
   python scripts/tui.py
   # Select run, press D for diagnostic
   ```

### Validation Checklist

Before running, verify:
- [ ] `config.yaml` has all required sections
- [ ] `validation:` section is present for each step
- [ ] Template files exist in `templates/`
- [ ] Schema files exist in `schemas/`
- [ ] `prompts.templates` maps step names to template files
- [ ] `schemas.files` maps step names to schema files
- [ ] Items file has correct structure for chosen strategy

### Config Validation Command

```bash
python scripts/orchestrate.py --validate-config --config pipelines/MyPipeline/config.yaml
```

---

## 11. Analyzing Monte Carlo Results

After running a simulation with `repeat: N`, use `scripts/analyze_results.py` to aggregate and analyze results.

### Count/Percentage Analysis

```bash
# Basic count - shows percentage of each outcome
python scripts/analyze_results.py runs/my_simulation \
  --group-by category --count-field outcome

# With net calculation (positive vs negative outcomes)
python scripts/analyze_results.py runs/my_simulation \
  --group-by strategy --count-field result \
  --net-positive success --net-negative failure
```

### Numeric Statistics

```bash
# Full statistics: mean, median, stdev, min, max
python scripts/analyze_results.py runs/my_simulation \
  --group-by model --numeric-field score

# With custom expressions (requires asteval)
python scripts/analyze_results.py runs/my_simulation \
  --group-by model --numeric-field score \
  --custom-stat "Spread=max_val-min_val" \
  --custom-stat "CV=stdev/mean if mean != 0 else 0"
```

### Output Formats

```bash
# ASCII table (default)
python scripts/analyze_results.py runs/sim --group-by x --count-field y

# CSV to stdout
python scripts/analyze_results.py runs/sim --group-by x --count-field y --output-format csv

# CSV to file
python scripts/analyze_results.py runs/sim --group-by x --count-field y --output-format csv --output results.csv
```

### Available Options

| Option | Description |
|--------|-------------|
| `--group-by FIELD` | Field to group results by |
| `--count-field FIELD` | Count occurrences of values in this field |
| `--numeric-field FIELD` | Calculate statistics for this numeric field |
| `--net-positive VALUE` | Values to count as +1 for net (repeatable) |
| `--net-negative VALUE` | Values to count as -1 for net (repeatable) |
| `--custom-stat "Name=Expr"` | Custom asteval expression (repeatable) |
| `--output-format FORMAT` | "table" (default) or "csv" |
| `--output FILE` | Write to file instead of stdout |
| `--title TEXT` | Title for table output |

### Expression Context (for --custom-stat)

When using `--custom-stat`, these variables are available:
- `data` - List of floats containing all values in the group (supports list operations and comprehensions)
- `count` - Number of values (integer)
- `mean`, `median`, `stdev`, `variance` - Pre-calculated statistics (floats)
- `min_val`, `max_val`, `sum_val` - Pre-calculated aggregates (floats)
- Math functions: `sqrt()`, `log()`, `exp()`, `abs()`, `sorted()`, `len()`, `sum()`, `min()`, `max()`

Example expressions:
- `"Spread=max_val-min_val"` - Range of values
- `"CV=stdev/mean if mean != 0 else 0"` - Coefficient of variation
- `"Pct95=sorted(data)[int(len(data)*0.95)]"` - 95th percentile (approximate)

---

## 12. Complete Example: Blackjack Monte Carlo Simulation

This example demonstrates the full Octobatch workflow: create pipeline → run simulation → analyze results.

### The Goal

Compare how different blackjack strategies perform on difficult hands using Monte Carlo simulation with 300 trials.

### Step 1: Create the Pipeline

Use this prompt with Claude Code:

```
Create a Blackjack Monte Carlo Simulation pipeline.

Requirements:
- 3 strategies: "The Pro" (basic strategy), "The Gambler" (aggressive), "The Coward" (never bust)
- 2 difficult hands: Player 16 vs Dealer 10, Player 12 vs Dealer 2
- Use `strategy: direct`, `repeat: 50`, and `expressions` for card randomness
- Output: action_log, player/dealer totals, result (enum), reasoning

Create in pipelines/Blackjack/ with 4-Point Lock verified.
```

### Step 2: Run the Simulation

```bash
# Initialize (6 scenarios × 50 reps = 300 units)
python scripts/orchestrate.py --init --pipeline Blackjack --run-dir runs/blackjack_full --yes

# Execute (cost varies by provider - Gemini is cheapest)
python scripts/orchestrate.py --realtime --run-dir runs/blackjack_full --yes
```

### Step 3: Analyze Results

**Option A: Manual analysis (run after pipeline completes)**
```bash
# Win/Loss rates by strategy
python scripts/analyze_results.py runs/blackjack_full \
  --group-by strategy --count-field result \
  --net-positive player_wins --net-negative dealer_wins \
  --title "Blackjack Strategy Comparison"

# Numeric analysis with volatility
python scripts/analyze_results.py runs/blackjack_full \
  --group-by strategy --numeric-field player_final_total \
  --custom-stat "Spread=max_val-min_val" \
  --title "Final Total Statistics"

# Export for spreadsheet analysis
python scripts/analyze_results.py runs/blackjack_full \
  --group-by strategy --count-field result \
  --net-positive player_wins --net-negative dealer_wins \
  --output-format csv --output blackjack_results.csv
```

**Option B: Automatic analysis (configure in config.yaml)**
```yaml
# Add to config.yaml to auto-run analysis when pipeline completes
post_process:
  - name: "Strategy Comparison"
    script: "scripts/analyze_results.py"
    args:
      - "--group-by"
      - "strategy"
      - "--count-field"
      - "result"
      - "--net-positive"
      - "player_wins"
      - "--net-negative"
      - "dealer_wins"
      - "--title"
      - "Blackjack Strategy Comparison"
    output: "strategy_comparison.txt"

  - name: "Results CSV"
    script: "scripts/analyze_results.py"
    args:
      - "--group-by"
      - "strategy"
      - "--count-field"
      - "result"
      - "--output-format"
      - "csv"
    output: "results.csv"
```

With `post_process` configured, running `--realtime` will automatically generate the analysis files in the run directory.

### Expected Output

```
Blackjack Strategy Comparison
=============================

Group       |  Total | dealer_wins | player_wins |     push |      Net
----------------------------------------------------------------------
the_coward  |    100 |       64.0% |       36.0% |     0.0% |      -28
the_pro     |    100 |       69.0% |       27.0% |     4.0% |      -42
the_gambler |    100 |       70.0% |       24.0% |     6.0% |      -46
----------------------------------------------------------------------
Total: 300 results
Top group by net: the_coward (-28)
```

### Key Insight

On these difficult hands, **all strategies lose money** (negative net), but "The Coward" loses *less* than mathematically optimal basic strategy. This counterintuitive result demonstrates the value of Monte Carlo simulation for testing assumptions against real data.

---

## 13. Testing Your Pipeline

After creating a pipeline, always verify it works with a small test run before scaling up.

### Standard Test Procedure
```bash
# 1. Initialize with a small sample (max-units 3 units, repeat 2 times)
python scripts/orchestrate.py --init --pipeline YOUR_PIPELINE --run-dir runs/test_v1 --max-units 3 --repeat 2 --yes --provider gemini

# 2. Run in realtime mode for quick feedback
python scripts/orchestrate.py --realtime --run-dir runs/test_v1
```

### Verification Checklist

1. **Validation:** Did all units pass? (Check logs for "0 failed")
2. **Multi-Step:** Did all steps execute in order?
3. **Post-Process:** Did scripts generate expected output files?
4. **Artifacts:** Are output files in `runs/test_v1/` correct?

### Troubleshooting Common Failures

If validation fails, inspect the failure log:
```bash
cat runs/test_v1/chunks/*/STEP_failures.jsonl | head -1 | python -m json.tool
```

**Common Issues:**

| Error | Cause | Fix |
|-------|-------|-----|
| `additionalProperties` | Orchestrator injects `unit_id` and `_metadata` | Remove `"additionalProperties": false` from schema |
| `maximum` exceeded | Numeric limits too strict (e.g., busted hand > 21) | Adjust or remove schema constraints |
| Missing required field | LLM omitted a field | Make prompt more explicit, or remove from `required` |

### Clean Up
```bash
rm -rf runs/test_v1
```

---

## Quick Reference

### Minimum Viable Config (with provider defaults)

```yaml
pipeline:
  steps:
    - name: generate
      prompt_template: generate.jinja2

api:
  provider: gemini                 # gemini | openai | anthropic
  model: gemini-2.0-flash-001

processing:
  strategy: direct
  chunk_size: 100
  items:
    source: items.yaml
    key: items
    name_field: id

prompts:
  template_dir: templates
  templates:
    generate: generate.jinja2

schemas:
  schema_dir: schemas
  files:
    generate: generate.json

# REQUIRED! Don't forget this!
validation:
  generate:
    required:
      - result
    types:
      result: string
```

### Minimum Viable Config (provider-agnostic)

Omit `api.provider` and `api.model` to require them at runtime:

```yaml
pipeline:
  steps:
    - name: generate
      prompt_template: generate.jinja2

api:
  retry:
    max_attempts: 3

processing:
  strategy: direct
  chunk_size: 100
  items:
    source: items.yaml
    key: items
    name_field: id

prompts:
  template_dir: templates
  templates:
    generate: generate.jinja2

schemas:
  schema_dir: schemas
  files:
    generate: generate.json

validation:
  generate:
    required:
      - result
    types:
      result: string
```

Run with: `--provider gemini --model gemini-2.0-flash-001`

### CLI Commands

```bash
# Validate config
python scripts/orchestrate.py --validate-config --config pipelines/X/config.yaml

# Initialize run (uses provider/model from config)
python scripts/orchestrate.py --init --pipeline X --run-dir runs/test --max-units N --yes

# Initialize run (override provider/model)
python scripts/orchestrate.py --init --pipeline X --run-dir runs/test \
  --provider openai --model gpt-4o-mini --yes

# Run realtime
python scripts/orchestrate.py --realtime --run-dir runs/test

# Run batch mode
python scripts/orchestrate.py --watch --run-dir runs/test

# Check status
python scripts/orchestrate.py --status --run-dir runs/test
```

### TUI

The TUI (`python scripts/tui.py`) provides a visual interface for:
- Creating new runs with provider/model selection ("Use Pipeline Config" default allows per-step overrides)
- Monitoring run progress in real-time with animated Otto the Octopus mascot
- **Pipeline funnel display**: Each step shows per-step throughput (Valid/Input) instead of global progress
- **Failure differentiation**: Yellow ⚠ for validation failures (retryable), red ✗ for hard failures
- **"complete ⚠ (N)" status**: Runs with validation failures show yellow count; failed runs show red
- **Otto status narrator**: Shows "Waiting for Gemini..." / "Waiting for Claude..." based on active providers
- **Scrollable stats sidebar**: Stats panels scroll independently; Otto animation stays fixed above
- Viewing run diagnostics and logs (S=save, C=copy, P=copy path)
- Managing multiple concurrent runs (pause, resume, kill, retry)
- Batch mode: pipeline boxes show "📤 N processing" / "⏳ N pending" status
- Provider-aware patience toast when batch mode idle >60 seconds
- **Splash screen**: Animated Otto overlay on startup with key passthrough to underlying screen
- **Retry logic**: R key retries only validation failures; hard failures skipped; failures archived to `.bak`

The TUI reads available providers and models from `scripts/providers/models.yaml`.

### TUI Screenshot Interpretation Guide

When debugging with screenshots, here's how to read the TUI:

**Home Screen:**
| Visual Element | Meaning |
|---------------|---------|
| Green ✓ + "complete" | All units validated, zero failures |
| Yellow ⚠ + "complete ⚠ (N)" | N validation failures remain (retryable) |
| Red ✗ + "failed" | Run-level failure (check logs) |
| Yellow number in Failed column | Validation failures (retryable) |
| Red number in Failed column | Hard failures (not retryable) |
| Spinner (◐◓◑◒) | Run actively processing |
| "detached" | Process died but run may still be valid |

**Detail Screen (Run View):**
| Visual Element | Meaning |
|---------------|---------|
| "406/500" in step box | 406 valid out of 500 input (funnel display) |
| "📤 N processing" | N chunks submitted to batch API |
| "⏳ N pending" | N chunks waiting to be submitted |
| Yellow "⚠ N valid. fail" | N validation failures for this step |
| Red "✗ N failed" | N hard failures (lost records) |
| 🏁 flag emoji | Step fully complete (valid == input) |
| "↻loops" (magenta) | Step has `loop_until` configuration |
| "Waiting for Gemini..." | Otto narrator: Gemini API calls in progress |
| "Otto is orchestrating..." | Multiple providers active or processing |

**Sidebar Stats (Selected Step):**
| Line | Meaning |
|------|---------|
| "Passed: N" | N units validated successfully |
| "Validation: N" (yellow) | N validation failures (retryable with R) |
| "Failed: N" (red) | N hard failures (not retryable) |
| "Processing: N" | N units currently being processed |
| "Provider: gemini (override)" | Step uses non-default provider |
