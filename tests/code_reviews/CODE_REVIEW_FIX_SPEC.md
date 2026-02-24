# Code Review Fix Spec — v1.0 Pre-Release

## Purpose

This spec prescribes fixes for findings from two independent code reviews
(Claude and GPT5) covering all code changes in the 7 days before 2026-02-23.
Fixes are grouped by subsystem. Each fix references the review finding,
describes the intended behavior, and explains how to verify the fix.

Dismissed findings are listed at the end with rationale.

---

## Group 1: Orchestrator Data Integrity

### Fix 1.1: revalidate_failures drops parse_error records
**Source:** Claude — orchestrate.py:5430 (CRITICAL)
**Problem:** Records that fail JSON parsing are added to `parse_errors` but
never included in `still_failing_records`. When the failures file is rewritten,
those records are silently deleted from disk.
**Fix:** Do NOT attempt to re-parse `parse_errors` — they contain fundamentally
broken JSON that will never pass revalidation regardless of schema changes.
When loading the failures file at the start of `revalidate_failures`, immediately
separate `parse_errors` from `schema_errors` and `logic_errors`. Hold the
`parse_errors` in memory. Run only the schema/logic errors through the
revalidation pipeline. Then write the `parse_errors` directly back into the
`{step}_failures.jsonl` file untouched alongside the `still_failing_records`.
**Verify:** Create a failures file with one valid failure and one unparseable
record. Run revalidation. Both records must be present in the output file.
The unparseable record must be byte-identical to the original.

### Fix 1.2: retry_validation_failures orphans in-flight batches
**Source:** Claude — orchestrate.py:3582-3585, 4707-4708 (CRITICAL)
**Problem:** `retry_validation_failures` now runs unconditionally at startup.
If a chunk has step 1 failures but is already at step 2 SUBMITTED,
retrying archives the step 1 failures and resets the chunk to step 1 PENDING,
orphaning the in-flight step 2 batch.
**Fix:** Add a strict state machine guard. The recovery scan must ONLY process
chunks that are in a terminal state (`VALIDATED` or `FAILED`). It must
explicitly skip any chunks in `{step}_SUBMITTED` or `{step}_PROCESSING`.
Do not attempt a single-line fix — this is a state machine invariant that
must be enforced at the entry point of the recovery scan.
**Verify:** Create a test scenario with a chunk at step2_SUBMITTED that has
step1_failures.jsonl. Run watch_run startup. The chunk must remain at
step2_SUBMITTED and step 1 failures must not be archived.

### Fix 1.3: Double progress_callback on expression loop timeouts
**Source:** Claude — orchestrate.py:3914-3915, 3939-3940
**Problem:** When a looping expression step times out, `progress_callback` is
called twice — once in the timeout block and again in the common post-loop
code. This inflates progress counters and corrupts ETA calculations.
**Fix:** Guard the second callback at line 3939 with
`if not (loop_until_expr and timed_out)`.
**Verify:** Run a DrunkenSailor pipeline with max_iterations set to 1. Confirm
progress count matches unit count exactly.

---

## Group 2: CLI Tools

### Fix 2.1: repair_run capped at 200 missing units
**Source:** Claude — run_tools.py:147, GPT5 — run_tools.py:147
**Problem:** `_verify_step` caps `missing_ids` at 200 for display, but
`repair_run` consumes this capped list as its repair source. Runs with >200
missing units are silently under-repaired.
**Fix:** Separate the data model from the presentation layer. `_verify_step`
must return the complete, uncapped list of `missing_ids` in its dictionary
payload so `repair_run` has all the data it needs. Apply the `[:200]`
truncation ONLY in the CLI print/display logic when formatting output for
the user. Do NOT remove the cap entirely — dumping 5,000 IDs to the terminal
on a massive attrition event will lock up the console.
**Verify:** Create a mock step report with 250 missing IDs. Run repair.
Confirm all 250 are included in retry chunks. Run verify. Confirm display
shows "200 of 250 shown" or similar truncation indicator.

### Fix 2.2: pipeline.index(step_name) unguarded ValueError
**Source:** Claude — run_tools.py:202
**Problem:** `pipeline.index(step_name)` raises ValueError if step name not
found. Can occur if manifest was modified between verify and repair.
**Fix:** Add a membership check before the index call. If step name not in
pipeline, skip with a warning.
**Verify:** Call repair with a step name not in the pipeline list. Confirm
graceful skip, not crash.

### Fix 2.3: verify/repair dead manifest guard
**Source:** Claude — run_tools.py:32-33, 177-179
**Problem:** `load_manifest()` raises exceptions, never returns None/empty.
The `if not manifest:` guard is dead code.
**Fix:** Replace with try/except around `load_manifest()` that catches
FileNotFoundError and json.JSONDecodeError and returns the appropriate error.
**Verify:** Call verify_run on a directory with no manifest. Confirm clean
error dict returned, not unhandled exception.

---

## Group 3: find_and_mark_missing.py

### Fix 3.1: Hardcoded step filter
**Source:** Claude — find_and_mark_missing.py:37
**Problem:** LLM step filter hardcodes `merge_stories` and `extract_units`.
Other expression steps would be incorrectly treated as LLM steps.
**Fix:** Read the config.yaml from the run directory and use step scope
(expression vs LLM) to filter, matching the orchestrator's logic.
**Verify:** Run on a DrunkenSailor run (which has an expression step).
Confirm the expression step is excluded from LLM step tracing.

### Fix 3.2: Crash on zero LLM steps
**Source:** GPT5 — find_and_mark_missing.py:65
**Problem:** `final_step = llm_steps[-1]` crashes with IndexError when no
LLM steps found.
**Fix:** Check `if not llm_steps:` and exit with a user-facing error message.
**Verify:** Run on a pipeline with only expression steps. Confirm clean error.

### Fix 3.3: Non-atomic manifest write
**Source:** Claude — find_and_mark_missing.py:256-257
**Problem:** Manifest written with plain `open()` instead of atomic
write-then-rename. Interrupt during write corrupts manifest.
**Fix:** Use the existing `save_manifest()` function (or write-then-rename
pattern) for atomic writes.
**Verify:** Confirm manifest write uses temp file + rename pattern.

### Fix 3.4: File handle leak
**Source:** Claude — find_and_mark_missing.py:33
**Problem:** `json.load(open(manifest_path))` opens file without `with`.
**Fix:** Use `with open(...) as f: json.load(f)`.
**Verify:** Code inspection.

### Fix 3.5: Naive timestamp
**Source:** Claude — find_and_mark_missing.py:254
**Problem:** `datetime.now().isoformat()` uses local time without timezone.
Rest of codebase uses UTC.
**Fix:** Use `datetime.now(timezone.utc).isoformat()`.
**Verify:** Code inspection.

---

## Group 4: Diagnostics

### Fix 4.1: Gzip file handling (3 functions)
**Source:** Claude — diagnostics.py:420-421, 524-536, 572-573,
GPT5 — diagnostics.py:178
**Problem:** `scan_step_health()`, `verify_disk_vs_manifest()`, and
`get_step_failure_analysis()` check file existence against `.jsonl` only,
missing `.jsonl.gz`. Additionally, `verify_disk_vs_manifest` uses plain
`open()` instead of `_open_jsonl_for_read()`.
**Fix:** All three functions must check for both `.jsonl` and `.jsonl.gz`.
Use `_open_jsonl_for_read()` for all file reads. Also update
`_get_failed_step()` to detect `_failures.jsonl.gz`.
**Verify:** Create a test with gzipped failure files. Confirm all four
functions correctly detect and read them.

---

## Group 5: TUI Archive Feature

### Fix 5.1: shutil.move name collision
**Source:** Claude — home_screen.py:1217, 1225; main_screen.py:2748,
GPT5 — home_screen.py:1225, main_screen.py:2748
**Problem:** If destination directory already exists, `shutil.move` nests
the source inside it instead of failing.
**Fix:** Check `dest.exists()` before moving. If it exists, show a
notification explaining the conflict and do not move.
**Verify:** Create a directory at the destination path. Attempt archive.
Confirm notification shown, no move performed.

### Fix 5.2: Empty/missing status blocks archive with misleading message
**Source:** Claude — main_screen.py:2713, GPT5 — main_screen.py:2724
**Problem:** When manifest is missing or unreadable, status is empty string
and user sees generic "Can only archive completed/failed runs" message.
**Fix:** When status is empty, show "Cannot archive: manifest missing or
unreadable" instead.
**Verify:** Remove manifest from a run directory. Attempt archive. Confirm
specific error message shown.

### Fix 5.3: Archived run name truncation
**Source:** Claude — home_screen.py:713-716
**Problem:** Archived run names with emoji prefix skip the truncation guard
applied to non-archived names. Long names can overflow.
**Fix:** Apply the same truncation logic to archived run names.
**Verify:** Create an archived run with a 60+ character name. Confirm it
displays truncated in the DataTable.

### Fix 5.4: "killed" status evaluation
**Source:** Claude — home_screen.py:1203
**Problem:** `terminal_statuses` includes "killed" but it's unclear if this
is a real manifest state.
**Fix:** Check if the orchestrator ever writes "killed" to manifests. If yes,
keep it. If no, remove it from the set.
**Verify:** Grep codebase for manifest status writes. Document finding.

### Fix 5.5: home_screen.py datetime TypeError
**Source:** Claude — home_screen.py:1021-1022
**Problem:** `run["started"].replace("Z", "+00:00")` fails because
`run["started"]` is a datetime object, not a string. Error is silently
caught, preventing the duration ticker from starting.
**Fix:** Use the same type-checking pattern already used at lines 740-747.
**Verify:** Start a run from the TUI. Confirm duration ticker updates in
real time on the home screen.

---

## Group 6: Config Validator

### Fix 6.1: Cascading expression errors
**Source:** Claude — config_validator.py:697-710
**Problem:** When an expression fails, the result is not injected into the
symtable. Downstream expressions that reference it produce cascading
"name not defined" errors.
**Fix:** On expression error, inject a safe fallback value (`0` or `""`,
NOT `None`) into the symtable so downstream expressions can be independently
validated.
**Verify:** Create an expression step where the first expression is invalid.
Confirm the second expression produces its own specific error, not a
cascading NameError.

### Fix 6.2: Config path verification
**Source:** Claude — config_validator.py:258
**Problem:** `_load_item_field_mocks()` may be using the wrong config path.
**Fix:** Read the three demo pipeline configs (DrunkenSailor, Blackjack,
NPCDialog) and verify that `_load_item_field_mocks()` successfully extracts
item fields from each. If the path is wrong, fix it to match the actual
config structure (`processing.items.source` and `processing.items.key`).
**Verify:** Run `--validate-config` on all three demo pipelines. Confirm no
expression step errors related to missing item fields.

---

## Group 7: Documentation

### Fix 7.1: Deleted file references
**Source:** Claude — DEVELOPMENT_CONTEXT.md:126, GPT5 — DEVELOPMENT_CONTEXT.md
**Problem:** References to `run.sh`, `run.cmd`, `setup.sh`, `setup.cmd` still
exist in docs despite these files being deleted.
**Fix:** Grep project for all references and remove or update them.
**Verify:** `grep -r "run\.sh\|run\.cmd\|setup\.sh\|setup\.cmd" --include="*.md"` returns no results.

---

## Dismissed Findings

### GPT5: blackjack.md accuracy field name
**Rationale:** False positive. The regenerated config uses `accuracy`, not
`strategy_accuracy`. The walkthrough YAML snippet correctly matches the
actual config. GPT5 is comparing against an older version.

---

## Deferred to v1.1

These findings are low risk, cosmetic, or edge cases that don't affect
correctness for the v1.0 release:

- `realtime_provider.py` "400" substring false positive (line 103)
- `trace_log` double datetime call (octobatch_utils.py:337)
- `tui_dump.py` batch cost always $0 (line 117)
- `diagnostics_screen.py` thread race on step index (line 147)
- `check_prerequisites` silent pass on unknown providers (orchestrate.py:561)
- `_load_model_registry` YAML parse on every call (runs.py:220)
- `extract_units.py` rmtree without safety bounds (line 90)
- `realtime_provider.py` call_duration only captures last attempt (line 109)
- Test improvements (asyncio_mode, vacuous assertions, hardcoded run names)
