# Integration Test Protocol

You are running integration tests for Octobatch across all three providers. Read `ai_context/DEVELOPMENT_CONTEXT.md` and `scripts/CONTEXT.md` for project context.

## SAFETY CONSTRAINTS

This protocol is designed to run with --dangerously-skip-permissions.
The following constraints are absolute:

1. **DO NOT modify any source code.** No changes to scripts/, specs/, ai_context/, pipelines/, or docs/.
2. **DO NOT modify any existing test files.** No changes to tests/integration_checks.py or tests/test_tui.py.
3. **The ONLY files you may create are:**
   - Run directories under runs/ (created by the orchestrator)
   - Files inside tests/results/ (report files only)
4. **DO NOT install packages, modify .env, or change any configuration.**
5. **If something fails, record the failure in the report and move on. DO NOT attempt to fix it.**
6. **DO NOT delete any files or directories.** Never run `rm`, `rmdir`, or any destructive commands. All existing run directories must be preserved.

## PRE-FLIGHT CHECK

Before running any tests:
1. Check the local `.env` file to confirm that `GOOGLE_API_KEY`, `OPENAI_API_KEY`, and `ANTHROPIC_API_KEY` are populated. If any are missing, stop and ask me for them.
2. Create the results directory if it doesn't exist: `mkdir -p tests/results`

## EXECUTION PROTOCOL

Runs are grouped to maximize parallelism while spreading API calls across different providers to avoid rate limits. Each group contains at most one run per provider.

**Run directory naming:** Use the same convention as the TUI: `{pipeline}_{YYYYMMDD}_{HHMMSS}`. Generate a timestamp once at the start and use it for all runs in this session. This produces clean names like `blackjack_20260222_183000` that look good in the TUI and in screenshots.

For each group:
1. Source the .env file and export API keys.
2. Launch all runs in the group simultaneously using `&` background processes.
3. Wait for all processes to complete (`wait`).
4. Run the post-run verification checklist for each completed run.
5. Move to the next group.

**CRITICAL: DO NOT clean up or delete the run directories.**

## TEST MATRIX (10 runs in 4 groups)

Generate timestamp first:
```bash
TS=$(date +%Y%m%d_%H%M%S)
source .env && export GOOGLE_API_KEY OPENAI_API_KEY ANTHROPIC_API_KEY
```

### Group 1 (parallel — 3 runs, one per provider)

```bash
# Note: --init and --watch are mutually exclusive. Split batch into two commands.
python scripts/orchestrate.py --init --pipeline DrunkenSailor --run-dir runs/drunken_sailor_batch_${TS} --max-units 30 --provider gemini --yes
python scripts/orchestrate.py --watch --run-dir runs/drunken_sailor_batch_${TS} --yes &

python scripts/orchestrate.py --init --pipeline DrunkenSailor --run-dir runs/drunken_sailor_openai_${TS} --realtime --max-units 30 --provider openai --yes &
python scripts/orchestrate.py --init --pipeline DrunkenSailor --run-dir runs/drunken_sailor_anthropic_${TS} --realtime --max-units 30 --provider anthropic --yes &
wait
```

### Group 2 (parallel — 3 runs, one per provider)

```bash
python scripts/orchestrate.py --init --pipeline DrunkenSailor --run-dir runs/drunken_sailor_gemini_${TS} --realtime --max-units 30 --provider gemini --yes &
python scripts/orchestrate.py --init --pipeline Blackjack --run-dir runs/blackjack_openai_${TS} --realtime --max-units 9 --provider openai --yes &
python scripts/orchestrate.py --init --pipeline NPCDialog --run-dir runs/npc_dialog_anthropic_${TS} --realtime --max-units 12 --provider anthropic --yes &
wait
```

### Group 3 (parallel — 3 runs, one per provider)

```bash
python scripts/orchestrate.py --init --pipeline Blackjack --run-dir runs/blackjack_gemini_${TS} --realtime --max-units 9 --provider gemini --yes &
python scripts/orchestrate.py --init --pipeline NPCDialog --run-dir runs/npc_dialog_openai_${TS} --realtime --max-units 12 --provider openai --yes &
python scripts/orchestrate.py --init --pipeline Blackjack --run-dir runs/blackjack_anthropic_${TS} --realtime --max-units 9 --provider anthropic --yes &
wait
```

### Group 4 (serial — 1 run)

```bash
python scripts/orchestrate.py --init --pipeline NPCDialog --run-dir runs/npc_dialog_gemini_${TS} --realtime --max-units 12 --provider gemini --yes
```

## QUALITY CHECKS

**DrunkenSailor:**
- outcome_balance: fell_in_water ratio should be 25-75% (step: analyze, field: outcome)
- outcome_valid_enum: all outcomes must be "fell_in_water" or "reached_ship"

**Blackjack:**
- all_strategies_present: "The Pro", "The Gambler", "The Coward" all appear (interleaved unit generation should ensure this with --max-units 9)
- result_valid_enum: all results are "player_wins", "dealer_wins", or "push"
- strategy_accuracy_above_gate: all validated units have strategy_accuracy >= 0.7

**NPCDialog:**
- tone_valid_enum: all tones in ["excited", "dismissive", "curious", "greedy", "mystical"]
- personality_above_gate: all validated personality_score >= 0.6
- tone_match_all_true: all validated tone_match == True

## POST-RUN VERIFICATION CHECKLIST

For each of the 10 runs, verify ALL of the following before marking it PASS:

1. **Log check:** `RUN_LOG.txt` exists and contains a completion or failure message. Read the last 20 lines.
2. **Manifest check:** MANIFEST.json shows status "complete" (or "failed" with explanation), correct total_units, all chunks in terminal state (VALIDATED or FAILED).
3. **Data check:** Final step's `_validated.jsonl` files exist in chunk directories, are non-empty, contain parseable JSON.
4. **Sample check:** Read 2-3 validated records from the final step. Confirm expected fields are present and populated:
   - DrunkenSailor: outcome, steps (array), distance
   - Blackjack: result, strategy_name, strategy_accuracy, player_hand, dealer_hand
   - NPCDialog: tone, personality_score, tone_match, dialog
5. **Quality check:** Run `python tests/integration_checks.py <pipeline> <run_dir>` and verify all checks pass.
6. **TUI home check:** `python scripts/tui.py --dump` shows the run with correct status and metrics.
7. **TUI detail check:** `python scripts/tui.py --dump --run-dir {run_dir}` shows correct pipeline steps and stats.

If ANY check fails, mark the run as FAIL and record which specific checks failed with details.

## REPORTING

After all 10 runs, write a test report to:

```
tests/results/INTEGRATION_REPORT_YYYY-MM-DD_HHMMSS.md
```

Also create `tests/results/INTEGRATION_REPORT_LATEST.md` as a copy of the most recent report.

The report should contain:
- Summary table (pipeline × provider, pass/fail for pipeline, TUI, and quality)
- Per-combination detail:
  - Run directory name
  - Units, valid, failed, cost, duration, tokens
  - Post-run verification results (all 7 checks with pass/fail)
  - Quality check results with specific values
  - 2-3 sample validated records (abbreviated — unit_id and key output fields only)
  - TUI verification results
- Cost summary by provider
- Speed summary by provider
- Any failures or warnings with details
- TUI rendering assessment
- Your assessment of overall system health
- Execution timing (per-group wall clock time and total)

If a run fails, record the failure and continue to the next group.
