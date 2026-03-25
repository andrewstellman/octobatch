# Integration Test Protocol: Octobatch

## Working Directory

All commands in this protocol run from the project root using **relative paths only**. Use `./scripts/`, `./pipelines/`, `./tests/`, and `./quality/`. Do not `cd` to an absolute path.

## Safety Constraints

- DO NOT modify source code.
- DO NOT delete run data outside the timestamped integration-test runs you create for this session.
- ONLY create files under `./runs/` and `./quality/results/`.
- If a run fails, record the failure and continue to the next check. Do not hot-fix code during this protocol.
- This protocol intentionally exercises real provider APIs. If API keys are missing, stop before the live runs.

## Pre-Flight Check

Run these commands before any live execution:

```bash
source ./.venv/bin/activate
./.venv/bin/python --version
./.venv/bin/python ./scripts/orchestrate.py --validate-config --config ./pipelines/DrunkenSailor/config.yaml
./.venv/bin/python ./scripts/orchestrate.py --validate-config --config ./pipelines/Blackjack/config.yaml
./.venv/bin/python ./scripts/orchestrate.py --validate-config --config ./pipelines/NPCDialog/config.yaml

test -n "$GOOGLE_API_KEY"
test -n "$OPENAI_API_KEY"
test -n "$ANTHROPIC_API_KEY"
```

Checklist:

- [ ] `.venv` activates successfully
- [ ] All three pipeline configs validate successfully
- [ ] `GOOGLE_API_KEY` is set
- [ ] `OPENAI_API_KEY` is set
- [ ] `ANTHROPIC_API_KEY` is set
- [ ] `./tests/integration_checks.py` exists
- [ ] `./scripts/tui.py --dump` is available for post-run UI verification

If any API key check fails, stop and ask the user whether to skip that provider or abort the live integration run.

## Test Matrix

The matrix below balances cost and coverage:

- **Nine realtime runs** cover `3 pipelines × 3 providers`
- **One Gemini batch run** covers the chunk-boundary and polling path with more than one chunk
- **Realtime unit counts** are calibrated to hit pipeline-specific correctness checks without excessive spend
- **Batch unit count** is intentionally above the default chunk size to exercise multi-chunk behavior

| # | Check | Method | Pass Criteria |
|---|-------|--------|---------------|
| 1 | DrunkenSailor realtime × Gemini | `--init --realtime --provider gemini --max-units 15` | Run completes, `outcome` values are valid, and distribution is non-degenerate |
| 2 | Blackjack realtime × OpenAI | `--init --realtime --provider openai --max-units 15` | Run completes, all three strategies appear, `strategy_correct` stays true for validated outputs |
| 3 | NPCDialog realtime × Anthropic | `--init --realtime --provider anthropic --max-units 12` | Run completes, `final_tone` stays in enum, both scoring gates remain above threshold |
| 4 | DrunkenSailor realtime × OpenAI | same as #1 | Same pass criteria as #1 |
| 5 | Blackjack realtime × Anthropic | same as #2 | Same pass criteria as #2 |
| 6 | NPCDialog realtime × Gemini | same as #3 | Same pass criteria as #3 |
| 7 | DrunkenSailor realtime × Anthropic | same as #1 | Same pass criteria as #1 |
| 8 | Blackjack realtime × Gemini | same as #2 | Same pass criteria as #2 |
| 9 | NPCDialog realtime × OpenAI | same as #3 | Same pass criteria as #3 |
| 10 | DrunkenSailor batch × Gemini | `--init`, then `--watch`, `--max-units 120` | Run completes across at least 2 chunks, manifest reaches terminal state, and quality checks still pass |

## Design Principles for These Checks

- **Happy path:** Each demo pipeline is exercised end-to-end through the real orchestrator and a real provider.
- **Cross-variant consistency:** Every supported provider executes every demo pipeline at least once.
- **Output correctness:** Each run is checked with pipeline-specific field and threshold gates, not just process exit code.
- **Component boundaries:** The batch run explicitly covers chunking, provider polling, validation, and manifest/TUI reporting together.

## Execution UX (How to Present When Running This Protocol)

### Phase 1: The Plan

Before running anything, present this table to the user:

```text
## Integration Test Plan

| # | Test | What It Checks | Est. Time |
|---|------|----------------|-----------|
| 1 | Group A realtime | 3 providers, 3 different pipelines in parallel | ~3-8 min |
| 2 | Group B realtime | rotated provider/pipeline coverage | ~3-8 min |
| 3 | Group C realtime | completes the 3×3 provider matrix | ~3-8 min |
| 4 | Gemini batch boundary | multi-chunk watch-mode validation | ~5-15 min |

Total: 10 runs, roughly 15-40 minutes depending on provider latency
```

### Phase 2: Progress

Report compact one-line progress updates:

```text
⧗ Test 1: DrunkenSailor realtime × Gemini... running
✓ Test 1: DrunkenSailor realtime × Gemini — PASS (42s)
✗ Test 2: Blackjack realtime × OpenAI — FAIL (strategy gate dropped below threshold)
```

### Phase 3: Results

After all runs complete, present:

```text
## Results

| # | Test | Result | Time | Notes |
|---|------|--------|------|-------|
| ... | ... | ✓ PASS / ✗ FAIL | ... | ... |

Passed: N/10 | Failed: M/10
Recommendation: SHIP IT / FIX FIRST / NEEDS INVESTIGATION
```

Save the full report to `quality/results/YYYY-MM-DD-integration.md`.

## Live Execution Commands

### Shared Session Variables

```bash
source ./.venv/bin/activate
STAMP=$(date +%Y%m%d-%H%M%S)
OUT_DIR="./quality/results/$STAMP"
mkdir -p "$OUT_DIR"
```

### Group A — one run per provider in parallel

```bash
./.venv/bin/python ./scripts/orchestrate.py --init --pipeline DrunkenSailor --run-dir "./runs/it_${STAMP}_sailor_gemini_rt" --realtime --provider gemini --max-units 15 --yes > "$OUT_DIR/01_sailor_gemini_rt.log" 2>&1 &
./.venv/bin/python ./scripts/orchestrate.py --init --pipeline Blackjack --run-dir "./runs/it_${STAMP}_blackjack_openai_rt" --realtime --provider openai --max-units 15 --yes > "$OUT_DIR/02_blackjack_openai_rt.log" 2>&1 &
./.venv/bin/python ./scripts/orchestrate.py --init --pipeline NPCDialog --run-dir "./runs/it_${STAMP}_npc_anthropic_rt" --realtime --provider anthropic --max-units 12 --yes > "$OUT_DIR/03_npc_anthropic_rt.log" 2>&1 &
wait
```

### Group B — rotate providers

```bash
./.venv/bin/python ./scripts/orchestrate.py --init --pipeline DrunkenSailor --run-dir "./runs/it_${STAMP}_sailor_openai_rt" --realtime --provider openai --max-units 15 --yes > "$OUT_DIR/04_sailor_openai_rt.log" 2>&1 &
./.venv/bin/python ./scripts/orchestrate.py --init --pipeline Blackjack --run-dir "./runs/it_${STAMP}_blackjack_anthropic_rt" --realtime --provider anthropic --max-units 15 --yes > "$OUT_DIR/05_blackjack_anthropic_rt.log" 2>&1 &
./.venv/bin/python ./scripts/orchestrate.py --init --pipeline NPCDialog --run-dir "./runs/it_${STAMP}_npc_gemini_rt" --realtime --provider gemini --max-units 12 --yes > "$OUT_DIR/06_npc_gemini_rt.log" 2>&1 &
wait
```

### Group C — complete the realtime matrix

```bash
./.venv/bin/python ./scripts/orchestrate.py --init --pipeline DrunkenSailor --run-dir "./runs/it_${STAMP}_sailor_anthropic_rt" --realtime --provider anthropic --max-units 15 --yes > "$OUT_DIR/07_sailor_anthropic_rt.log" 2>&1 &
./.venv/bin/python ./scripts/orchestrate.py --init --pipeline Blackjack --run-dir "./runs/it_${STAMP}_blackjack_gemini_rt" --realtime --provider gemini --max-units 15 --yes > "$OUT_DIR/08_blackjack_gemini_rt.log" 2>&1 &
./.venv/bin/python ./scripts/orchestrate.py --init --pipeline NPCDialog --run-dir "./runs/it_${STAMP}_npc_openai_rt" --realtime --provider openai --max-units 12 --yes > "$OUT_DIR/09_npc_openai_rt.log" 2>&1 &
wait
```

### Group D — batch boundary run

```bash
./.venv/bin/python ./scripts/orchestrate.py --init --pipeline DrunkenSailor --run-dir "./runs/it_${STAMP}_sailor_gemini_batch" --provider gemini --max-units 120 --yes > "$OUT_DIR/10_sailor_gemini_batch_init.log" 2>&1
./.venv/bin/python ./scripts/orchestrate.py --watch --run-dir "./runs/it_${STAMP}_sailor_gemini_batch" > "$OUT_DIR/10_sailor_gemini_batch_watch.log" 2>&1
```

## Post-Run Verification Checklist

For **every** run directory above, verify all of the following:

1. **Process-level:** The command exited cleanly and its log file exists in `$OUT_DIR`.
2. **State-level:** `MANIFEST.json` exists and reports a terminal status (`complete` or `failed`).
3. **Data-level:** At least one `*_validated.jsonl` file exists for the final step and parses as JSONL.
4. **Content-level:** Pipeline-specific quality gates pass.
5. **UI-level:** `./scripts/tui.py --dump --run-dir <run_dir> --json` renders consistent status and step breakdown.

Recommended verification commands per run:

```bash
./.venv/bin/python ./scripts/tui.py --dump --run-dir "./runs/it_${STAMP}_sailor_gemini_rt" --json > "$OUT_DIR/tui_sailor_gemini_rt.json"
./.venv/bin/python ./tests/integration_checks.py DrunkenSailor "./runs/it_${STAMP}_sailor_gemini_rt" > "$OUT_DIR/check_sailor_gemini_rt.json"
```

Repeat the matching `integration_checks.py` command for each pipeline/run combination.

## Field Reference Table (built from schemas, not memory)

### Pipeline: DrunkenSailor
Schema: `pipelines/DrunkenSailor/schemas/analyze.json`

| Field | Type | Constraints |
|-------|------|-------------|
| `scenario_name` | string | required |
| `final_position` | integer | required, min 0, max 10 |
| `steps_taken` | integer | required, min 0 |
| `path` | array | required, items are integers |
| `outcome` | string | required, enum: `fell_in_water`, `reached_ship` |

### Pipeline: Blackjack (`play_hand` step)
Schema: `pipelines/Blackjack/schemas/play_hand.json`

| Field | Type | Constraints |
|-------|------|-------------|
| `action_log` | array | required, items are strings, minItems 2 |
| `player_cards_used` | array | required, items are strings, minItems 2 |
| `dealer_cards_used` | array | required, items are strings, minItems 2 |
| `player_final_total` | integer | required, min 2 |
| `dealer_final_total` | integer | required, min 2 |
| `player_busted` | boolean | required |
| `dealer_busted` | boolean | required |
| `result` | string | required, enum: `player_wins`, `dealer_wins`, `push` |
| `first_action` | string | required, enum: `hit`, `stand`, `double_down` |
| `player_initial_total` | integer | required, min 2 |

### Pipeline: Blackjack (`analyze_difficulty` step)
Schema: `pipelines/Blackjack/schemas/analyze_difficulty.json`

| Field | Type | Constraints |
|-------|------|-------------|
| `difficulty` | string | required, enum: `easy`, `medium`, `hard` |
| `reasoning` | string | required, minLength 10 |
| `strategy_name` | string | required |
| `result` | string | required, enum: `player_wins`, `dealer_wins`, `push` |

### Pipeline: NPCDialog (`generate_dialog` step)
Schema: `pipelines/NPCDialog/schemas/generate_dialog.json`

| Field | Type | Constraints |
|-------|------|-------------|
| `greeting` | string | required, minLength 5 |
| `player_response_hint` | string | required, minLength 5 |
| `tone` | string | required, enum: `warm`, `cold`, `nervous`, `hostile`, `mysterious` |
| `dialog` | string | required, minLength 50 |

### Pipeline: NPCDialog (`score_consistency` step)
Schema: `pipelines/NPCDialog/schemas/score_consistency.json`

| Field | Type | Constraints |
|-------|------|-------------|
| `npc_name` | string | required |
| `mood_name` | string | required |
| `topic_name` | string | required |
| `final_tone` | string | required, enum: `warm`, `cold`, `nervous`, `hostile`, `mysterious` |
| `personality_consistency` | number | required, min 0, max 1 |
| `mood_responsiveness` | number | required, min 0, max 1 |
| `personality_reasoning` | string | required, minLength 10 |
| `mood_reasoning` | string | required, minLength 10 |

## Pipeline-Specific Quality Gates

### DrunkenSailor

Run:

```bash
./.venv/bin/python ./tests/integration_checks.py DrunkenSailor "./runs/it_${STAMP}_sailor_gemini_rt"
```

Pass criteria:

- `outcome` uses only `fell_in_water` or `reached_ship`
- `final_position` stays within `[0, 10]`
- `path` is present and parseable as an integer array
- outcome balance is non-degenerate: `fell_in_water` ratio stays between `0.25` and `0.75`
- batch run (`max-units 120`) must span at least two chunk directories under `./runs/it_${STAMP}_sailor_gemini_batch/chunks/`

### Blackjack

Run:

```bash
./.venv/bin/python ./tests/integration_checks.py Blackjack "./runs/it_${STAMP}_blackjack_gemini_rt"
```

Pass criteria:

- final validated output includes `difficulty`, `reasoning`, `strategy_name`, and `result`
- `result` stays in `player_wins` / `dealer_wins` / `push`
- all three strategies appear in validated output (`The Pro`, `The Gambler`, `The Coward`)
- inherited `strategy_correct` remains `true` for validated rows
- `difficulty` stays in `easy` / `medium` / `hard`

### NPCDialog

Run:

```bash
./.venv/bin/python ./tests/integration_checks.py NPCDialog "./runs/it_${STAMP}_npc_gemini_rt"
```

Pass criteria:

- `final_tone` stays in `warm` / `cold` / `nervous` / `hostile` / `mysterious`
- `personality_consistency >= 0.6` for validated rows
- `mood_responsiveness >= 0.6` for validated rows
- both reasoning fields are present and at least 10 characters

**Important:** `tests/integration_checks.py` is a useful baseline, but its `mood_responsiveness` helper is weaker than the pipeline config. Supplement it with a direct artifact check:

```bash
./.venv/bin/python - <<'PY'
import json
from pathlib import Path
run_dir = Path("./runs/it_${STAMP}_npc_gemini_rt")
violations = []
for path in run_dir.glob("chunks/chunk_*/*score_consistency_validated.jsonl"):
    for line in path.read_text().splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        if float(row["mood_responsiveness"]) < 0.6:
            violations.append((row.get("unit_id"), row["mood_responsiveness"]))
print({"violations": violations[:10], "count": len(violations)})
PY
```

## Reporting (Saved to File)

Save results to `quality/results/YYYY-MM-DD-integration.md`.

### Summary Table
| Check | Result | Notes |
|-------|--------|-------|
| ... | PASS / FAIL | ... |

### Detailed Findings
- Provider-specific failures
- Manifest/status mismatches
- Quality-gate failures by pipeline
- TUI dump discrepancies

### Recommendation
- `SHIP IT` — all 10 runs passed and quality gates held
- `FIX FIRST` — one or more quality gates or terminal-state checks failed
- `NEEDS INVESTIGATION` — provider instability or inconsistent results require follow-up
