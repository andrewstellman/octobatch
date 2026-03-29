# Integration Test Results — 2026-03-25

**Timestamp:** 20260325-113335
**Runner:** Claude Code (automated)
**Python:** 3.13.1

## Results

| # | Test | Result | Time | Notes |
|---|------|--------|------|-------|
| 1 | DrunkenSailor realtime x Gemini | PASS | 29s | 15/15 valid, outcome balance 40% |
| 2 | Blackjack realtime x OpenAI | PASS | 163s | 12 validated (final step), 3 failed, all strategies present, strategy_correct=true |
| 3 | NPCDialog realtime x Anthropic | FAIL | 522s | 7 valid generate_dialog / 5 permanently failed; 0 score_consistency validated; schema failures on retries |
| 4 | DrunkenSailor realtime x OpenAI | PASS | 61s | 15/15 valid, outcome balance 40% |
| 5 | Blackjack realtime x Anthropic | PASS | 589s | 11 validated (final step), 1 failed, all strategies present, strategy_correct=true |
| 6 | NPCDialog realtime x Gemini | PASS | 67s | 11 score_consistency validated, 1 failed, mood_responsiveness min=0.8, personality_consistency min=0.9 |
| 7 | DrunkenSailor realtime x Anthropic | PASS | 221s | 15/15 valid, outcome balance 40%, 8 retried |
| 8 | Blackjack realtime x Gemini | PASS | 57s | 11 validated (final step), all strategies present, strategy_correct=true |
| 9 | NPCDialog realtime x OpenAI | PASS | 113s | 12/12 score_consistency validated, 0 failed, mood_responsiveness min=0.7, personality_consistency min=0.7 |
| 10 | DrunkenSailor batch x Gemini | PARTIAL | 340s | 100/100 valid, outcome balance 47%, but only 1 chunk (multi-chunk boundary NOT exercised) |

**Passed: 8/10 | Failed: 1/10 | Partial: 1/10**

## Detailed Findings

### Provider-Specific Issues

**Anthropic:**
- NPCDialog x Anthropic (Test 3) failed hard: `generate_dialog` had persistent schema validation failures across all 5 retry attempts (only 2 valid per retry pass). The model consistently produced output that failed schema validation, preventing any units from reaching `score_consistency`.
- Blackjack x Anthropic (Test 5) was slow (589s) with 18 retries but ultimately succeeded.
- DrunkenSailor x Anthropic (Test 7) needed 8 retries but completed with 0 failures.

**Gemini and OpenAI:** No provider-specific issues. All runs completed within expected parameters.

### Quality Gate Results

**DrunkenSailor (all 4 runs):**
- `outcome` enum: PASS (all valid)
- `outcome_balance` (fell_in_water ratio 0.25-0.75): PASS — 40% (realtime), 47% (batch)
- `final_position` within [0, 10]: PASS
- `path` parseable as integer array: PASS

**Blackjack (all 3 runs):**
- All three strategies present (`The Pro`, `The Gambler`, `The Coward`): PASS
- `result` enum valid: PASS
- `strategy_correct` remains true: PASS
- `difficulty` enum valid: PASS

**NPCDialog:**
- Anthropic (Test 3): FAIL — no `score_consistency` validated records
- Gemini (Test 6): PASS — all gates held (personality_consistency min=0.9, mood_responsiveness min=0.8)
- OpenAI (Test 9): PASS — all gates held (personality_consistency min=0.7, mood_responsiveness min=0.7)
- Supplemental mood_responsiveness >= 0.6 check: PASS for Gemini and OpenAI (0 violations)

### Batch Boundary (Test 10)

The batch run generated only 100 units in 1 chunk (chunk_size=100). DrunkenSailor has 1 base item, so `--max-units 120` capped at 100 repetitions. The multi-chunk boundary was **not** exercised. To test multi-chunk behavior, either reduce `chunk_size` in config or use a pipeline with more base items.

### TUI Dump Verification

All 10 runs produced valid TUI JSON dumps. No discrepancies between manifest state and TUI-reported status.

### Manifest/Status

All runs reached terminal status (`complete`). No stuck or inconsistent manifests.

## Cost Summary

| Run | Cost |
|-----|------|
| DrunkenSailor x Gemini RT | $0.0023 |
| Blackjack x OpenAI RT | $0.0092 |
| NPCDialog x Anthropic RT | $0.0382 |
| DrunkenSailor x OpenAI RT | $0.0021 |
| Blackjack x Anthropic RT | $0.1453 |
| NPCDialog x Gemini RT | $0.0059 |
| DrunkenSailor x Anthropic RT | $0.0516 |
| Blackjack x Gemini RT | $0.0073 |
| NPCDialog x OpenAI RT | $0.0049 |
| DrunkenSailor batch x Gemini | $0.0064 |
| **Total** | **$0.2732** |

## Recommendation

**FIX FIRST**

- **Test 3 (NPCDialog x Anthropic):** Anthropic consistently fails schema validation on `generate_dialog`. Investigate whether the prompt template needs Anthropic-specific adjustments or if the schema constraints are too strict for Anthropic's output format.
- **Test 10 (batch multi-chunk):** The multi-chunk boundary was not exercised. Consider reducing `chunk_size` or using a pipeline with more base items to validate chunk-boundary behavior in future runs.
