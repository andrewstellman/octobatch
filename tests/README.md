# Tests & Quality

This directory contains Octobatch's quality infrastructure: test protocols, review processes, and automated checks. The goal is to make quality repeatable and AI-friendly — every protocol in this directory can be executed by pointing an AI coding assistant at the relevant file.

## Quality Philosophy

See `ai_context/QUALITY.md` for the full quality constitution. The short version: quality is built into context, not inspected after the fact. Every AI session inherits the same quality bar through persistent documentation.

Three levels of quality assurance, from fastest to most thorough:

| Level | What | When | Time |
|-------|------|------|------|
| Code Review | Review recent changes for bugs | Before every release, after features | ~10 min |
| Integration Tests | Run all pipelines across all providers | Before every release | ~25 min |
| Regression Tests | Full codebase audit against intent specs | After major architectural changes | ~2 hours |

## How to Run Each

### Code Review

Catches bugs in recent changes. Give two or more AI tools the same prompt independently, then cross-reference findings.

**Protocol:** `tests/RUN_CODE_REVIEW.md`

```
Read tests/RUN_CODE_REVIEW.md and perform a code review.
```

To scope to specific files or a time range:
```
Read tests/RUN_CODE_REVIEW.md and review only scripts/orchestrate.py.
Read tests/RUN_CODE_REVIEW.md and review changes from the last commit: git diff HEAD~1
```

**Output:** `tests/code_reviews/CODE_REVIEW_[TOOL]_[DATE].md`

**After the review:** Write a fix spec (see `tests/CODE_REVIEW_FIX_SPEC.md` for an example), implement fixes in batches, then have reviewers verify the fixes.

### Integration Tests

Runs all three demo pipelines across all three providers (Gemini, OpenAI, Anthropic) plus a batch mode test. 10 combinations total.

**Protocol:** `tests/RUN_INTEGRATION_TESTS.md`

```
Read tests/RUN_INTEGRATION_TESTS.md and run the full integration test suite.
```

Best run with `claude --dangerously-skip-permissions` since the protocol is read-only (safety constraints are built into the file).

**Output:** `tests/results/INTEGRATION_REPORT_[DATE].md`

**Automated checks:** `tests/integration_checks.py` validates field names, enums, and threshold gates for each pipeline.

### Regression Tests (Council of Three)

The most thorough quality process. Three independent AI tools audit the entire codebase against intent specifications. Each tool reviews independently with the same prompt, preventing any single model's blind spots from going undetected.

**Key finding from our first regression cycle:** 74% of defects were found by only one auditor. No single AI model is sufficient.

**Protocol:** `tests/RUN_REGRESSION_TESTS.md`

**Intent specs (what the system should do):**
- `specs/ORCHESTRATOR.md`
- `specs/VALIDATION.md`
- `specs/EXPRESSION.md`
- `specs/PROVIDERS.md`
- `specs/TUI.md`

**When to use:** Before major releases, after architectural changes, when intent specs are updated. This is heavy — budget 2+ hours and expect 20-40 findings that need triage.

## Directory Contents

```
tests/
  README.md                        — This file
  RUN_CODE_REVIEW.md               — Code review protocol
  RUN_INTEGRATION_TESTS.md         — Integration test protocol
  RUN_REGRESSION_TESTS.md          — Council of Three regression protocol
  CODE_REVIEW_FIX_SPEC.md          — Example: fix spec from v1.0 code review
  integration_checks.py            — Automated pipeline quality checks
  test_tui.py                      — TUI unit tests
  results/                         — Integration test reports
  code_reviews/                    — Code review reports
```

## The Workflow

For a typical release:

1. **Develop** — implement features using spec-driven development
2. **Code review** — run `RUN_CODE_REVIEW.md` with 2+ AI tools
3. **Fix** — write a fix spec, implement in batches, verify fixes
4. **Integration test** — run `RUN_INTEGRATION_TESTS.md` to verify all pipelines work across all providers
5. **Ship** — tag the release

For major architectural changes, add a regression cycle (step 0) before the code review.

## Related Files

- `ai_context/QUALITY.md` — Quality constitution (coverage targets, fitness scenarios, review protocol, human gate)
- `specs/` — Intent specifications for each subsystem
- `ai_context/DEVELOPMENT_CONTEXT.md` — Current development state and bootstrap instructions
