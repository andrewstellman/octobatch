# Code Review — Claude — 2026-02-23

**Diff range:** `HEAD@{7.days.ago}` (full week: ~14,200 lines added, ~5,300 removed across 131 files)

**Scope:** All code changes. Docs, pipeline data, regression reports, and specs reviewed only for cross-references.

---

## CRITICAL — Data Loss / Corruption Risk

### scripts/orchestrate.py

- **Line 5430:** [BUG] `revalidate_failures` drops failure records whose `raw_response` is empty or unparseable. Records that fail the JSON parse step are added to `parse_errors` (lines 5401-5409) but are never added to `still_failing_records` (initialized at line 5430 from `hard_failures` only). When the failures file is atomically rewritten at lines 5536-5541, those `parse_errors` records are silently deleted from disk. These represent units that previously failed validation — they should be preserved. Fix: initialize `still_failing_records = list(hard_failures) + list(parse_errors)` at line 5430, or append `parse_errors` before the file write.

- **Lines 3582-3585 / 4707-4708:** [BUG] `retry_validation_failures` now runs unconditionally at `watch_run` and `realtime_run` startup, no longer gated by `is_run_terminal`. If `watch_run` is called on a run that has in-flight batches (e.g., a chunk at `step2_SUBMITTED` that has lingering `step1_failures.jsonl`), `retry_validation_failures` will archive those step 1 failures and reset the chunk state to `step1_PENDING` (line 931). This orphans the step 2 batch already submitted to the provider — batch results will never be collected because the chunk is no longer in `*_SUBMITTED` state. The `batch_id` field is not cleared, leaving the chunk in an inconsistent state. This scenario occurs in multi-step pipelines when a process restart triggers `watch_run` while batches are still processing.

### scripts/run_tools.py

- **Line 147:** [BUG] `_verify_step` caps `missing_ids` at 200 entries: `sorted(list(missing_ids))[:200]`. This is labeled "Cap for display" but `repair_run` (lines 190-192) consumes these capped IDs to determine which units to repair. If a step has >200 missing units, `repair_run` silently ignores the rest. The `total_missing` count at line 197 will also be wrong. This directly undermines the "Silent Attrition" fix this file is designed to provide (QUALITY.md Scenario 1).

- **Line 202:** [BUG] `pipeline.index(step_name)` raises `ValueError` if the step name is not found in the pipeline list. No try/except or membership check. Can occur if the manifest was modified between verify and repair.

- **Lines 32-33 / 177-179:** [BUG] `verify_run` and `repair_run` both check `if not manifest:` after calling `load_manifest()`. However, `load_manifest()` raises `FileNotFoundError` or `json.JSONDecodeError` — it never returns `None` or `{}`. The guard is dead code; a missing/corrupt manifest produces an unhandled exception instead of the intended error dict.

### scripts/find_and_mark_missing.py

- **Lines 256-257:** [BUG] Manifest is written non-atomically using `with open(manifest_path, "w") as f: json.dump(...)`. All other manifest writes use atomic write-then-rename (via `save_manifest`). If the process is interrupted during this write, the manifest is left partially written and corrupt.

- **Line 37:** [BUG] LLM step filter is hardcoded: `llm_steps = [s for s in pipeline if s not in ("merge_stories", "extract_units")]`. This only works for pipelines using exactly these names for non-LLM steps. Any other expression step is incorrectly treated as an LLM step, causing incorrect tracing. Unlike `run_tools.py`, this script has no way to determine step scope from config metadata.

---

## HIGH — Incorrect Behavior

### scripts/orchestrate.py

- **Lines 3914-3915 / 3939-3940:** [BUG] Double `progress_callback` invocation for timed-out expression loop units. When a looping expression step times out, `progress_callback` is called at line 3914 inside the `if timed_out:` block, and again at line 3939 in the common post-loop code. The callback increments `progress_count[0]` on every call (line 4912), so timed-out units inflate the progress counter and corrupt ETA calculations. The second callback at line 3939 should be guarded with `if not (loop_until_expr and timed_out):`.

### scripts/tui/utils/diagnostics.py

- **Lines 420-421, 431-432:** [BUG] `scan_step_health()` checks file existence against the plain `.jsonl` path only, which misses gzipped `.jsonl.gz` files. The code then uses `_open_jsonl_for_read()` (which handles gzip), but the existence check gates whether the file is opened at all. Valid and failure counts will be silently reported as 0 for compressed files.

- **Lines 524-536:** [BUG] `verify_disk_vs_manifest()` has the same missing-gzip-check problem, plus uses plain `open()` instead of `_open_jsonl_for_read()`. Even if the existence check were fixed, gzipped files would fail to decompress. Disk verification reports false discrepancies.

- **Lines 572-573:** [BUG] `get_step_failure_analysis()` same pattern — `failures_file.exists()` only checks non-gz path. Error analysis shows "No failures" when failures exist in `.gz` format.

### scripts/tui/screens/home_screen.py

- **Lines 1021-1022:** [BUG] `run["started"].replace("Z", "+00:00")` — `run["started"]` is a `datetime` object (from `_build_run_data_from_manifest`), not a string. `datetime.replace()` does not accept positional string arguments. This raises `TypeError`, caught silently by `except Exception: pass` at line 1025. Result: duration ticker never starts for runs that transition to "running" between full table rebuilds in `_do_progress_tick`. The correct pattern (already used at lines 740-747) handles both types.

- **Lines 1217 / 1225:** [BUG] `shutil.move(str(run_path), str(dest))` — if `dest` already exists as a directory, `shutil.move` nests the source inside it, creating `_archive/run_name/run_name/`. Should check `dest.exists()` before moving.

### scripts/tui/screens/main_screen.py

- **Line 2748:** [BUG] Same `shutil.move` name-collision issue as home_screen.py. Both archive code paths share this bug.

### scripts/tui_dump.py

- **Lines 117-121:** [BUG] Cost computed solely from `manifest.get("realtime_progress", {}).get("cost_so_far", 0)`. For batch-mode runs, `realtime_progress` is absent, so cost always shows `$0.0000`. Should fall back to `get_run_cost_value(manifest)` for batch runs.

---

## MEDIUM — Correctness Concerns

### scripts/octobatch_utils.py

- **Lines 337-338:** [BUG] `trace_log` calls `datetime.now(timezone.utc)` twice — once for date/time, once for milliseconds. These are separate calls, so the milliseconds could belong to a different second than the timestamp (e.g., at second boundaries). Fix: capture `now = datetime.now(timezone.utc)` once.

### scripts/realtime_provider.py

- **Line 103:** [QUESTION] Fatal error detection uses substring matching: `"400" in error_str_check`. This can false-positive on error messages containing "400" in other contexts (e.g., "Request payload exceeds 1400 bytes" matches "400"). A false positive raises `FatalProviderError`, aborting the entire run.

### scripts/config_validator.py

- **Line 258:** [QUESTION] `_load_item_field_mocks()` uses config path `processing.items.key`. PROJECT_CONTEXT.md documents config structure as `items_source: items.yaml` at the top level. If no pipeline config uses this path, the function always returns `{}`, silently degrading sequential validation to having no item field context.

- **Lines 697-710:** [QUESTION] When an expression evaluation has errors, the result is NOT injected into the symtable. Subsequent expressions referencing that variable fail with cascading "name not defined" errors. The old code pre-seeded all variables with `0`, avoiding this cascade. Consider injecting a fallback value on error.

### scripts/find_and_mark_missing.py

- **Line 254:** [QUESTION] `datetime.now().isoformat()` produces a local timezone timestamp without timezone info. Rest of codebase uses `datetime.now(timezone.utc)`. Inconsistent timestamp formats may cause ordering/comparison issues.

- **Line 33:** [BUG] `json.load(open(manifest_path))` opens file without `with` statement. File handle leaked.

### scripts/tui/screens/diagnostics_screen.py

- **Line 147:** [BUG] `self._selected_step_index = i` written from a background thread (`@work(thread=True)` at line 134), but also read/written on the main thread by `action_step_prev/next` (lines 336-346). Race condition if user presses arrow keys while background scan is running.

---

## LOW — Minor Issues / Questions

### scripts/orchestrate.py

- **Lines 2285-2286:** [QUESTION] When a batch result has non-JSON content AND a provider-level `error` field, the `continue` skips the error check. Provider error metadata is lost in the failure record.

- **Lines 561-563:** [QUESTION] `check_prerequisites` silently passes unknown provider names (not in `PROVIDER_KEY_MAP`). A typo like `"opneai"` would only surface at API call time.

### scripts/tui/screens/home_screen.py

- **Line 1203:** [QUESTION] `terminal_statuses = {"complete", "failed", "killed"}` — is `"killed"` a real manifest status distinct from `"failed"`? CONTEXT docs only list `running/paused/failed/complete`.

- **Lines 713-716:** [QUESTION] Archived run names with Rich markup prefix are never truncated. Long archived names could overflow the DataTable column.

### scripts/tui/utils/runs.py

- **Lines 236-237:** [INCOMPLETE] Docstring for `_get_model_pricing()` says it returns a 3-tuple but it returns a 2-tuple. Caller is correct; docstring is wrong.

- **Lines 220-230:** [QUESTION] `_load_model_registry()` parses `models.yaml` from disk on every call, invoked per-run in `scan_runs()`. For 50+ runs, this means 50+ YAML parses per refresh cycle.

### scripts/extract_units.py

- **Lines 90-91:** [QUESTION] `shutil.rmtree(output_dir)` for idempotency — if `output_dir` is an absolute path from config pointing somewhere unexpected, this is destructive with no safety bounds check.

### scripts/realtime_provider.py

- **Line 109:** [QUESTION] `call_duration` for exhausted retries only captures the last attempt's duration, not total wall-clock time including backoff sleeps.

### scripts/tui/screens/main_screen.py

- **Line 2713:** [QUESTION] When manifest is unreadable, `status` remains `""` and user sees "Can only archive completed/failed/killed runs" — misleading when the actual problem is a corrupt manifest.

---

## Tests

### tests/test_tui.py

- **Line 31:** [BUG] Missing `asyncio_mode` configuration. With `pytest-asyncio` v0.21+, default mode is `strict`. Without a `pyproject.toml` or `conftest.py` setting `asyncio_mode = "auto"`, async tests may not execute.

- **Lines 65-84:** [QUESTION] `test_data_table_populates` passes vacuously when no runs exist (no assertion executes). Should either `pytest.skip()` or assert `row_count == 0`.

- **Lines 146-154:** [INCOMPLETE] `test_dump_home_text` asserts `"Run"` and `"Status"` in output, but these only appear when runs exist. Fails on fresh checkouts or CI with no `runs/` directory.

- **Lines 204-230:** [INCOMPLETE] `test_completed_with_failures_shows_complete` hardcodes specific run directory names. Never exercised without pre-existing run data.

### tests/integration_checks.py

- **Lines 133, 223:** [QUESTION] Hardcoded step name fallbacks (`"analyze_difficulty"`, `"score_dialog"`) are fragile. If step names change, checks silently load no data and report failure without explaining why.

---

## Files with No Findings

- `scripts/providers/base.py`, `gemini.py`, `openai.py`, `anthropic.py` — dead code removal of `normalize_response()`, zero callers confirmed
- `scripts/schema_validator.py` — no findings
- `scripts/validator.py` — no findings
- `scripts/generate_units.py` — `hash()` → `hashlib.sha256()` for deterministic seeding is correct
- `scripts/tui/screens/modals.py` — clean ArchiveConfirmModal
- `scripts/tui/screens/common.py` — no findings
- `scripts/tui/screens/process_info.py` — no findings
- `scripts/tui/data.py` — no findings
- `scripts/tui.py` — no findings
- `docs/examples/blackjack.md`, `docs/examples/npc-dialog.md` — documentation only
- Deleted: `run.sh`, `run.cmd`, `setup.sh`, `setup.cmd` — replaced by `octobatch`/`octobatch-tui` wrappers

---

## Summary

| Severity | Count |
|----------|-------|
| BUG | 20 |
| QUESTION | 14 |
| INCOMPLETE | 4 |

### Top Priority Fixes

1. **`revalidate_failures` drops parse_error records** (orchestrate.py:5430) — silent unit loss
2. **`retry_validation_failures` orphans in-flight batches** (orchestrate.py:3582) — chunk state corruption on restart
3. **`repair_run` capped at 200 missing units** (run_tools.py:147) — undermines Silent Attrition fix
4. **Gzip file handling in diagnostics** (diagnostics.py:420/524/572) — 3 functions silently report 0 for compressed files
5. **`find_and_mark_missing.py` non-atomic manifest write** (line 256) — corruption risk on interrupt

### Overall Assessment: **FIX FIRST**

The week's changes add significant functionality (CLI tools, archive feature, diagnostics, config validator refactor, revalidation). Most of it is well-structured. However, there are data integrity issues in the orchestrator's new `revalidate_failures` path, the `repair_run` 200-unit cap directly contradicts its stated purpose, and the diagnostics module doesn't handle gzipped JSONL files. These should be fixed before the next production batch run.
