# Pipelines Directory Context
> **File:** `pipelines/CONTEXT.md`

## 1. Purpose

This directory contains pipeline configurations for the Octobatch batch processing system. Each pipeline is a self-contained package with configuration, templates, schemas, and item sources that define how units are generated and processed through LLM steps.

## 2. Directory Structure

```
pipelines/
├── ExamplePipeline/
│   ├── config.yaml                 # Main pipeline configuration
│   ├── items.yaml                  # Items source data
│   ├── templates/
│   │   ├── generate_prompt.jinja2
│   │   ├── score_prompt.jinja2
│   │   └── ...
│   └── schemas/
│       ├── generate.json
│       ├── score.json
│       └── ...
│
└── AnotherPipeline/
    ├── config.yaml
    ├── items.yaml
    ├── templates/
    │   └── generate.jinja2
    └── schemas/
        └── output.json
```

## 3. Config.yaml Structure

### Top-Level Sections

**`pipeline:`** - Defines ordered sequence of processing steps
```yaml
pipeline:
  steps:
    - name: generate           # Step identifier
      description: Generate stories
      prompt_template: story_generation_prompt.jinja2
      requests_per_triple: 1   # API calls per unit
      outputs_per_request: 4   # Results per request
      depends_on: null         # Previous step (optional)
      scope: chunk             # "chunk" (default) or "run"
```

**Expression Steps:**
Steps with `scope: expression` perform local computation without LLM calls. They evaluate expressions for each unit and write directly to validated output. These steps:
- Don't require `prompt_template`
- Don't require schema or validation entries
- Use seeded randomness via `_repetition_seed`
- Have zero API cost

Example:
```yaml
- name: compute_stats
  scope: expression
  expressions:
    word_count: "len(content.split())"
    is_long: "word_count > 500"
```

**Looping Expression Steps:**
Expression steps can loop until a condition is met using `loop_until`:
```yaml
- name: deal_until_bust
  scope: expression
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
- `init`: Expressions evaluated once before loop starts
- `loop_until`: Boolean condition; loop exits when True
- `max_iterations`: Safety limit (default 1000)
- Output metadata includes `iterations` count and `timeout` flag

**`api:`** - API provider and rate limiting
```yaml
api:
  # Provider and model are OPTIONAL - can be specified via CLI (--provider, --model) or TUI
  provider: gemini             # gemini | openai | anthropic (optional default)
  model: gemini-2.0-flash-001  # See scripts/providers/models.yaml for available models
  max_inflight_batches: 5
  poll_interval_seconds: 30
  retry:
    max_attempts: 3
    initial_delay_seconds: 5
    backoff_multiplier: 2.0
  realtime:
    cost_cap_usd: 50.0
    auto_retry: true
  subprocess_timeout_seconds: 600  # Default: 600. Timeout for validation/prompt subprocesses.
```

> **Note:** Pricing is registry-driven from `scripts/providers/models.yaml`. Do NOT add pricing blocks to pipeline configs. Provider/model resolution precedence: CLI flags > Step config > Global config > registry default_model.

**Per-step provider/model overrides:**
Individual steps can override the global or CLI provider/model:
```yaml
pipeline:
  steps:
    - name: generate
      prompt_template: generate.jinja2
      provider: gemini              # Use Gemini for generation (cheap)
      model: gemini-2.0-flash-001
    - name: score
      prompt_template: score.jinja2
      provider: anthropic           # Use Anthropic for scoring (quality)
      model: claude-sonnet-4-5-20250929
```
Resolution order: CLI flags (highest) > Step config > Global `api:` config.

**`processing:`** - Unit generation and chunking
```yaml
processing:
  strategy: permutation        # Unit generation strategy (see below)
  chunk_size: 100
  validation_retry:
    max_attempts: 3
  positions:                   # Defines unit structure
    - name: past_card
    - name: present_card
    - name: future_card
  items:
    source: cards.yaml         # Item source file
    key: cards                 # Top-level key in source
    name_field: name           # Field for unit_id generation
```

**Unit Generation Strategies:**
- **`permutation`** (default): All permutations without replacement from single item pool
- **`cross_product`**: Cartesian product of items from different groups (positions specify source_key)
- **`direct`**: Each item becomes one unit directly (no combination)

**`prompts:`** - Jinja2 template configuration
```yaml
prompts:
  template_dir: templates
  templates:
    generate: story_generation_prompt.jinja2
    score_coherence: coherence_scoring_prompt.jinja2
  global_context:
    game_name: "My Game"
    era_style: "fantasy medieval"
```

**`schemas:`** - JSON Schema validation
```yaml
schemas:
  schema_dir: schemas
  files:
    generate: story.json
    score_coherence: coherence.json
  strict_mode: false
  log_validation_errors: true
```

**`validation:`** - Expression-based business logic
```yaml
validation:
  generate:
    required:
      - stories
    types:
      stories: array
    rules:
      - name: story_count
        expr: "len(stories) == 4"
        error: "Expected 4 stories, got {_computed}"
        level: error
      - name: unique_emphasis
        expr: "len(set(s['emphasis'] for s in stories)) == 4"
        error: "Duplicate emphasis tags found"
        level: error
```

## 4. Template Files (.jinja2)

### Input Variables
Templates receive unit data plus global context:
```python
{
    # Position data (one item per position)
    "past_card": {"name": "...", "description": "...", ...},
    "present_card": {...},
    "future_card": {...},

    # Previous step outputs (if depends_on set)
    "stories": [...],

    # Global context
    "game_name": "My Game",
    "era_style": "fantasy medieval",

    # System fields (from config global_context)
    "categories": [...]  # If defined in config
}
```

### Output Format
Templates instruct LLM to return JSON matching the schema:
```jinja2
Return a JSON object with this structure:
{
  "stories": [
    {
      "text": "Your story text here...",
      "path": "integration" or "fragmentation",
      "emphasis": "ONE_OF_THE_TAGS",
      "journal_entry": "Brief reflection"
    }
  ]
}
```

## 5. Schema Files (.json)

JSON Schema Draft 2020-12 format for response validation:

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "type": "object",
  "required": ["stories"],
  "properties": {
    "stories": {
      "type": "array",
      "minItems": 4,
      "maxItems": 4,
      "items": {
        "type": "object",
        "required": ["text", "path", "emphasis"],
        "properties": {
          "text": {"type": "string", "minLength": 100},
          "path": {"enum": ["integration", "fragmentation"]},
          "emphasis": {"enum": ["CLINGING", "AVOIDANCE", ...]}
        }
      }
    },
    "unit_id": {"type": "string"},
    "_metadata": {"type": "object"}
  }
}
```

## 6. Items Source Files

YAML files containing items for unit generation. Structure varies by strategy:

### For permutation strategy (single item pool)
```yaml
# items.yaml
items:
  - name: "Item Alpha"
    description: "A versatile starting element..."
    category: "Generation"
    primary_use: "Foundation building"
    secondary_use: "Combination catalyst"
```

### For cross_product strategy (grouped items)
```yaml
# dialogs.yaml
characters:
  - id: merchant
    name: "Village Merchant"
    personality: "Friendly but shrewd"
  - id: guard
    name: "Town Guard"
    personality: "Stern but fair"

situations:
  - id: greeting
    name: "First Meeting"
    context: "Player approaches NPC"
  - id: bargaining
    name: "Bargaining"
    context: "Player is negotiating"
```

### For direct strategy (complete units)
```yaml
# hands.yaml
hands:
  - id: flush_draw
    name: "Flush Draw"
    cards: ["Ah", "9h"]
    community_cards: ["Kh", "3h", "5c"]
    position: cutoff
    stage: flop
```

## 7. Unit Generation

### Strategy: permutation (default)
Units are permutations without replacement from a single item pool:
- 22 items × 3 positions = 22 × 21 × 20 = 9,240 unique triples
- Each unit gets `unit_id` from position values: `Item_Alpha-Item_Beta-Item_Gamma`
- Config: No strategy field needed (default), or `strategy: permutation`

### Strategy: cross_product
Cartesian product of items from different groups:
- Each position specifies `source_key` to identify the group in the source file
- 3 characters × 3 situations = 9 units
- Example unit_id: `merchant-greeting`

```yaml
processing:
  strategy: cross_product
  positions:
    - name: character
      source_key: characters    # Key in dialogs.yaml
    - name: situation
      source_key: situations    # Key in dialogs.yaml
  items:
    source: dialogs.yaml
    name_field: id
```

Source file structure:
```yaml
characters:
  - id: merchant
    name: "Village Merchant"
    personality: "Friendly but shrewd"
situations:
  - id: greeting
    name: "First Meeting"
    context: "Player approaches NPC"
```

### Strategy: direct
Each item becomes one unit directly (no combination):
- 5 hands = 5 units
- Unit inherits all item fields directly
- Positions are optional
- Example unit_id: `flush_draw`

```yaml
processing:
  strategy: direct
  items:
    source: hands.yaml
    key: hands
    name_field: id
```

## 8. Processing Flow

```
Items Source (cards.yaml)
    │
    ▼
Unit Generation (permutations)
    │
    ▼
Chunking (100 units per chunk)
    │
    ▼
For each chunk, for each step:
    │
    ├── Template Rendering → prompts.jsonl
    ├── API Call (batch or realtime)
    ├── Schema Validation → failure_stage: "schema_validation" if fails
    ├── Expression Validation → failure_stage: "validation" if fails
    └── State Update → next step
    │
    ▼
VALIDATED or FAILED

Failure Categories:
    schema_validation / validation → Retryable (yellow ⚠ in TUI)
    pipeline_internal              → Hard failure (red ✗ in TUI)

TUI Funnel View (per-step throughput):
    Step 1 (Generate):          406/500  (81% — 406 valid out of 500 input)
    Step 2 (Score Coherence):   262/406  (64% — 262 valid out of 406 from Step 1)
    Step 3 (Score Wounds):      222/262  (85% — 222 valid out of 262 from Step 2)
    Each step's input = previous step's valid count (Step 1 input = total_units)
```

## 9. Creating New Pipelines

1. Create directory: `pipelines/MyPipeline/`
2. Copy skeleton config.yaml
3. Define items source with positions
4. Create templates for each step
5. Create schemas for validation
6. Add validation rules as needed
7. Test with `--validate-config` and small `--max-units`

### Skeleton Configs

**Permutation Strategy (default):**
```yaml
pipeline:
  steps:
    - name: generate
      description: Generate content

api:
  provider: gemini             # Optional - can use --provider flag instead
  model: gemini-2.0-flash-001  # Optional - can use --model flag instead

processing:
  chunk_size: 100
  positions:
    - name: first_item
    - name: second_item
  items:
    source: items.yaml
    key: items
    name_field: name

prompts:
  template_dir: templates

schemas:
  schema_dir: schemas
```

**Cross-Product Strategy:**
```yaml
processing:
  strategy: cross_product
  chunk_size: 100
  positions:
    - name: character
      source_key: characters
    - name: situation
      source_key: situations
  items:
    source: dialogs.yaml
    name_field: id
```

**Direct Strategy:**
```yaml
processing:
  strategy: direct
  chunk_size: 100
  items:
    source: hands.yaml
    key: hands
    name_field: id
```

## 10. Testing

```bash
# Validate config
python scripts/orchestrate.py --validate-config --config pipelines/Example/config.yaml

# Test with limit
python scripts/orchestrate.py --init --pipeline Example --run-dir runs/test --max-units 5

# Run realtime
python scripts/orchestrate.py --realtime --run-dir runs/test --yes
```

## 11. Repetition for Monte Carlo Simulation

For statistical simulations, use `repeat: N` to run each unit N times:

```yaml
processing:
  strategy: direct
  repeat: 1000  # Each unit runs 1000 times
  chunk_size: 100
  items:
    source: scenarios.yaml
    key: scenarios
    name_field: id
```

Each repetition gets:
- `_repetition_id`: Integer 0 to N-1
- `_repetition_seed`: Deterministic seed for reproducible randomness (derived from unit_id)
- Modified `unit_id`: Original ID + `__repNNNN` suffix (e.g., `scenario_1__rep0042`)

**Example usage in template:**
```jinja2
Scenario: {{ scenario.name }}
Random seed for this trial: {{ _repetition_seed }}
Trial number: {{ _repetition_id + 1 }} of 1000
```

**Testing:**
```bash
# Generate 6 units (2 base × 3 repetitions)
python scripts/generate_units.py --config pipelines/TestRepeat/config.yaml --max-units 2
```

## 12. Expressions for Dynamic Values

Use `expressions` to inject computed or random values into each unit:

```yaml
processing:
  strategy: direct
  repeat: 1000
  expressions:
    next_card: "random.choice(['2','3','4','5','6','7','8','9','10','J','Q','K','A'])"
    dice_roll: "random.randint(1, 6)"
    hit_or_stand: "random.choice(['hit', 'stand'])"
```

Expressions are evaluated using asteval with a seeded random module:
- Each repetition uses `_repetition_seed` for reproducibility
- Same seed = same "random" results (enables debugging specific trials)
- Expression results are added to unit data before template rendering

### Available in expressions:
- `random.choice(seq)` - Pick random element from sequence
- `random.randint(a, b)` - Random integer from a to b (inclusive)
- `random.random()` - Random float 0.0 to 1.0
- `random.uniform(a, b)` - Random float from a to b
- `random.sample(population, k)` - Pick k unique elements
- `random.gauss(mu, sigma)` - Gaussian distribution
- All unit fields (e.g., `unit_id`, `player_hand`, custom fields)

### Example: Blackjack simulation
```yaml
processing:
  strategy: direct
  repeat: 1000
  expressions:
    dealer_hidden: "random.choice(['2','3','4','5','6','7','8','9','10','J','Q','K','A'])"
    player_hit_card: "random.choice(['2','3','4','5','6','7','8','9','10','J','Q','K','A'])"
```

Template can then use `{{ dealer_hidden }}` and `{{ player_hit_card }}`.

### Testing expressions:
```bash
# Test expression evaluation directly
python -c "
from scripts.expression_evaluator import evaluate_expressions
result = evaluate_expressions(
    {'card': \"random.choice(['A','K','Q'])\", 'roll': 'random.randint(1,6)'},
    {'player': 'test'},
    seed=42
)
print(result)
# Should print same result every time with seed=42
"

# Test validation
python -c "
from scripts.expression_evaluator import validate_expression
print(validate_expression(\"random.choice(['A','K'])\"))  # Should be (True, '')
print(validate_expression('invalid syntax here'))  # Should be (False, '...')
"
```

## 13. Known Limitations

- Items source must be YAML (no JSONL support)
- Templates must output valid JSON (no streaming)
- cross_product strategy requires all groups to have items (empty groups cause errors)
