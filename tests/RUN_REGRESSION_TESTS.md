# Octobatch v1.0: Council of Three Regression & Verification Methodology

## Overview

This document outlines a quality assurance methodology using three independent AI coding assistants to audit a codebase against written intent specifications. Each auditor examines the same specs and codebase independently, preventing AI sycophancy (where a model rubber-stamps its own code) and ensuring strict architectural alignment.

**Key Insight:** During our auditing process, only 3 out of 43 defects were found by all three auditors. 32 defects (74%) were found by only one auditor. No single AI model is sufficient for a comprehensive code audit.

## Roles

| Role | Tool | Model | Purpose |
|------|------|-------|---------|
| **Executor** | Claude Code | Opus 4.6 | Writes the code, performs initial implementation, and conducts deep read-only analysis with exact line-number citations. |
| **Logician** | Cursor | GPT-5.3 Codex | Acts as the primary defect hunter. Focuses on logical edge cases, race conditions, unhandled states, and strict architectural compliance. |
| **Analyst** | Copilot | Gemini 2.5 Pro | Verifies system-wide impacts, scaling bottlenecks, and overall architectural coherence. |

## Prerequisites

- **Intent Specifications:** Markdown documents describing what the system is strictly intended to do (not aspirational specs). In our case: `ORCHESTRATOR.md`, `VALIDATION.md`, `EXPRESSION.md`, `PROVIDERS.md`, `TUI.md`.
- **Quality Framework:** `QUALITY.md` defining fitness-to-purpose scenarios.
- **Access:** Local access to all three AI tools with the current codebase loaded.

---

## Phase 1: Initial Audit & Prompt Guardrails

**Goal:** Each auditor independently compares intent specs against actual code.

### Round 1 (Without Guardrails)

| Auditor | Model | Defects Found | Quality |
|---------|-------|---------------|---------|
| Claude Code | Opus 4.6 | 32 | High — every defect had line numbers and verified citations |
| Copilot | Gemini 2.5 Pro | 8 | Low — made factual errors (claimed features didn't exist when they did) |
| Cursor | GPT 5.3 Codex | 21 | Good — but connectivity issues delayed completion |

**Problem:** Copilot claimed type coercion and `loop_until` didn't exist when they clearly did with specific line-number evidence. Some findings across auditors lacked line numbers, making claims unverifiable.

### Round 2 (With Guardrails) — THE DEFINITIVE PROMPT

The following prompt was given identically to all three auditors (with tool-specific file reference syntax adapted per tool). **This is the prompt to reuse in future regression cycles:**

```
Read these files first:
- ai_context/QUALITY.md
- specs/ORCHESTRATOR.md
- specs/VALIDATION.md
- specs/EXPRESSION.md
- specs/PROVIDERS.md
- specs/TUI.md

These are our Intent Specs — what we believe the system currently does.

Your task: Act as the Tester. Read the actual Python codebase and strictly
compare it against these Intent Specs. Generate specs/DEFECTS_[AUDITOR].md.

Rules:
- I do NOT want a summary of what matches. ONLY list defects.
- A defect is: code that fails to do what a spec says, does it differently,
  has features the specs don't mention, or where a spec describes something
  that doesn't exist in the code.
- Be ruthless. We are looking for defects.
- For EVERY defect, cite the specific file and line number(s) in the code.
  If you cannot cite a line number, you have not verified the claim — do not
  include it.
- Before claiming a feature is missing, grep the codebase for it. If you find
  it in a different file or function than expected, that's a defect in
  location, not a missing feature.
- Before claiming a feature exists, verify by reading the actual function
  body — don't rely on function names, imports, or comments alone.
- Classify each defect: MISSING (spec describes something code doesn't do),
  DIVERGENT (code does it differently than spec says), UNDOCUMENTED (code has
  features specs don't mention), or PHANTOM (spec describes something that
  was never built).

Specific areas to scrutinize:
1. Do the OpenAI and Anthropic batch/realtime providers actually exist and
   function, or are they stubs/missing? Read the actual method bodies, not
   just the class definitions.
2. Does the code handle PID file / manifest synchronization correctly on
   resume?
3. Does batch mode extract Markdown before Phase 1 validation?
4. Are there CLI flags in orchestrate.py that aren't in the specs?
5. Does the expression evaluator implement hash(unit_id) seeding and
   loop_until exactly as described? Read the actual seeding code and state
   what formula it uses.
6. Does raw_response capture work in both batch and realtime modes?
7. Does automatic type coercion exist in schema_validator.py? Search for
   coerce, coercion, or type conversion functions.
8. Are there any features in the code that none of the specs mention?
```

### The Four Guardrails That Made the Difference

1. **Line numbers mandatory** — "If you cannot cite a line number, you have not verified the claim — do not include it." Prevents confident claims without evidence.
2. **Grep before claiming missing** — "If you find it in a different file or function than expected, that's a defect in location, not a missing feature." Eliminates false negatives like Copilot's Round 1 errors.
3. **Read function bodies, not signatures** — "Don't rely on function names, imports, or comments alone." Prevents marking stubs as implemented.
4. **Classification taxonomy** — MISSING, DIVERGENT, UNDOCUMENTED, PHANTOM. Forces structured thinking about *what kind* of defect it is, making the merged report easier to triage.

### Round 2 Results

| Auditor | Model | Round 1 | Round 2 | Change |
|---------|-------|---------|---------|--------|
| Claude Code | Opus 4.6 | 32 | 36 | +4, caught new issues (dead code, raw_response semantics) |
| Copilot | Gemini 2.5 Pro | 8 | 21 | **+13 (nearly 3x), zero false claims** |
| Cursor | GPT 5.3 Codex | — | 21 | First successful run (connectivity issues in Round 1) |

Copilot's improvement from 8 to 21 defects with no false claims validates that guardrails dramatically improve audit quality.

---

## Phase 2: Merge, Triage & The Verification Probe

All defect reports were compared side-by-side and deduplicated by matching against the same code location/issue. Agreement levels tracked:

- 🟢 Found by all three auditors: **3 defects** (7%)
- 🟡 Found by two auditors: **8 defects** (19%)
- 🔴 Found by one auditor only: **32 defects** (74%)

**Total unique defects: 43**

### Triage Categories

| Category | Count | Action Required |
|----------|-------|-----------------|
| Spec bugs (spec wrong, code fine) | 11 | Update specs manually (~30 min) |
| Design decisions (human judgment) | 9 | Human decides, then update spec OR code |
| Real code bugs | 9 | Fix code in 5 batched sessions |
| Documentation gaps (features undocumented) | 14 | Update specs to document existing features (~45 min) |

**Key insight:** A single mega-prompt to "fix all 43 defects" was explicitly rejected. Many "defects" are spec bugs or design decisions that require human judgment. Code fixes were batched into 5 isolated groups by subsystem (Providers, raw_response, Failure routing, Config validation, Manifest safety) to maintain the tight feedback loop.

### The Verification Probe Pattern

During Phase 2, a critical "Hallucination Incident" occurred. Copilot (The Analyst) reported near-perfect success rates on several code fixes, while Cursor (The Logician) identified 7 critical failures in the same fixes.

**Resolution:** Claude Code was deployed as a *read-only verification probe* to establish ground truth by examining the actual code state. Claude confirmed Cursor was correct and Copilot had hallucinated success.

**Rule of thumb:** When auditors disagree on factual claims about code behavior, deploy a read-only probe to establish ground truth before proceeding. Never resolve factual disputes by majority vote — verify against the source.

---

## Phase 3: Code Fix Execution & The Council Review

### Execution

**Executor:** Claude Code (Opus 4.6) implemented fixes based on the execution plan (`DEFECTS_CODE_FIXES.md`), reading all 5 intent specs plus the code fixes checklist, and generating `phase3_fixes.diff` with 13 regression fixes.

### Review Prompt

All three reviewers independently verified the diff using this prompt:

```
Read the following specs:
- specs/ORCHESTRATOR.md
- specs/VALIDATION.md
- specs/EXPRESSION.md
- specs/PROVIDERS.md
- specs/TUI.md

Then read:
- specs/REGRESSION_CODE_FIXES.md (the prescribed fixes)
- specs/phase3_fixes.diff (what was actually implemented)

For each fix in REGRESSION_CODE_FIXES.md, verify:
1. Does the fix match the prescribed solution?
2. Does the fix match the spec's intent?
3. Are there logical errors, edge cases, or off-by-one bugs?
4. Are there missed call sites where the same pattern should have been fixed?
5. Does the fix introduce any new bugs?

Output format: For each fix, state PASS, PARTIAL, or FAIL with specific
reasoning.
```

### Cross-Reference Results

Cross-referencing the three independent reviews identified 4 subtle logic gaps that required a "Second Pass":

1. **Fix 11 (REDO):** Realtime cost cap was implemented per-chunk instead of per-unit.
2. **Fix 2 (PATCH):** `FatalProviderError` was being swallowed in the retry path.
3. **Fix 8 (PATCH):** Expression validation missed `init` and `loop_until` blocks.
4. **Fix 12 (PATCH):** TUI `ProcessInfoScreen` suffered from a recursive timer fan-out bug.

---

## Phase 4: Full System Regression Audit

With the core defects fixed, a comprehensive regression audit was run against all 5 intent specs. This yielded three major discoveries:

1. **Documentation Debt:** Several flagged "defects" were actually stale `*(Code fix pending)*` notes left in the specs after the code had already been fixed.

2. **The `hash()` Blocker (CRITICAL):** An auditor discovered that Python's native `hash()` function produces different values across process restarts due to `PYTHONHASHSEED` randomization (enabled by default since Python 3.3). Every seed computation using `hash()` was non-reproducible across runs. **Fix:** All `hash()` calls were replaced with stable `hashlib.sha256` implementations. This is the kind of cross-cutting architectural bug that only emerges when auditors are specifically instructed to verify reproducibility claims against actual runtime behavior.

3. **11 Core Deviations:** Including missing manifest lifecycle states (`status: "pending"`, `mode: "batch"`), missing top-level config validation checks, and fatal error handling gaps.

---

## Phase 5: Second Pass & Final Verification

### Second Pass Execution Prompt

```
Bootstrap: Read ai_context/DEVELOPMENT_CONTEXT.md and PROJECT_CONTEXT.md

Then read:
- specs/REGRESSION_CODE_FIXES.md
- specs/phase3_fixes.diff

Execute these 4 corrections:
- Fix 11 (REDO): Cost cap per-unit not per-chunk
- Fix 2 (PATCH): FatalProviderError swallowed in retry path
- Fix 8 (PATCH): Expression validation missing init/loop_until
- Fix 12 (PATCH): Timer fan-out on manual refresh
```

### Dual Verification

Both Cursor and Claude Code independently verified that all 4 second-pass corrections passed. The codebase achieved 100% alignment with the intent specs.

---

## Phase 6: Deferred Items

5 items were identified during the regression cycle but explicitly deferred to the v1 backlog to maintain focus on core stability:

1. Verify Anthropic batch adapter (stub vs. working).
2. TUI `reset_unit_retries` missing `.bak` file generation.
3. Schema validator missing boolean coercion for capitalized `"True"`/`"False"`.
4. Schema validator improperly mutating payload by deleting the `response` wrapper.
5. Missing `--version` CLI flag.

---

## Results Summary

| Metric | Value |
|--------|-------|
| Total unique defects found | 43 |
| Found by all three auditors | 3 (7%) |
| Found by two auditors | 8 (19%) |
| Found by one auditor only | 32 (74%) |
| Spec bugs (not code issues) | 11 |
| Design decisions requiring human judgment | 9 |
| Real code bugs fixed | 9 (in 5 batched sessions) |
| Documentation gaps documented | 14 |
| Second-pass corrections needed | 4 |
| Final verification: all items passing | ✓ |

---

## How to Replicate

1. **Write intent specs first** — Document what you believe the system currently does (not aspirational). These are your audit baseline.
2. **Add guardrails to the audit prompt** — Use the definitive prompt above. The 4 guardrails (line numbers, grep before claiming missing, read function bodies, classification taxonomy) are essential.
3. **Give the identical prompt to 3 different AI tools** — Same prompt, same specs, independent execution. Don't let auditors see each other's results.
4. **Merge findings and track agreement levels** — Deduplicate by code location. Mark 🟢/🟡/🔴 agreement.
5. **Triage into categories** — Spec bugs / design decisions / code bugs / documentation gaps. Many "defects" don't need code fixes.
6. **Human resolves design decisions** — AI should never unilaterally resolve spec-vs-code conflicts. The human architect decides.
7. **Execute code fixes in small batches** — Group by subsystem. Never one mega-prompt for everything.
8. **Have all 3 auditors review the fixes independently** — Use the review prompt above.
9. **Cross-reference reviews** — Fix items flagged by *any* reviewer, not just majority. Deploy verification probes for factual disputes.
10. **Final dual verification** — At least two auditors confirm all items pass before marking complete.

---

## Key Principles

- **No single AI model is sufficient** — 74% of defects were caught by only one auditor.
- **Guardrails dramatically improve quality** — Forcing line-number citations and code reading eliminates confident hallucinations. Copilot went from 8 to 21 findings.
- **Triage before fixing** — Many "defects" are actually spec bugs or design decisions.
- **Small batches, not mega-prompts** — Fix architecture in 5 focused sessions, not one massive change.
- **Verification probe for disputes** — When auditors disagree on facts, deploy a read-only probe for ground truth. Never resolve by majority vote.
- **Human architect decides ambiguities** — AI should never unilaterally resolve spec-vs-code conflicts.

---

## File Inventory in This Archive

| File | Description |
|------|-------------|
| `CODE_REVIEW_COPILOT.md` | Copilot's Phase 2 code fix review |
| `CODE_REVIEW_CURSOR.md` | Cursor's Phase 2 code fix review |
| `DEFECTS_CODE_FIXES.md` | Execution plan for code bug fixes |
| `REGRESSION_CLAUDE.md` | Claude Code's Phase 4 full regression audit |
| `REGRESSION_COPILOT.md` | Copilot's Phase 4 regression audit (original) |
| `REGRESSION_COPILOT_V2.md` | Copilot's Phase 4 regression audit (revised) |
| `REGRESSION_CURSOR.md` | Cursor's Phase 4 regression audit |
| `REGRESSION_CODE_FIXES.md` | Phase 3 code fixes checklist |
| `REGRESSION_REVIEW_CLAUDE_CODE.md` | Claude Code's review of Phase 3 fixes |
| `REGRESSION_REVIEW_COPILOT.md` | Copilot's review of Phase 3 fixes |
| `REGRESSION_REVIEW_CURSOR.md` | Cursor's review of Phase 3 fixes |
| `REGRESSION_REVIEW_SECOND_PASS_CLAUDE_CODE.md` | Claude Code's second-pass verification |
| `REGRESSION_REVIEW_SECOND_PASS_CURSOR.md` | Cursor's second-pass verification |
| `REGRESSION_TRIAGE.md` | Triage of regression findings |
| `recent_changes.diff` | Phase 2 code changes diff |
| `METHODOLOGY.md` | This file |

---

*Archive created: 2026-02-21*
*Regression cycle: regression_1*
*Codebase state: All Phase 3 fixes verified, 5 items deferred to v1 backlog*
