# QUALITY.md — Octobatch Quality Constitution

## Purpose

This file defines what "quality" means for Octobatch. Every AI session working on this codebase must read this file and treat it as binding. It is not aspirational — every standard here exists because we learned the hard way what happens without it.

The core principle: **quality is built into context, not inspected into output** (Deming). Every new AI session starts with its own idea of "good enough" unless told otherwise. This file makes the bar explicit, persistent, and inherited.

---

## Quality Philosophy

Three quality theorists provide our intellectual foundation:

- **Deming** ("quality is built in, not inspected in") — This file embodies that principle. We build quality into the AI's context so it doesn't need to be inspected after the fact.
- **Juran** ("fitness for use") — Not "does it pass tests" but "does it fulfill its intended purpose under real-world conditions." Octobatch must not just run pipelines — it must be operable when pipelines fail halfway through 9,240-unit batch runs at 2am.
- **Crosby** ("quality is free") — Building quality infrastructure upfront costs less than debugging AI-generated code after the fact. The time spent writing this file saves 10x in rework.

---

## Testing Standards

### Coverage Goals

| Subsystem | Target | Why |
|-----------|--------|-----|
| Expression evaluator | 100% | Touches randomness and seeding. The drunken sailor reseeding bug passed all visual inspection — only statistical verification caught that sequential seeds created correlation bias (77.5% fell in water instead of theoretical 50/50). Subtle bugs here produce plausible-but-wrong output. |
| Orchestrator core (state machine) | 90% | Chunk state transitions are the heart of crash recovery. Untested transitions = silent data loss. |
| Validation pipeline | 90% | Schema + business logic validation is the only thing between LLM hallucinations and corrupted game data. The trailing comma bug (11% failure rate) was only caught by running large batches. |
| Provider adapters | 80% | Network code is inherently hard to test, but timeout handling, retry logic, and error categorization must be verified. The Gemini infinite hang (0% CPU, 40+ minutes) happened because the Client had no timeout parameter. |
| CLI flags & argument parsing | 80% | Every flag we add (`--verify`, `--repair`, `--units-file`) must work in combination. Edge cases like `--repair --run` triggering both repair and immediate retry. |
| TUI utilities (pure functions) | 90% | `runs.py`, `formatting.py`, `status.py` are pure functions with no UI dependencies. Easy to test, no excuse not to. |
| TUI screens & widgets | Critical paths automated, visual feel manual | Textual has a built-in async testing framework (`async with app.run_test() as pilot:`) that supports programmatic key presses, widget clicks, and DOM assertions. Use it for keybindings, state transitions, and screen navigation. Visual feel and responsiveness remain in the Human Gate checklist. |

### Test Discipline for AI Sessions

Before marking any task complete, every AI session must:

1. Run the existing test suite: `pytest tests/`
2. Add tests for new functionality (not just happy path — include error cases)
3. If modifying orchestrator state transitions: add a test that verifies the state machine handles the new path
4. If modifying validation: add a test with deliberately malformed input
5. If modifying provider code: add a test that verifies timeout/error handling
6. Update this file if new coverage gaps are discovered

### Why Every Gate Matters

Do not argue coverage targets down. Do not skip testing a path that "looks simple." The drunken sailor bug looked simple — `random.seed(unit_index)` instead of `random.seed(hash(unit_id))` — but it created correlated sequences across all units. Only a statistical test caught it. Simple-looking code with hidden complexity is the most dangerous kind.

---

## Fitness-to-Purpose Scenarios

These scenarios are both human-readable AND specific enough that an LLM reading this file can evaluate code changes against them. Each scenario comes from real experience.

### Scenario 1: Crash Recovery (The Silent Attrition Incident)

**What happened:** A 9,240-unit batch run was interrupted by network failures. The orchestrator recovered, but 1,693 units were silently lost — they started but never completed, and nothing flagged them as missing.

**The requirement:** If the orchestrator is killed or loses connectivity mid-batch and is restarted, it must:
- Resume without duplicating API calls (idempotency)
- Not silently drop units — every unit must end in either `valid` or `failed` state
- Provide a `--verify` command that compares input units against final validated outputs and reports the gap
- Enable recovery via `--repair` that marks missing units for retry

**How to verify:** Run a 100-unit pipeline. Kill the process at 50%. Restart. Verify that final unit count equals 100 (all valid or explicitly failed). Run `--verify` and confirm it reports 0 missing.

### Scenario 2: Retry Granularity (The 4,300-Unit Cascade)

**What happened:** 308 units failed across 64 chunks. Retrying reset entire chunks, cascading through all subsequent pipeline steps for every unit in those chunks — reprocessing 4,300 units instead of 308.

**The requirement:** Retry must only reprocess failed units, not entire chunks. When 5 units fail in a 100-unit chunk, only those 5 should be resubmitted.

**How to verify:** Create a 100-unit run. Inject 5 artificial failures at a middle pipeline step. Run retry. Count API calls — should be approximately 5, not 100.

### Scenario 3: Laptop Sleep / Network Loss

**What happened:** Closing a laptop during a realtime run killed network connections. The orchestrator hung silently, then crashed without logging an error. On restart, partial progress within a step was lost because checkpointing only happens at step boundaries.

**The requirement:**
- All API calls must have request-level timeouts (already implemented: 120s for Gemini)
- A watchdog must detect calls pending beyond a configurable threshold and fail them gracefully
- SIGINT must be responsive even during blocking API calls or `time.sleep()`
- The manifest must be saved as "paused" on graceful shutdown

**How to verify:** Start a realtime run. Send SIGINT during an API call. Verify the process exits within 5 seconds and the manifest shows "paused."

### Scenario 4: Extract Units Safety (The 16,479 Duplicate Bug)

**What happened:** Running `extract_units.py` on a run that already had extracted units appended new files to the existing output, creating 16,479 files instead of 8,932.

**The requirement:** `extract_units` must be idempotent. Running it twice produces the same result as running it once. Either clear the output directory before writing, or skip existing files, or use atomic replacement.

**How to verify:** Run extract_units. Count files. Run it again. Count files. Numbers must match.

### Scenario 5: PID File Alignment (The Zombie Detection Bug)

**What happened:** The TUI showed "PROCESS LOST" for a running process because the orchestrator wrote the PID to `orchestrator.pid` but the manifest stored the PID from a previous session. The TUI checked the manifest PID, found it dead, and declared the process lost.

**The requirement:** There must be exactly one source of truth for PID. When resuming a run, the orchestrator must update both the PID file and the manifest's `pid` field. The TUI must check the PID file (not a cached value) on every refresh tick.

**How to verify:** Start a run. Note PID. Kill it. Start again (new PID). Verify TUI shows "RUNNING" with the new PID, not "PROCESS LOST."

### Scenario 6: Batch Mode Observability (The Silence Trap)

**What happened:** Running in batch mode, the monitor showed "Status: Running" and then nothing for 20-40 minutes while Google processed batches server-side. User couldn't tell if the process was alive or frozen.

**The requirement:** In batch mode, the system must provide:
- Heartbeat logging every N seconds confirming the process is alive and polling
- State transition logging when chunks move between states
- Clear indication that silence is expected ("Waiting for Gemini... 12m since last update")

**How to verify:** Start a batch run. Watch the log for 5 minutes. Verify heartbeat lines appear even when nothing else changes.

### Scenario 7: Statistical Correctness (The Drunken Sailor Bug)

**What happened:** A Monte Carlo random walk simulation showed 77.5% probability of falling in the water instead of the theoretical ~50%. Root cause: `random.seed(unit_index)` created correlated sequences because sequential integers produce related random streams.

**The requirement:** Expression steps with randomness must use `hash(unit_id)` (or equivalent) for seeding, not sequential integers. Monte Carlo simulations run at sufficient scale must produce results within expected statistical bounds.

**How to verify:** Run the drunken sailor pipeline with 1,000+ units. Verify the fall-in-water rate is between 40-60% (not 75%+). Run twice with same seeds — results must be identical. Run with different seeds — results must differ.

**Generalized lesson:** Pure functions that look simple but handle data transformations or randomness are the highest risk for silent, plausible corruption. They produce output that looks right but is subtly wrong. Only statistical or mathematical verification catches them. This is why expression evaluation has a 100% coverage target.

### Scenario 8: Validation Catches Real Errors

**What happened:** The wound scoring step initially had an 11% failure rate due to a trailing comma in JSON output. The schema validator caught it, but only after running 9,240 units. Earlier, a `wound_count` field accepted strings via `oneOf` in the schema, causing int/string comparison failures downstream.

**The requirement:** The validation pipeline must catch:
- Malformed JSON (trailing commas, missing brackets)
- Schema violations (wrong types, missing required fields)
- Business logic violations (net_sum not matching path direction, wound_count not matching actual non-zero scores)
- And route all failures for retry with the raw LLM response preserved for debugging

**How to verify:** Feed the validator deliberately malformed input for each category. Verify each is caught and produces a meaningful error message, not a Python traceback.

---

## The Council of Three — Multi-Model Review Protocol

For critical architectural changes, code must be reviewed by at least two AI models. Assign roles based on observed strengths:

### Claude (The Architect)
**Prompt:** "Review this code against PROJECT_CONTEXT.md and TOOLKIT.md. Does the implementation match the documented architecture? Are there inconsistencies between what the docs say and what the code does? Are the interfaces clean?"

**Strengths observed:** Code consistency, following documented constraints, catching deviations from project context files.

### Gemini (The Analyst)
**Prompt:** "Here's the entire codebase. Look for system-wide issues: race conditions, state management problems, scaling bottlenecks, places where error handling is inconsistent across modules. Will this batch logic actually scale to 10k units?"

**Strengths observed:** Large context window analysis, catching things other models miss (found the wrong data path in cross-LLM review, caught the "wait longer" hack as bad engineering), practical operational concerns.

### ChatGPT/o-series (The Logician)
**Prompt:** "Here are the three most complex functions in the codebase [expression evaluator, realtime_run loop, manifest state transitions]. Find edge cases and logic bugs."

**Strengths observed:** Logical reasoning on complex functions, edge case discovery, formal correctness.

### When to Invoke the Council

- Any change to the orchestrator's state machine
- Any change to the validation pipeline
- Any change to expression evaluation or seeding
- Any new CLI flag that modifies run state
- Before any release

### Process

1. Each model reviews independently with its specific prompt
2. Capture all responses, especially disagreements
3. Human developer makes final judgment on which findings to act on
4. Document the decision and rationale

---

## The Human Gate

These quality checks require human judgment and cannot be automated or delegated to an AI:

### TUI Visual Verification
- [ ] Dashboard stats populate correctly on launch
- [ ] Pipeline visualization shows correct step states with colors
- [ ] Unit table scrolls smoothly with 9,000+ units (lazy loading)
- [ ] Progress percentages match actual completion
- [ ] Otto's narrative makes sense for current state
- [ ] Mode indicator (batch/realtime/mixed) is visible and correct
- [ ] Keyboard shortcuts work from every screen

### Operational Feel
- [ ] Starting a batch run feels responsive (< 2 seconds to first log line)
- [ ] SIGINT stops the process within 5 seconds
- [ ] Resuming a paused run picks up where it left off (verify by counting)
- [ ] The `--verify` → `--repair` → retry workflow feels natural
- [ ] Log output is informative without being noisy (no throttle spam, yes heartbeat)

### Prompt Template Quality
- [ ] Generated stories use 1910s Waite-era language (spot-check 10 random triples)
- [ ] Coherence distribution is roughly 47% Score 3, 7% Score 2, 46% Score 1
- [ ] Wound scoring produces 3-5 non-zero wounds per story (check validation stats)
- [ ] Journal fragments accurately reflect wound impact

### Documentation Accuracy
- [ ] README installation steps work on a clean machine
- [ ] Example configs produce working pipelines
- [ ] Screenshots match current UI
- [ ] Context files reflect current architecture

---

## Spec-Driven Development Process

For the v1 backlog, we follow the spec-driven approach validated in the game prototype project:

### Phase 1: Design Discussion (Already Done)
Every backlog feature has been discussed extensively, with real-world examples of the problem and often manual workarounds that define expected behavior.

### Phase 2: Specification
Before implementing any feature group, write a short spec that defines:
- What the features do
- Why they exist (with reference to the real incident that motivated them)
- How to verify they work (reference fitness-to-purpose scenarios above)
- What files are affected
- What is explicitly out of scope

**Hard requirement:** Every feature spec MUST explicitly map its requirements to at least one existing Fitness-to-Purpose scenario, or define a new one if it addresses a novel failure mode. This creates traceability from design intent through implementation to verification.

### Phase 3: Implementation
- Point Claude Code / Cursor at the spec
- Implement in small iterations
- Verify each iteration before moving on
- Run tests after every change

### Phase 4: Review
- Run the test suite
- Check fitness-to-purpose scenarios
- Invoke Council of Three for architectural changes
- Run Human Gate checklist for UI changes
- Update QUALITY.md if new scenarios are discovered

---

## AI Session Quality Discipline

Every AI session working on Octobatch must follow these rules:

1. **Read QUALITY.md before starting work.** This file defines the quality bar.
2. **Include the "why" in every change.** Comments, commit messages, and context file updates must explain rationale, not just what changed.
3. **Don't refactor deliberate decisions.** If something looks wrong but has a comment explaining why, respect it. Ask before changing.
4. **Test before declaring done.** Run `pytest tests/`. If you added a feature, add a test.
5. **Output a Quality Compliance Checklist before ending a session.** Before marking a task complete, explicitly state: which tests you ran, which fitness-to-purpose scenarios your change affects, which context files you updated, and how you verified the change works. This forces the chain-of-thought to verify its own work. Do not skip this step.
6. **Update context files.** If your change affects architecture, update the relevant CONTEXT.md. If it affects quality standards, update this file.
7. **Preserve fitness-to-purpose scenarios.** Never remove a scenario from this file. Only add new ones as new failure modes are discovered.

---

## Living Document

This file evolves as we discover new failure modes and quality requirements. When you encounter a bug that should have been caught, add a fitness-to-purpose scenario for it. When an AI session argues a coverage target down, add a "why" that explains the real-world consequence.

The goal is not perfection — it's a ratchet. Quality only goes up.
