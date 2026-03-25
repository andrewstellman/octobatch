# Spec Audit Protocol: Octobatch

## The Definitive Audit Prompt

Give the following prompt, unchanged, to three independent AI tools.

---

**Context files to read:**

1. `quality/QUALITY.md`
2. `AGENTS.md`
3. `ai_context/PROJECT_CONTEXT.md`
4. `scripts/CONTEXT.md`
5. `specs/ORCHESTRATOR.md`
6. `specs/VALIDATION.md`
7. `specs/EXPRESSION.md`
8. `specs/PROVIDERS.md`
9. `specs/TUI.md`
10. `pipelines/TOOLKIT.md`

**Task:** Act as the Tester. Read the actual code in `scripts/`, `scripts/providers/`, `scripts/tui/`, and the pipeline configs in `pipelines/`. Compare the implementation against the specifications listed above.

**Requirement confidence tiers:**
- `formal` — written in a spec or explicit project doc. Treat divergence as a real finding.
- `user-confirmed` — stated by the user but not formalized. Treat as authoritative unless contradicted.
- `inferred` — deduced from code behavior. Report divergence as `NEEDS REVIEW`, not as a definitive defect.

**Rules:**
- ONLY list defects. Do not summarize what matches.
- For EVERY defect, cite specific file and line number(s). If you cannot cite a line number, do not include the finding.
- Before claiming something is missing, grep the codebase.
- Before claiming something exists, read the actual function body.
- Classify each finding as `MISSING`, `DIVERGENT`, `UNDOCUMENTED`, or `PHANTOM`.
- For findings against inferred requirements, append `NEEDS REVIEW`.

**Defect classifications:**
- `MISSING` — spec requires it, code does not implement it
- `DIVERGENT` — code and spec both address it, but disagree
- `UNDOCUMENTED` — code does it, spec does not mention it
- `PHANTOM` — spec describes it, but the implementation behaves differently than described

**Project-specific scrutiny areas:**

1. Read `scripts/orchestrate.py` around manifest writes, retry logic, `run_validation_pipeline()`, `retry_validation_failures()`, `watch_run()`, and `realtime_run()`. Do any paths permit silent unit loss, duplicate work, or terminal-state misclassification?
2. Read `scripts/schema_validator.py`, `scripts/validator.py`, and the validation flow in `scripts/orchestrate.py`. Does raw provider output get preserved and correctly classified when JSON parsing, schema validation, or business validation fails?
3. Read `scripts/expression_evaluator.py` and `scripts/generate_units.py`. Does seeded randomness remain deterministic and statistically safe, especially for repetition-heavy Monte Carlo pipelines?
4. Read `scripts/providers/__init__.py`, `scripts/realtime_provider.py`, and provider adapters. Does provider/model precedence truly follow CLI flags > step config > global config > registry default, and are transient vs fatal errors handled consistently?
5. Read the TUI state-reading code in `scripts/tui/` and dump mode in `scripts/tui.py` / `scripts/tui_dump.py`. Does the UI rely only on filesystem truth, and do detached/zombie/paused/running states match the specs?
6. Compare each demo pipeline config in `pipelines/` against `pipelines/TOOLKIT.md` and the validation specs. Are the 4-Point Link Rule and expression-step exemptions applied consistently?
7. Cross-check every QUALITY scenario against the current code. If the code no longer supports the scenario, report whether QUALITY.md is stale or the implementation regressed.
8. Look for field-level drift between pipeline schemas/configs and helper tooling such as `tests/integration_checks.py`. Report any mismatch that weakens the intended quality gates.

**Output format:**

### path/to/file.py
- **Line NNN:** `MISSING` / `DIVERGENT` / `UNDOCUMENTED` / `PHANTOM` `[Req: tier — source]` Description.
  - **Spec says:** quote or precise paraphrase
  - **Code does:** observed behavior from the implementation
  - **Why it matters:** which QUALITY scenario or operator risk this affects

---

## Running the Audit

1. Run the prompt independently with three different models.
2. Do not let one auditor read another auditor's report first.
3. Save each raw report to `quality/spec_audits/YYYY-MM-DD-[model].md`.

Suggested role mix:

- one architecture-oriented model
- one edge-case-oriented model
- one cross-check / verification-oriented model

## Triage Process

After collecting the three reports, merge findings by overlap and confidence.

| Confidence | Found By | Action |
|------------|----------|--------|
| Highest | All three auditors | Treat as almost certainly real; fix or update spec immediately |
| High | Two of three auditors | Verify quickly in code, then fix or update spec |
| Needs verification | One auditor only | Run a read-only verification probe before acting |

### Verification Probe

When auditors disagree, do **not** resolve the dispute by majority vote alone. Launch a focused read-only probe asking one model to inspect the cited files and report ground truth with line numbers.

## Categorize Every Confirmed Finding

Use one of these dispositions:

- `spec bug` — code is fine, spec is wrong
- `design decision` — requires human judgment
- `real code bug` — implementation must change
- `documentation gap` — feature exists but is not documented
- `missing test` — code may be correct, but no automated test protects it
- `inferred requirement wrong` — QUALITY.md inference needs correction

## Fix Execution Rules

- Group fixes by subsystem, not by report order.
- Never ask one AI session to fix every finding in one batch.
- For each fix batch: implement, run targeted tests, then re-review the changed diff.
- Any confirmed finding that changes behavior should either add a functional test or update `quality/QUALITY.md`.

## Output Files

Save these artifacts:

- `quality/spec_audits/YYYY-MM-DD-[model].md` — one per auditor
- `quality/spec_audits/YYYY-MM-DD-triage.md` — merged triage and dispositions
- optional `quality/spec_audits/YYYY-MM-DD-verification-probe.md` — for disputed findings
