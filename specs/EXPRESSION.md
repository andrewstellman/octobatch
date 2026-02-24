# CURRENT_EXPRESSION.md — As-Is Intent Specification

## Purpose

This document describes what Octobatch's expression evaluation engine **should** do based on our design discussions. Written from intent, not code inspection. Gaps between this spec and the actual implementation are defects.

**Quality mapping:** Maps primarily to QUALITY.md Scenario 7 (Statistical Correctness — The Drunken Sailor Bug) and the generalized lesson about pure functions with hidden corruption risk.

---

## What Expression Steps Are

Expression steps are pipeline steps that run locally on the user's machine with no LLM call. They evaluate Python-like expressions using `asteval`, a safe expression evaluator. Expression steps are free, instant, and deterministic.

An expression step has `scope: expression` in the pipeline config:

```yaml
- name: deal_cards
  scope: expression
  description: "Deal random cards from a 6-deck shoe"
  expressions:
    shoe: "['2','3','4','5','6','7','8','9','10','J','Q','K','A'] * 24"
    dealt: "random.sample(shoe, 14)"
    player_cards: "[dealt[0], dealt[1]]"
    dealer_up_card: "dealt[2]"
```

Expression steps are **exempt from the 4-Point Link Rule** — they only need the `pipeline.steps` entry. No template, schema, or validation config required.

---

## The Expression Evaluator

### Implementation

`scripts/expression_evaluator.py`

### Core Behavior

The evaluator takes a dictionary of expression definitions and a context dictionary, and returns evaluated results:

```python
result = evaluate_expressions(
    expressions={'card': "random.choice(['A','K','Q'])", 'roll': 'random.randint(1,6)'},
    context={'player': 'test'},
    seed=42
)
```

### Evaluation Order

Expressions are evaluated **in definition order**. Later expressions can reference earlier ones:

```yaml
expressions:
  total: "player_hand[0] + player_hand[1]"
  is_blackjack: "total == 21 and len(player_hand) == 2"
  category: "'high' if total >= 17 else 'medium' if total >= 12 else 'low'"
```

Each evaluated expression is added to the context before the next one runs. So `is_blackjack` can reference `total` because `total` was just computed and added to context.

### Available in Expressions

#### Random Module

| Function | Description |
|----------|-------------|
| `random.choice(seq)` | Pick one random element |
| `random.randint(a, b)` | Random integer, inclusive |
| `random.random()` | Float from 0.0 to 1.0 |
| `random.uniform(a, b)` | Float from a to b |
| `random.sample(pop, k)` | Pick k unique elements |
| `random.shuffle(x)` | Shuffle list x in-place |
| `random.gauss(mu, sigma)` | Gaussian distribution |

#### Built-in Functions

`len`, `int`, `str`, `float`, `bool`, `min`, `max`, `sum`, `abs`, `round`, `sorted`, `list`, `dict`, `tuple`, `set`, `range`, `enumerate`, `zip`, `map`, `filter`, `any`, `all`

#### Operators

All standard Python operators: arithmetic, comparison, logical, membership (`in`, `not in`), and ternary expressions (`x if condition else y`).

#### Data Structures

List comprehensions, slicing, concatenation, string methods, dictionary access.

### Context Variables

All unit fields are available as variables in expressions:
- Fields from the items file (e.g., `name`, `keywords`)
- Fields from previous pipeline steps (accumulated via merge)
- Fields from unit generation (e.g., `past_card`, `present_card`, `future_card` for permutation strategy)
- Generated metadata: `unit_id`, `_repetition_id`, `_repetition_seed`

---

## Seeded Randomness

### The Core Principle

Every unit gets a deterministic random seed so that:
- **Reproducible:** The same unit always gets the same random values
- **Debuggable:** You can re-run a specific unit and get identical results
- **Statistically sound:** Different units get different, uncorrelated seeds

### Seed Derivation

The seed for each expression step is derived from both the unit ID and the step name:

```
seed = hash(unit_id + step_name) & 0x7FFFFFFF
```

- **`step_name` is included** so that two expression steps processing the same unit produce different random sequences. Without this, a `deal_cards` step and a `roll_dice` step on the same unit would get identical random values.
- **`& 0x7FFFFFFF`** masks the hash to a positive 31-bit integer, ensuring a valid seed value regardless of platform hash behavior.
- **`_repetition_seed` override:** If the unit has a `_repetition_seed` field (set by Monte Carlo `repeat: N` pipelines), that value is used as the base seed instead of hashing `unit_id`. This ensures each repetition gets a unique, deterministic seed derived from the repetition index.

**Why hash-based seeding matters — The Drunken Sailor Bug:**

An earlier implementation used `random.seed(unit_index)` where `unit_index` was a sequential integer (0, 1, 2, ...). This created correlated random sequences across units because sequential integers produce related random streams in Python's Mersenne Twister. The result: a Monte Carlo random walk simulation showed 77.5% probability of falling in the water instead of the theoretical ~50%.

The fix was to use `hash(unit_id)` which produces well-distributed seeds from string identifiers, breaking the sequential correlation.

**Generalized lesson:** This is a class of bug where pure functions that look simple but handle randomness produce plausible-but-wrong output. Only statistical verification catches them. This is why the expression evaluator has a 100% test coverage target in QUALITY.md.

### Stream-Based Randomness in Loops

For looping expression steps, there is **no per-iteration reseeding**. A single `SeededRandom` instance is created before the loop begins, and the RNG state advances naturally as iterations call random functions.

This means:
- **Iteration N's random values depend on all prior iterations' random calls.** The RNG is a single continuous stream, not independent per-iteration streams.
- **Results are deterministic and reproducible** given the same seed — the same unit always produces the same sequence of random values across all iterations.
- **Different iterations get different random values** because the RNG state has advanced from prior calls, not because of reseeding.

### Repetition Seeds

For Monte Carlo pipelines using `repeat: N`, each repetition gets a unique `_repetition_seed` derived from the unit ID and repetition index. The unit ID becomes `{original_id}__rep{NNNN}`.

---

## Looping Expressions (`loop_until`)

Expression steps can loop until a condition is met, enabling iterative simulations.

### Config Structure

```yaml
- name: random_walk
  scope: expression
  init:
    position: "start_position"
    path: "[start_position]"
    steps_taken: "0"
  expressions:
    move: "random.choice([-1, 1])"
    position: "position + move"
    path: "path + [position]"
    steps_taken: "steps_taken + 1"
  loop_until: "position <= 0 or position >= 10"
  max_iterations: 1000
```

### Execution Flow

1. **Seed and create SeededRandom** — A single `SeededRandom` instance is created using the derived seed (see Seed Derivation above).

2. **Init block** — Evaluated once before the loop starts. Sets up initial state variables. Init expressions can reference unit fields (e.g., `start_position` from the items file). Random calls in init use the same `SeededRandom` instance that the expression block will use.

3. **Expression block** — Evaluated every iteration, in definition order. Each expression can reference variables from init, from previous expressions in this iteration, or from the previous iteration's state.

4. **Loop condition** — `loop_until` expression evaluated after each iteration. When it evaluates to `True`, the loop exits.

5. **Safety limit** — `max_iterations` (default 1000) prevents infinite loops. If reached, the loop exits and the unit is **included in the output** with timeout metadata (see Output Metadata below). Reaching `max_iterations` is **not** a failure.

**Shared SeededRandom instance:** The init block and expressions block share a single `SeededRandom` instance. The RNG state advances continuously from init into the expression loop, so init and expressions naturally produce different random values even though they use the same seed. This is the intended behavior.

### Output Metadata

The output includes:
- All final expression values
- `_metadata.iterations`: How many iterations the loop ran
- `_metadata.timeout`: Boolean flag — `True` if `max_iterations` was reached before `loop_until` triggered

**Timeout behavior:** When `max_iterations` is reached, the unit is **included in the output** with `_metadata.timeout: true` and `_metadata.iterations: N`. It is **not** treated as a failure — the unit proceeds through the rest of the pipeline with whatever state it had at the iteration limit. This is the intended behavior.

### Typical Use Cases

- **Random walks** (Drunken Sailor): Walk until reaching a boundary
- **Convergence simulations**: Iterate until a value stabilizes
- **Game simulations**: Play turns until a win/loss condition

---

## Expression Steps in the Orchestrator

### Batch Mode

In `tick_run()`, expression steps are detected and handled locally:

1. Check if current step has `scope: expression`
2. Evaluate expressions for all units in the chunk
3. Write results directly to `{step}_validated.jsonl`
4. Advance chunk state to the next step
5. No batch API call is made

Expression steps skip the `SUBMITTED` state entirely — they go directly from `{step}_PENDING` to `{next_step}_PENDING`.

### Realtime Mode

Identical behavior — expression steps are processed locally regardless of execution mode. No difference between batch and realtime for expression steps.

### No Validation Phase

Expression steps do not go through the schema/business-logic validation pipeline. The expressions either evaluate successfully or throw an error. There's no "the expression returned a wrong answer" concept — if the expression runs, the result is correct by definition (assuming the expression itself is correct).

---

## Config Validation for Expressions

`--validate-config` tests expression syntax with mock context:

1. Creates a mock context dictionary with placeholder values for each unit field
2. Evaluates each expression in the step's `expressions` block
3. If `init` is present, evaluates those first
4. Reports syntax errors, undefined variable references, and type errors

This catches problems like:
- `random.choice([A, K])` (missing quotes — `A` and `K` are undefined variables)
- Unbalanced parentheses
- References to fields that don't exist
- Using Python features not supported by asteval

### Limitations of Config Validation

Config validation uses mock data (empty strings, zeros), so it can't catch:
- Runtime type errors (e.g., `len(field)` where `field` is actually an integer at runtime)
- Logical errors (expression evaluates but produces wrong results)
- Issues that only appear with real data distributions

These require actual test runs with `--max-units 5`.

---

## The `validate_expression()` Function

A standalone function for testing individual expressions:

```python
from scripts.expression_evaluator import validate_expression

valid, error = validate_expression("random.choice(['A','K','Q'])")
# valid=True, error=None

valid, error = validate_expression("invalid syntax here")
# valid=False, error="..."
```

Used by `config_validator.py` and available for interactive testing.

---

## Data Flow for Expression Steps

```
Previous step's {step}_validated.jsonl (or units.jsonl for first step)
    │
    ▼
Load unit data + accumulated fields from all prior steps
    │
    ▼
For each unit:
  1. Derive seed: hash(unit_id + step_name) & 0x7FFFFFFF
     (or use _repetition_seed if present)
  2. Create a single SeededRandom instance with that seed
  3. Evaluate init expressions (if present), using SeededRandom
  4. Evaluate expression block, using same SeededRandom
  5. If loop_until: repeat step 4 until condition met or max_iterations
     (RNG state advances naturally across iterations)
  6. Collect final values (include _metadata.timeout if max_iterations hit)
    │
    ▼
Write {step}_validated.jsonl
    │
    ▼
Advance chunk state to next step
```

---

## Known Issues and Technical Debt

1. **No expression step validation rules** — Expression steps bypass the validation pipeline entirely. If an expression produces an unexpected type or value, there's no way to catch it short of the next LLM step failing. We may want optional validation rules for expression outputs.

2. **Mock context limitations** — `--validate-config` uses placeholder values that may not match real data shapes. An expression like `items[0]['name']` will fail at runtime if `items` is actually a string, but config validation won't catch this.

3. **No partial failure handling** — If one unit's expression evaluation fails (e.g., division by zero with specific data), the entire chunk may fail depending on error handling. Intent is that individual unit failures should be recorded in a failures file, same as LLM step failures.

4. **asteval security surface** — While `asteval` is sandboxed, the full security surface hasn't been audited. Expression steps accept user-defined code strings. For a single-user tool this is acceptable, but it should be noted.

5. **No expression profiling** — For looping steps, there's no way to see how many iterations each unit took without checking the output. A summary log line ("average 25 iterations, max 847, 3 timeouts") would help operational monitoring.
