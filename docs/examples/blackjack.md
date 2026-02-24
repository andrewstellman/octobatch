# Blackjack Walkthrough

[← Back to README](../../README.md) | [← Back to Examples](../../README.md#demo-pipeline-walkthroughs) | [Core Concepts](../core-concepts.md) | [Expression Steps](../expression-steps.md)

A strategy comparison pipeline that demonstrates the full power of Octobatch: expression steps dealing cards, multi-step LLM chains, the LLM-as-validator pattern with automatic retries, and post-processing analysis.

## Concept

Three blackjack strategies play out 100 hands each (300 total). An expression step deals the cards (free, deterministic). An LLM plays each hand according to the assigned strategy. A second LLM validates that the strategy was followed correctly, retrying inaccurate plays. A third LLM rates the difficulty of the decisions faced.

The result: win rates per strategy, accuracy scores, and difficulty distribution — all generated and validated automatically.

## The Prompt

This prompt was used to generate the pipeline with Claude Code:

```
Read pipelines/TOOLKIT.md to understand Octobatch pipeline creation.

Create a Blackjack strategy comparison pipeline.

### Concept
Compare three betting strategies by simulating blackjack hands. This pipeline
demonstrates expression steps, multi-step LLM chains, and validation with retries. An
expression step deals cards, an LLM plays the hand, a second LLM validates that the
strategy was followed correctly (retrying failures), then a third LLM analyzes
decision difficulty.

### Strategies
- "The Pro": Follows basic strategy religiously. Hits on 16 against dealer 7 or
  higher, stands on 17+, doubles down on 11, splits aces and eights.
- "The Gambler": Plays aggressively and takes risks. Always hits on 16 regardless of
  dealer card, doubles down on any 10 or 11, chases the win even when the odds are bad.
- "The Coward": Terrified of busting. Stands on any 12 or higher no matter what, never
  doubles down, never splits — would rather lose slowly than risk going over 21.

### Pipeline Steps

**Step 1: Deal Cards (Expression)**
Deal random cards from a 6-deck shoe: player's initial two cards, dealer's up card,
dealer's hole card, and a shoe of extra cards for potential hits. No LLM needed.

**Step 2: Play Hand (LLM)**
Play out the hand according to the assigned strategy. Output the sequence of actions,
final totals, and the result (player wins, dealer wins, or push).

**Step 3: Validate Strategy (LLM)**
Score how accurately the strategy was followed from 0.0 to 1.0. Use Octobatch's
validation system to enforce strategy accuracy >= 0.7. Hands below this threshold
will automatically fail validation and be retried.

**Step 4: Analyze Difficulty (LLM)**
Rate the difficulty of the decisions (easy, medium, hard) based on notorious blackjack
situations.

### Requirements
- Three items (one per strategy)
- Run each strategy 100 times (300 total hands)
- Validation threshold enforced via config.yaml expression rule

Create in pipelines/Blackjack/
```

## Pipeline Structure

```
┌─────────────┐    ┌─────────────┐    ┌──────────────────┐    ┌────────────────────┐
│ deal_cards   │───▶│ play_hand   │───▶│ validate_strategy │───▶│ analyze_difficulty  │
│ expression   │    │ LLM         │    │ LLM + gate       │    │ LLM                │
│              │    │             │    │ accuracy >= 0.7   │    │                    │
└─────────────┘    └─────────────┘    └──────────────────┘    └────────────────────┘
```

**Step 1: deal_cards** — Expression step. Deals 14 cards from a 6-deck shoe (312 cards). Free, instant, reproducible.

**Step 2: play_hand** — LLM plays the hand following the assigned strategy. Outputs action_log, final totals, and result (player_wins/dealer_wins/push).

**Step 3: validate_strategy** — LLM reviews the play and scores strategy adherence from 0.0 to 1.0. Expression rule enforces `strategy_accuracy >= 0.7` — hands below this fail and the entire pipeline retries for that unit.

**Step 4: analyze_difficulty** — LLM rates decision difficulty (easy/medium/hard) based on the hand situation.

## The Three Strategies

The items file defines three strategies:

```yaml
strategies:
  - id: the_pro
    strategy_name: "The Pro"
    strategy_description: "Follows basic strategy religiously. Hits on 16 against
      dealer 7 or higher, stands on 17+, doubles down on 11, splits aces and eights."

  - id: the_gambler
    strategy_name: "The Gambler"
    strategy_description: "Plays aggressively and takes risks. Always hits on 16
      regardless of dealer card, doubles down on any 10 or 11, chases the win even
      when the odds are bad."

  - id: the_coward
    strategy_name: "The Coward"
    strategy_description: "Terrified of busting. Stands on any 12 or higher no matter
      what, never doubles down, never splits — would rather lose slowly than risk
      going over 21."
```

With `repeat: 100`, each strategy plays 100 hands, totaling 300 units.

## How the Expression Step Deals Cards

```yaml
- name: deal_cards
  scope: expression
  description: "Deal random cards from a 6-deck shoe"
  expressions:
    shoe: "['2','3','4','5','6','7','8','9','10','J','Q','K','A'] * 24"
    dealt: "random.sample(shoe, 14)"
    player_cards: "[dealt[0], dealt[1]]"
    dealer_up_card: "dealt[2]"
    dealer_hole_card: "dealt[3]"
    extra_cards: "dealt[4:]"
```

The expressions are evaluated in order:

1. **shoe**: Creates a 312-card shoe (13 ranks x 4 suits x 6 decks = 312 cards, but since we only track rank, it's 13 x 24 = 312)
2. **dealt**: Randomly samples 14 cards without replacement
3. **player_cards**: First two dealt cards go to the player
4. **dealer_up_card**: Third card is the dealer's face-up card
5. **dealer_hole_card**: Fourth card is the dealer's hidden card
6. **extra_cards**: Remaining 10 cards available for hits

Each unit's random seed ensures reproducible dealing. The same `unit_id` always gets the same cards.

## The LLM-as-Validator Pattern

This is the key pattern in the Blackjack pipeline. Step 3 doesn't just score — it acts as a quality gate.

### The Validation Template

The validate_strategy template asks the LLM to review each decision against the assigned strategy:

```jinja2
Score the strategy adherence from 0.0 to 1.0:
- 1.0 = every decision matched the strategy perfectly
- 0.7-0.9 = minor deviations but mostly correct
- 0.4-0.6 = mixed adherence
- 0.0-0.3 = mostly or entirely wrong strategy

Strategy-specific checks:

**The Pro (basic strategy):**
- Did they hit on hard 16 when dealer shows 7 or higher?
- Did they stand on hard 17+?
- Did they double down on 11?

**The Gambler (aggressive):**
- Did they always hit on 16 regardless of dealer card?
- Did they double down on any 10 or 11?

**The Coward (conservative):**
- Did they stand on ANY 12 or higher, no matter what?
- Did they NEVER double down?
```

### The Expression Rule

```yaml
validation:
  validate_strategy:
    rules:
      - name: accuracy_threshold
        expr: "strategy_accuracy >= 0.7"
        error: "Strategy accuracy {strategy_accuracy} is below threshold 0.7"
        level: error
```

When `strategy_accuracy` comes back below 0.7, the unit fails validation. Octobatch retries the entire pipeline for that unit — dealing new cards, playing the hand again, and re-validating. Up to 3 retries (configured by `validation_retry.max_attempts`).

### Why Some Hands Get Retried

The LLM doesn't always follow the assigned strategy correctly. Common reasons for retries:

- **The Pro**: The LLM might stand on 16 vs dealer 10 (basic strategy says hit)
- **The Gambler**: The LLM might play conservatively on a bad hand instead of being aggressive
- **The Coward**: The LLM might hit on 12 or 13 (The Coward should always stand on 12+)

The validation step catches these deviations. After a retry, the LLM typically gets it right — having essentially learned from the validator's feedback through a new attempt.

### Some failures are expected

Even after all retry attempts, some hands will still fail validation — this is normal and by design. The `validate_strategy` step is intentionally strict, and certain card combinations create genuinely ambiguous situations where LLMs struggle to follow the strategy rules precisely. In our integration tests across all three providers, we consistently see 20–50% of Blackjack units fail validation after exhausting retries, with Anthropic having the highest failure rate and OpenAI the lowest.

This is what makes Blackjack a good demo pipeline: it shows the full retry cycle in action, including what happens when retries are exhausted. The units that pass validation have verified-correct strategy adherence (all with accuracy scores of 1.0), making the analysis of the remaining hands highly reliable.

## Running It

### Realtime test with a small sample

```bash
python scripts/orchestrate.py --init --pipeline Blackjack \
    --run-dir runs/bj_test --realtime --max-units 3 --provider gemini --yes
```

Processes 3 of the 300 units. This verifies all four steps work end-to-end before committing to a full run.

### Larger batch run

```bash
python scripts/orchestrate.py --init --pipeline Blackjack \
    --run-dir runs/bj_full --provider gemini --yes
python scripts/orchestrate.py --watch --run-dir runs/bj_full
```

All 300 hands (3 strategies x 100 repetitions). With `chunk_size: 100`, this creates 3 chunks processed in parallel.

## Expected Results

### Win/Loss/Push Rates

On difficult hands, all strategies tend to lose. A typical result:

```
Blackjack Strategy Comparison: Win/Loss/Push Rates
===================================================

Group        | Total | dealer_wins | player_wins | push  |  Net
---------------------------------------------------------------
the_coward   |   100 |      64.0%  |      36.0%  |  0.0% |  -28
the_pro      |   100 |      69.0%  |      27.0%  |  4.0% |  -42
the_gambler  |   100 |      70.0%  |      24.0%  |  6.0% |  -46
```

The Coward often has the best net result on difficult hands — standing on 12+ avoids busting, even though it means losing to higher dealer totals. This counterintuitive result is one of the interesting findings from the simulation.

### Difficulty Distribution

```
Performance by Hand Difficulty
==============================

Group  | Total | dealer_wins | player_wins | push
--------------------------------------------------
easy   |    89 |      61.8%  |      34.8%  |  3.4%
medium |   134 |      67.2%  |      28.4%  |  4.5%
hard   |    77 |      74.0%  |      22.1%  |  3.9%
```

Hard hands (16 vs dealer 10, 12 vs dealer 2) have significantly higher dealer win rates.

### Strategy Accuracy

```
Strategy Accuracy Distribution (Validated Hands)
=================================================

Group        | Count | Mean  | Median | Stdev
----------------------------------------------
the_coward   |   100 | 0.92  | 0.95   | 0.08
the_gambler  |   100 | 0.88  | 0.90   | 0.10
the_pro      |   100 | 0.85  | 0.85   | 0.12
```

All validated hands have accuracy >= 0.7 (the threshold). The Coward tends to score highest because its rules are simplest — "stand on 12+" is unambiguous.

## Post-Processing Configuration

```yaml
post_process:
  - name: "Strategy Win/Loss Rates"
    script: "scripts/analyze_results.py"
    args:
      - "--group-by"
      - "strategy_name"
      - "--count-field"
      - "game_outcome"
      - "--net-positive"
      - "player_wins"
      - "--net-negative"
      - "dealer_wins"
    output: "strategy_comparison.txt"

  - name: "Performance by Difficulty"
    script: "scripts/analyze_results.py"
    args: ["--group-by", "difficulty", "--count-field", "game_outcome",
           "--net-positive", "player_wins", "--net-negative", "dealer_wins"]
    output: "difficulty_analysis.txt"

  - name: "Strategy Accuracy Distribution"
    script: "scripts/analyze_results.py"
    args: ["--group-by", "strategy_name", "--numeric-field", "accuracy_score"]
    output: "accuracy_stats.txt"

  - name: "Results CSV"
    script: "scripts/analyze_results.py"
    args: ["--group-by", "strategy_name", "--count-field", "game_outcome",
           "--net-positive", "player_wins", "--net-negative", "dealer_wins",
           "--output-format", "csv"]
    output: "results.csv"
```

Note: Post-processing uses `game_outcome` and `accuracy_score` — fields unique to the `analyze_difficulty` step — to avoid double-counting across steps. The `analyze_results.py` script reads all `*_validated.jsonl` files, so using step-unique field names is important.

## Key Takeaways

1. **Expression steps eliminate cost for deterministic work**. Card dealing is free — only the LLM analysis steps cost money.

2. **The LLM-as-validator pattern creates self-correcting pipelines**. Strategy adherence is verified automatically, and bad plays are retried.

3. **Multi-step pipelines accumulate context**. Step 4 has access to everything from steps 1–3: the dealt cards, the play actions, the accuracy score.

4. **Post-processing turns raw data into insights**. The pipeline produces structured data; `analyze_results.py` aggregates it into readable reports.

5. **Monte Carlo reveals surprises**. The Coward's unexpected performance on difficult hands is the kind of finding that emerges from running hundreds of simulated trials.
