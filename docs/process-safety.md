# Process Safety & Crash Recovery

[← Back to README](../README.md)

How Octobatch protects your data and your money when things go wrong.

---

## The Manifest: Single Source of Truth

Every run has a `MANIFEST.json` that tracks the complete state of every unit and chunk. The manifest is the only file the orchestrator trusts when deciding what to do next. If the manifest says a chunk is validated, it's validated — even if you restart the process, move the run directory, or resume on a different machine.

The manifest records: run status (running, paused, complete, failed), every chunk's current pipeline step and state, retry counts per unit, cost and token totals, and the PID of the orchestrator process.

## State Machine

Each chunk progresses through a predictable sequence of states:

```
{step}_PENDING → {step}_SUBMITTED → {step}_VALIDATED → next step...
                                   → {step}_FAILED (if retries exhausted)
```

A chunk advances to the next step only after all its units are either validated or permanently failed. The orchestrator never skips states or jumps ahead.

## What Happens When Things Go Wrong

### You close your laptop / lose network

All three providers (Gemini, OpenAI, Anthropic) have 120-second request timeouts. If a network call hangs, it will time out and fail after 2 minutes — not hang forever.

In **batch mode**, your submitted batches continue processing on the provider's servers. When you resume, the orchestrator polls for completed batches and picks up where it left off. No work is lost.

In **realtime mode**, the current API call fails. Completed units are already written to disk. The in-progress unit is lost and will be retried on resume.

### You press Ctrl+C (SIGINT)

The orchestrator catches SIGINT and shuts down gracefully within 5 seconds. It saves the manifest with status "paused", writes any pending results to disk, and exits with code 130. All completed work is preserved.

To resume: run the same command with `--watch` (batch) or `--realtime` on the same `--run-dir`.

### The process crashes or is killed

If the orchestrator dies without saving state (kill -9, system crash, OOM), the manifest's status remains "running" but the PID is dead. On the next startup:

1. The orchestrator reads the existing manifest
2. Updates the PID file and manifest with the new process ID
3. For batch mode: polls for any batches that completed while we were down
4. For realtime mode: checks which units were already validated and only reprocesses the rest
5. Retries any units that were in-flight when the crash happened

The TUI detects crashed processes by checking if the PID in the PID file is still alive. It shows these as "detached" or "process lost" and offers the Resume (R) key to restart.

### Units fail validation

When an LLM response fails schema or business logic validation, the unit is retried automatically (up to `validation_retry.max_attempts`, default 3). Each retry gets a fresh API call.

Retries operate at the **unit level**, not the chunk level. If 5 units fail in a 100-unit chunk, only those 5 are resubmitted. The 95 validated units are untouched.

Units that exhaust all retries are marked as permanently failed. They appear in the run's failure files and are excluded from the final validated output. The pipeline continues with the remaining units.

### Something looks wrong with a completed run

Use `--verify` to check a run's integrity:

```bash
python scripts/orchestrate.py --verify --run-dir runs/my_run
```

This compares the expected units (from the initial generation) against the actual validated and failed outputs at each step. It reports missing units (started but never completed), orphaned units (in output but not in input), and count mismatches.

If `--verify` finds problems, use `--repair` to fix them:

```bash
python scripts/orchestrate.py --repair --run-dir runs/my_run
```

This creates retry chunks for any missing units and resets the run status so the orchestrator can resume processing them.

## PID Management

The orchestrator writes its process ID to `orchestrator.pid` in the run directory. This file is the authoritative source for "which process owns this run." On resume, the PID file is overwritten with the new process's PID. The TUI reads the PID file on every 2.5-second refresh tick — it never caches the PID.

## Cost Protection

Every API call's cost is tracked in the manifest. The TUI shows running cost totals on both the home screen and the detail view. Batch mode is approximately 50% cheaper than realtime mode for the same work.

If you need to stop a run to control costs, Ctrl+C pauses cleanly. You can inspect the cost so far, then resume or abandon the run.

## Idempotency Guarantees

These operations are safe to run multiple times:

- **Resume a paused run**: Only processes units that aren't already validated
- **Extract units** (`extract_units.py`): Clears the output directory before writing, so running twice produces the same result
- **Verify a run** (`--verify`): Read-only inspection, changes nothing
- **Expression steps**: Produce identical output for the same seed, regardless of how many times they run
