# Blackjack Walkthrough

[← Back to README](../../README.md) | [← Back to Examples](../../README.md#demo-pipeline-walkthroughs) | [Core Concepts](../core-concepts.md) | [Expression Steps](../expression-steps.md)

A strategy comparison pipeline that demonstrates the full power of Octobatch: expression steps dealing cards, deterministic verification catching LLM math errors, deterministic strategy compliance checking, multi-step LLM chains with automatic retries, and post-processing analysis.

## Concept

Three blackjack strategies play out 300 hands each (900 total). An expression step deals the cards (free, deterministic). An LLM plays each hand according to the assigned strategy. Two deterministic verification steps catch errors — one recalculates totals and checks outcomes, the other verifies the player followed their strategy correctly. A final LLM rates the difficulty of the decisions faced.

The result: win rates per strategy, difficulty distribution, and verified-correct gameplay — all generated, verified, and validated automatically.

## The Prompt

This prompt was iteratively refined across seven runs, improving the first-pass verification rate from 31% to 84%. Each iteration was informed by forensic analysis of the specific error patterns the LLM was making. The final prompt encodes every lesson learned:

```
Read pipelines/TOOLKIT.md to understand Octobatch pipeline creation.

Create a Blackjack strategy comparison pipeline.

Concept: Compare three betting strategies by simulating blackjack hands. An expression step deals cards, an LLM plays the hand, two deterministic verification steps check the math and strategy compliance, then a final LLM analyzes decision difficulty.

Strategies:

"The Pro" follows basic strategy religiously. The complete rules: Always hit on hard 8 or less. Hit on hard 9-10 (unless doubling). Always double down on hard 11. Stand on hard 12-16 against dealer 2-6. Hit on hard 12-16 against dealer 7 or higher. Stand on hard 17+. For soft hands (containing an ace counted as 11): always hit on soft 17 or less, stand on soft 19+, hit on soft 18 against dealer 9 or higher, stand on soft 18 otherwise. Do not split pairs. If dealt a natural 21 (ace + face card or 10), always stand.

"The Gambler" plays aggressively and takes risks. The complete rules: Double down on any hard 10 or 11. Always hit on 16 or less regardless of dealer card. Stand on 19+. Hit on 17-18 against dealer 7 or higher, stand on 17-18 otherwise. Never split. WARNING: Yes, hitting on 17 or 18 is unusual and risky — that is the whole point of this strategy. The Gambler takes risks that no sane player would. You MUST hit on 17-18 against dealer 7+ even though it feels wrong. If dealt a natural 21, always stand.

"The Coward" is terrified of busting. The complete rules: Stand on any 12 or higher no matter what. Hit on 11 or less. Never double down. Never split. WARNING: Yes, standing on 12 against a dealer 10 is a terrible play — that is the whole point of this strategy. The Coward would rather lose slowly than risk busting. You MUST stand on 12+ regardless of the dealer's card. If dealt a natural 21, always stand.

Pipeline steps:

Step 1, Deal Cards (Expression): Deal random cards from a 6-deck shoe — player's initial two cards, dealer's up card, dealer's hole card, and two separate pools of extra cards to avoid index-tracking confusion: player_extra_cards (5 cards) and dealer_extra_cards (5 cards). No LLM needed.

Step 2, Play Hand (LLM): Play out the hand according to the assigned strategy. Output the sequence of actions, final totals, and the result (player wins, dealer wins, or push). Important prompt engineering notes for the template — LLMs consistently make these specific errors when simulating blackjack, so the template must explicitly address each one: (1) The player draws from player_extra_cards in order. The dealer draws from dealer_extra_cards in order. These are separate pools — there is no shared index to track. (2) The dealer's total must include BOTH the up card AND the hole card plus any drawn cards. (3) After the player's turn is complete, if the player did not bust, the dealer MUST play out their hand. Use this exact structured format for the dealer's turn: "Dealer reveals: [up_card] + [hole_card] = [total]" then for each required hit "Dealer hits: drew [card] ([running sum]=[new total]), total now [new total]" then "Dealer stands at [final]" or "Dealer busts at [final]". The dealer hits on any total of 16 or less and stands on 17 or higher. Do not skip the dealer's turn. Do not invent a dealer total — only use cards actually dealt. If the dealer's initial two cards total 17 or more, the dealer stands immediately — no hits needed. (4) Show the running arithmetic in each action log entry so the total is traceable, e.g. "Hit: drew 7 (10+4+7=21), total now 21". (5) Ace handling — CRITICAL: Aces are worth 1 by default. Only count an ace as 11 if doing so would NOT make the total exceed 21. After EVERY hit, if the total exceeds 21 and there is an ace counted as 11, you MUST flip it to 1. Show the check: "ace check: 10+4+11=25 > 21 so ace=1, total is 10+4+1=15". (6) NEVER split pairs. Play all hands as a single hand. (7) After both hands are complete, determine the result using these rules IN ORDER: First, if the player busted (total > 21), the result is dealer_wins — do not compare totals. Second, if the dealer busted (total > 21), the result is player_wins — do not compare totals. A bust is an automatic loss regardless of the other hand's total. Third, if neither busted, compare: higher total wins, equal totals push. Write the result check: "RESULT: Player [total] vs Dealer [total] → [result]". (8) The player_final_total and dealer_final_total in the JSON must exactly match the last running total shown in the action log. Also include in the JSON output: first_action (the very first action the player took — "hit", "stand", or "double_down") and player_initial_total (the player's hand total before any actions, with aces counted optimally).

Step 3, Verify Hand (Expression): Deterministically verify the mechanical aspects of the hand. Recalculate player and dealer totals from the actual cards (aces are worth 1 unless counting as 11 would keep the total at 21 or below). Check that bust detection is correct — if someone exceeds 21, they busted. Check that the reported outcome is consistent — higher non-busted total wins, equal totals push, busting loses. Fail the unit if any check fails so it gets retried automatically. Note: player cards come from player_hand + player_extra_cards, dealer cards come from dealer_up_card + dealer_hole_card + dealer_extra_cards. Count draws from the action log to determine how many extra cards each side used.

Step 4, Verify Strategy (Expression): Deterministically verify that the player's first action was correct for their strategy. Use the player_initial_total, first_action, and dealer_up_card. Determine if the hand is soft (contains an ace counted as 11 with total <= 21). Handle natural 21 (player_initial_total == 21): must always "stand". For The Coward: if player_initial_total >= 12, first_action must be "stand"; otherwise must be "hit". For The Pro: if hard 8 or less, must "hit"; if hard 9 or hard 10, must "hit"; if hard 11, must be "double_down"; if hard 12-16 vs dealer 2-6, must "stand"; if hard 12-16 vs dealer 7+, must "hit"; if hard 17+, must "stand"; if soft 17 or less, must "hit"; if soft 18 vs dealer 9/10/J/Q/K/A, must "hit"; if soft 18 vs dealer 2-8, must "stand"; if soft 19+, must "stand". For The Gambler: if hard 10 or 11, must be "double_down"; if 16 or less, must "hit"; if 19+, must "stand"; if 17-18 vs dealer 7+, must "hit"; if 17-18 vs dealer 2-6, must "stand". Output a strategy_correct boolean and fail the unit if false.

Step 5, Analyze Difficulty (LLM): Rate the difficulty of the decisions (easy, medium, hard) based on notorious blackjack situations.

Requirements: Three items (one per strategy). Run each strategy 300 times (900 total hands).

Create in pipelines/Blackjack/
```

### Prompt Engineering Lessons

The prompt above contains eight specific guardrails, each addressing a failure pattern discovered through forensic analysis of LLM errors:

1. **Pre-split deck pools** (guardrail 1): LLMs lose track of shared array indices. Splitting into `player_extra_cards` and `dealer_extra_cards` eliminated wrong-card errors entirely.

2. **Dealer total includes both cards** (guardrail 2): LLMs frequently computed the dealer total from only the up card, forgetting the hole card.

3. **Structured dealer format** (guardrail 3): The single biggest error category was the LLM skipping the dealer's turn entirely and inventing a plausible total. Requiring a rigid format ("Dealer reveals: ... Dealer hits: ... Dealer stands at ...") forces the LLM to actually compute each step.

4. **Chain of Thought arithmetic** (guardrail 4): Requiring running arithmetic in every action log entry ("Hit: drew 7 (10+4+7=21), total now 21") dramatically reduced calculation errors. Multi-draw failure rates dropped from 55% to 26%.

5. **Flipped ace default** (guardrail 5): LLMs consistently treated aces as 11 and forgot to convert to 1 when busting. Flipping the default to "aces are 1 unless 11 is safe" changed the model's behavior at the token-prediction level.

6. **No splitting** (guardrail 6): Split hands require tracking multiple sub-hands and card pools — a state-tracking challenge that LLMs handle poorly.

7. **Ordered bust rules** (guardrail 7): LLMs would correctly identify a dealer bust, then compare raw totals anyway ("Player 21 vs Dealer 23, 21 < 23 → dealer_wins"). Explicit ordering — check busts first, then compare — fixed 80% of result determination errors.

8. **Strategy warnings** (The Gambler and Coward): LLMs have strong built-in blackjack priors from training data. They refuse to hit on 18 for The Gambler and override The Coward's "stand on 12+" rule against strong dealer cards. Explicit warnings ("Yes, this is unusual — that's the whole point") reduced strategy override errors.

## Pipeline Structure

```
┌────────────┐   ┌───────────┐   ┌─────────────┐   ┌─────────────────┐   ┌────────────────────┐
│ deal_cards │──▶│ play_hand │──▶│ verify_hand │──▶│ verify_strategy │──▶│ analyze_difficulty │
│ expression │   │ LLM       │   │ expression  │   │ expression      │   │ LLM                │
│            │   │           │   │ math check  │   │ strategy check  │   │                    │
└────────────┘   └───────────┘   └─────────────┘   └─────────────────┘   └────────────────────┘
```

**Step 1: deal_cards** — Expression step. Deals cards from a 6-deck shoe into separate pools. Free, instant, reproducible.

**Step 2: play_hand** — LLM plays the hand following the assigned strategy. Outputs action_log with Chain of Thought arithmetic, final totals, bust flags, and result.

**Step 3: verify_hand** — Expression step. Deterministically recalculates player and dealer totals from the actual cards, checks bust detection, and verifies the reported outcome is consistent. Hands that fail any check are automatically retried.

**Step 4: verify_strategy** — Expression step. Deterministically checks whether the player's first action was correct for their assigned strategy, accounting for soft vs. hard hands and natural 21. Hands that violated the strategy are automatically retried.

**Step 5: analyze_difficulty** — LLM rates decision difficulty (easy/medium/hard) based on the hand situation.

## The Three Strategies

The items file defines three strategies with complete decision tables:

```yaml
strategies:
  - id: the_pro
    strategy_name: "The Pro"
    strategy_description: "Follows basic strategy religiously"
    strategy_rules: |
      Always hit on hard 8 or less.
      Hit on hard 9-10 (unless doubling).
      Always double down on hard 11.
      Stand on hard 12-16 against dealer 2-6.
      Hit on hard 12-16 against dealer 7 or higher.
      Stand on hard 17+.
      For soft hands (containing an ace counted as 11):
        Always hit on soft 17 or less.
        Stand on soft 19+.
        Hit on soft 18 against dealer 9 or higher.
        Stand on soft 18 otherwise.
      Do not split pairs.
      If dealt a natural 21 (ace + face card or 10), always stand.

  - id: the_gambler
    strategy_name: "The Gambler"
    strategy_description: "Plays aggressively and takes risks"
    strategy_rules: |
      Double down on any hard 10 or 11.
      Always hit on 16 or less regardless of dealer card.
      Stand on 19+.
      Hit on 17-18 against dealer 7 or higher, stand on 17-18 otherwise.
      Never split.
      WARNING: Yes, hitting on 17 or 18 is unusual and risky — that is the
      whole point of this strategy. The Gambler takes risks that no sane
      player would. You MUST hit on 17-18 against dealer 7+ even though
      it feels wrong.
      If dealt a natural 21, always stand.

  - id: the_coward
    strategy_name: "The Coward"
    strategy_description: "Terrified of busting"
    strategy_rules: |
      Stand on any 12 or higher no matter what.
      Hit on 11 or less.
      Never double down.
      Never split.
      WARNING: Yes, standing on 12 against a dealer 10 is a terrible play
      — that is the whole point of this strategy. The Coward would rather
      lose slowly than risk busting. You MUST stand on 12+ regardless of
      the dealer's card.
      If dealt a natural 21, always stand.
```

With `repeat: 300`, each strategy plays 300 hands, totaling 900 units.

## How the Expression Step Deals Cards

```yaml
- name: deal_cards
  scope: expression
  description: "Deal random cards from a 6-deck shoe — player's two cards, dealer's up/hole cards, and separate extra card pools."
  expressions:
    _shoe_cards: "['A','2','3','4','5','6','7','8','9','10','J','Q','K'] * 24"
    _dealt: "random.sample(_shoe_cards, 14)"
    player_card_1: "_dealt[0]"
    player_card_2: "_dealt[1]"
    dealer_up_card: "_dealt[2]"
    dealer_hole_card: "_dealt[3]"
    player_extra_cards: "_dealt[4:9]"
    dealer_extra_cards: "_dealt[9:14]"
```

The expressions are evaluated in order. Variables prefixed with `_` are internal (not passed to later steps):

1. **_shoe_cards**: Creates a 312-card shoe (13 ranks × 24 copies = 312, representing 6 decks)
2. **_dealt**: Randomly samples 14 cards without replacement
3. **player_card_1, player_card_2**: First two dealt cards go to the player as separate fields
4. **dealer_up_card**: Third card is the dealer's face-up card
5. **dealer_hole_card**: Fourth card is the dealer's hidden card
6. **player_extra_cards**: Cards 5–9 available for player hits
7. **dealer_extra_cards**: Cards 10–14 available for dealer hits

Pre-splitting the extra cards into separate pools eliminates index-tracking errors. The LLM doesn't need to remember where the player stopped drawing — each side has its own independent pool.

Each unit's random seed ensures reproducible dealing. The same `unit_id` always gets the same cards.

## Deterministic Verification: Keep the Math Out of the LLM

Steps 3 and 4 are the key innovations in this pipeline. Instead of trusting the LLM's arithmetic or strategy knowledge, deterministic expression steps verify everything and catch errors automatically.

### Step 3: verify_hand — Math Verification

The verify_hand step reads the cards dealt in step 1 and the LLM's play record from step 2, then:

1. **Reconstructs the hands** — Counts draw actions in the action_log to determine which extra cards were used by the player and dealer
2. **Recalculates totals** — Sums card values with proper ace handling (aces are 1 unless counting as 11 would keep the total at 21 or below)
3. **Checks bust detection** — If a recalculated total exceeds 21, the hand busted; verifies the LLM reported this correctly
4. **Checks outcome consistency** — Busts lose automatically (no total comparison); otherwise higher total wins, equal totals push

#### The ace calculation

The ace formula handles soft/hard totals in a single expression:

```yaml
_p_total: "_p_sum + 10 if (_p_aces >= 1 and _p_sum + 10 <= 21) else _p_sum"
```

All aces start as 1 (via the card value map). If the hand contains at least one ace and promoting one to 11 (adding 10) wouldn't bust, it adds 10. This handles the common case — at most one ace counts as 11 — in a single readable expression. The same formula is used for the dealer's hand.

#### Clean failure handling

When any check fails, the unit fails via a validation rule — not an exception:

```yaml
validation:
  verify_hand:
    required:
      - verification_passed
    types:
      verification_passed: boolean
    rules:
      - name: hand_verification
        expr: "verification_passed == True"
        error: "Hand verification failed: {verification_details}"
        level: error
```

The `verification_details` field provides full diagnostics: calculated vs. reported totals, which checks failed, and what outcome was expected.

### Step 4: verify_strategy — Strategy Compliance

An earlier version of this pipeline used an LLM to validate strategy adherence, scoring accuracy from 0.0 to 1.0. Analysis revealed the LLM validator was wrong 73% of the time for The Pro — it misapplied basic strategy rules, penalized correct plays, and made binary 0.0/1.0 judgments instead of nuanced scoring. This was the single biggest source of false rejections.

The fix: replace the LLM validator with a deterministic expression step. The verify_strategy step checks whether the player's first action was correct given their initial hand, the dealer's up card, and their assigned strategy:

- **Soft vs. hard detection**: Determines if the hand contains an ace counted as 11
- **Natural 21 handling**: Any natural blackjack must stand
- **Complete decision tables**: Every strategy has explicit rules for every possible hand total and dealer up card combination
- **No ambiguity**: The expression either passes or fails — no judgment calls

This single change recovered over 150 units that the LLM validator had incorrectly rejected, and was the largest contributor to the improvement from 50% to 79% pass rate.

### Why deterministic verification matters

In testing across multiple providers, LLMs consistently make errors on a significant percentage of hands. Common mistakes include:

- **Dealer hallucination**: Skipping the dealer's turn entirely and inventing a plausible total (44.9% of verify_hand failures in one run)
- **Ace handling**: Always treating aces as 11 and never converting to 1 when busting
- **Result determination**: Correctly computing both totals but picking the wrong winner, especially after dealer busts
- **Strategy override**: The LLM's built-in blackjack knowledge overriding explicit strategy instructions (refusing to hit on 18 for The Gambler)
- **Running total drift**: Losing track of card values across multiple draws

Without deterministic verification, these errors pass through the pipeline undetected.

<a id="improvement-arc"></a>

## The Improvement Arc

Seven iterations of forensic analysis and targeted fixes took the first-pass verification rate from 31% to 84%:

| Run | Pass Rate | Key Change |
|-----|-----------|------------|
| 1 (broken gate) | 57% (fake) | Expression step validation wasn't enforced — 456 bad hands passed unchecked |
| 2 (fixed gate) | 31% | Orchestrator fix: expression steps now run validation rules |
| 3 | 37% | Prompt fixes: shared shoe pool instructions, dealer must include both cards |
| 4 | 48% | Chain of Thought: running arithmetic in every action log entry |
| 5 | 79% | Replaced LLM strategy validator with deterministic expression step |
| 6 | 81% | Structured dealer format, soft hand detection, pre-split deck |
| 7 | 84% | Ordered bust rules, strategy override warnings, natural 21 handling |

<a id="original-prompt"></a>

### Where the prompt started

The original prompt had 4 steps (not 5), ran 100 hands per strategy (not 300), and used an LLM to validate strategy compliance instead of a deterministic expression step. It looked like this:

```
Read pipelines/TOOLKIT.md to understand Octobatch pipeline creation.

Create a Blackjack strategy comparison pipeline.

Concept: Compare three betting strategies by simulating blackjack hands. This pipeline demonstrates expression
steps, multi-step LLM chains, and validation with retries. An expression step deals cards, an LLM plays the hand,
a second LLM validates that the strategy was followed correctly (retrying failures), then a third LLM analyzes
decision difficulty.

Strategies:

"The Pro" follows basic strategy religiously. Hits on 16 against dealer 7 or higher, stands on 17+, doubles down
on 11, splits aces and eights.

"The Gambler" plays aggressively and takes risks. Always hits on 16 regardless of dealer card, doubles down on any
10 or 11, chases the win even when the odds are bad.

"The Coward" is terrified of busting. Stands on any 12 or higher no matter what, never doubles down, never splits
— would rather lose slowly than risk going over 21.

Pipeline steps:

Step 1, Deal Cards (Expression): Deal random cards from a 6-deck shoe — player's initial two cards, dealer's up
card, dealer's hole card, and a shoe of extra cards for potential hits. No LLM needed.

Step 2, Play Hand (LLM): Play out the hand according to the assigned strategy. Output the sequence of actions,
final totals, and the result (player wins, dealer wins, or push).

Step 3, Validate Strategy (LLM): Score how accurately the strategy was followed from 0.0 to 1.0. Use Octobatch's
validation system to enforce strategy_accuracy >= 0.7. Hands below this threshold will automatically fail
validation and be retried.

Step 4, Analyze Difficulty (LLM): Rate the difficulty of the decisions (easy, medium, hard) based on notorious
blackjack situations.

Requirements: Three items (one per strategy). Run each strategy 100 times (300 total hands). Validation threshold
enforced via config.yaml expression rule.

Create in pipelines/Blackjack/
```

Compare this to [the final prompt at the top of this page](#the-prompt). The original is about 40 lines of plain English with no implementation details — anyone who knows blackjack can read it and understand exactly what the pipeline does. Every difference between the two versions represents a lesson learned from the seven iterations below.

The biggest structural changes: the strategy descriptions grew from one-line summaries to complete decision tables with explicit warnings about counterintuitive rules (Run 7). Step 3 changed from an LLM validator to a deterministic expression step (Run 5), and a second deterministic step was added for arithmetic verification (Run 6). The play_hand step gained eight specific guardrails, each addressing a failure pattern discovered through forensic analysis. And the scale tripled from 300 to 900 hands once the pipeline was reliable enough to justify the larger run.

Each iteration followed the same process: run the pipeline, analyze the failures forensically, identify the most common error patterns, implement targeted fixes, and measure the improvement. The analysis was performed by Claude Code, with Gemini providing independent review and architectural insights.

Key insights from the improvement arc:

- **The broken gate** (Run 1 → 2): The framework itself had a bug — expression steps bypassed validation entirely. Three independent AI advisors all missed this because they shared the same assumption about how validation worked.
- **The LLM validator was the bottleneck** (Run 4 → 5): The biggest single improvement came from replacing an LLM with a deterministic check. The LLM validator was wrong 73% of the time for The Pro.
- **LLM laziness, not inability** (Run 5 → 6): The inverted complexity finding — hands where the player stood (simplest case) had the highest failure rate because the LLM skipped the dealer's turn. More player complexity meant the LLM was "warmed up" and handled the dealer correctly.
- **Training priors fight instructions** (Run 6 → 7): The LLM's built-in blackjack knowledge overrides explicit strategy rules. Warnings explaining *why* the counterintuitive rules exist ("that's the whole point of this strategy") partially mitigated this.

## Sample Results

From the final run (754 verified hands out of 900 dealt):

```
Blackjack Strategy Comparison
=============================
Group       |  Total | dealer_wins | player_wins |     push |      Net
----------------------------------------------------------------------
The Pro     |    241 |       44.0% |       46.5% |     9.5% |       +6
The Coward  |    257 |       48.6% |       44.4% |     7.0% |      -11
The Gambler |    256 |       49.2% |       39.1% |    11.7% |      -26

Decision Difficulty by Strategy
===============================
Group       |  Total |     easy |     hard |   medium
-----------------------------------------------------
The Coward  |    257 |    66.9% |     9.7% |    23.3%
The Pro     |    241 |    65.6% |     4.1% |    30.3%
The Gambler |    256 |    43.0% |     9.0% |    48.0%
```

The Pro (basic strategy) is the only profitable strategy at +6 net. The Coward bleeds chips slowly at -11, while The Gambler's aggressive play loses the most at -26. The difficulty distribution shows The Gambler creates the most complex decision points (48% medium), while The Coward's simple "stand on 12+" rule makes most hands easy (66.9%).

Total cost for 900 hands across 5 pipeline steps: $0.22.

## Running It

### Realtime test with a small sample

```bash
python scripts/orchestrate.py --init --pipeline Blackjack \
    --run-dir runs/bj_test --realtime --max-units 3 --provider gemini --yes
```

Processes 3 of the 900 units. This verifies all five steps work end-to-end before committing to a full run.

### Full batch run

```bash
python scripts/orchestrate.py --init --pipeline Blackjack \
    --run-dir runs/bj_full --provider gemini --yes
python scripts/orchestrate.py --watch --run-dir runs/bj_full
```

All 900 hands (3 strategies × 300 repetitions). With the default chunk size, this creates parallel chunks processed simultaneously via the Gemini Batch API.

## Post-Processing Configuration

Reports are generated automatically when the run completes:

```yaml
post_process:
  - name: "Strategy Comparison"
    script: "scripts/analyze_results.py"
    args:
      - "--group-by"
      - "strategy_name"
      - "--count-field"
      - "result"
      - "--net-positive"
      - "player_wins"
      - "--net-negative"
      - "dealer_wins"
      - "--title"
      - "Blackjack Strategy Comparison"
    output: "strategy_comparison.txt"

  - name: "Difficulty Analysis"
    script: "scripts/analyze_results.py"
    args:
      - "--group-by"
      - "strategy_name"
      - "--count-field"
      - "difficulty"
      - "--title"
      - "Decision Difficulty by Strategy"
    output: "difficulty_analysis.txt"

  - name: "Results CSV"
    script: "scripts/analyze_results.py"
    args:
      - "--group-by"
      - "strategy_name"
      - "--count-field"
      - "result"
      - "--net-positive"
      - "player_wins"
      - "--net-negative"
      - "dealer_wins"
      - "--output-format"
      - "csv"
    output: "results.csv"

  - name: "Compress Raw Results"
    type: gzip
    files:
      - "chunks/*/*_validated.jsonl"
    keep_originals: false
```

## Key Takeaways

1. **Make deterministic work deterministic.** The verify_hand and verify_strategy expression steps catch errors that LLMs make consistently. You don't need AI to check that J+5+8 = 23 or that standing on 15 violates "always hit on 16 or less."

2. **Don't use an LLM as a quality gate for objective rules.** The LLM strategy validator was wrong 73% of the time. Replacing it with a deterministic expression step was the single biggest improvement in the entire pipeline.

3. **Forensic failure analysis drives targeted fixes.** Each prompt iteration was informed by categorizing every failure. "Dealer hallucinated totals" led to structured dealer format. "Ace always 11" led to flipping the default. Generic "make the prompt better" wouldn't have found these.

4. **LLM training priors fight explicit instructions.** The model's built-in blackjack knowledge overrides what you tell it. Explaining *why* a counterintuitive rule exists ("that's the whole point") works better than just repeating the rule louder.

5. **Chain of Thought improves downstream analysis.** When play_hand shows its arithmetic ("10+4+7=21"), the downstream analyze_difficulty LLM can assess actual decision complexity instead of just seeing a final score. The quality of upstream reasoning directly improves downstream analysis.

6. **Pre-split shared resources to reduce cognitive load.** Giving the LLM separate `player_extra_cards` and `dealer_extra_cards` pools eliminated index-tracking errors entirely. Design your data structures to minimize the state the LLM needs to track.

7. **Expression steps eliminate cost for deterministic work.** Card dealing and both verification steps are free — only the LLM analysis steps (play_hand and analyze_difficulty) cost money. Total cost for 900 verified hands: $0.22.
