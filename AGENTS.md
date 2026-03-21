# AGENTS.md

Octobatch is a batch processing orchestrator for LLM APIs. It transforms expensive real-time API operations into efficient batch workflows across Gemini, OpenAI, and Anthropic. Written in Python 3.11+, it features a TUI dashboard built with Textual, expression steps using asteval for deterministic computation, fan-out steps for one-to-many pipeline branching, cross-run analysis tooling, and multi-step pipelines with automatic validation and retry.

## Setup

```bash
git clone https://github.com/andrewstellman/octobatch.git
cd octobatch
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

API keys are set as environment variables: `GOOGLE_API_KEY` (Gemini), `OPENAI_API_KEY` (OpenAI), `ANTHROPIC_API_KEY` (Anthropic). Keys can be placed in a `.env` file in the project root (see `.env.example`); the orchestrator and TUI load it automatically via `python-dotenv`.

## Virtual environment

**Important:** Before running any Python commands, always check for and activate the existing virtual environment. All dependencies are already installed there — do not `pip install` packages manually.

```bash
# Activate the existing venv (do this first!)
source .venv/bin/activate
```

If `.venv` does not exist, create it with the Setup instructions above.

## Build & test

```bash
# Run all tests (venv must be active)
pytest tests/

# Run tests with coverage
pytest tests/ --cov=scripts --cov-report=term-missing

# Validate a pipeline config without running it
python3 scripts/orchestrate.py --validate-config --config pipelines/Blackjack/config.yaml

# Run a pipeline (realtime mode, small test)
python3 scripts/orchestrate.py --init --pipeline DrunkenSailor --run-dir runs/test --repeat 10 --realtime --provider gemini --yes

# Generate a pipeline report
python3 scripts/orchestrate.py --report --run-dir runs/test
python3 scripts/orchestrate.py --report --json --run-dir runs/test

# Re-validate failures without API calls
python3 scripts/orchestrate.py --revalidate --run-dir runs/test

# Restart a running orchestrator
python3 scripts/orchestrate.py --restart --run-dir runs/test

# Name a run
python3 scripts/orchestrate.py --name "My Experiment" --run-dir runs/test

# Compare runs side by side
python3 scripts/orchestrate.py --compare runs/run1 runs/run2

# Launch the TUI
python3 scripts/tui.py
```

All tests must pass before committing. Run `pytest tests/` to see the current test count. Coverage must not decrease — verify with `pytest tests/ --cov=scripts --cov-report=term-missing`. Core modules (config_validator, generate_units, octobatch_utils, run_tools, schema_validator) must stay above 80% coverage.

## Architecture

The codebase lives in `scripts/`:

- `orchestrate.py` — Main orchestrator. State machine managing chunk lifecycle, batch/realtime execution, retry logic, signal handling, fan-out steps, mode switching. Entry point for all CLI operations.
- `expression_evaluator.py` — Safe Python expression evaluation via asteval. Handles seeded randomness, looping expressions, and context variable injection.
- `validator.py` — Business logic validation using asteval expressions.
- `schema_validator.py` — JSON Schema Draft 2020-12 validation with type coercion and trailing comma recovery.
- `config_validator.py` — Pipeline config parsing and validation. Accepts `fan_out` scope.
- `generate_units.py` — Unit generation with permutation, direct, and cross_product strategies.
- `run_tools.py` — verify_run(), repair_run() for crash recovery. compare_runs(), compare_hands() for cross-run analysis. generate_report() with fan-out boundary display and failures-by-field grouping.
- `realtime_provider.py` — Realtime API execution wrapper.
- `providers/` — Gemini, OpenAI, Anthropic batch API adapters with 120s timeouts.
- `tui/` — Textual TUI application (screens, widgets, utilities). Includes modals for troubleshooting, confirmation, text input.

Pipelines live in `pipelines/` with config.yaml, templates/, schemas/, and items files.

Run data lives in `runs/` with MANIFEST.json as the single source of truth for run state.

## Code conventions

- Python 3.11+ with type hints.
- No external test framework beyond pytest. Use `tmp_path` fixture for filesystem tests.
- Expression steps (scope: expression) and fan-out steps (scope: fan_out) are exempt from the 4-Point Link Rule — they only need a pipeline.steps entry.
- LLM steps require entries in four places: pipeline.steps, prompts.templates, schemas.files, and validation.
- Underscore-prefixed variables in expressions (e.g., `_card_val`) are internal computation variables, not output fields.
- Chunk states follow: `{step}_PENDING` → `{step}_SUBMITTED` → `{step}_VALIDATED` / `{step}_FAILED`.
- Expression step validation failures are permanently failed (fast-fail), not retried, because re-running deterministic math on the same input produces the same result.
- Fan-out steps (`scope: fan_out`) expand array fields into child units. Child IDs: `{parent_id}__fan{NNN}`. Children packed into new chunks respecting `chunk_size`. Parent chunk marked terminal after fan-out.

## Key design decisions

- Deterministic work must be deterministic. Use expression steps for math, card dealing, random setup — never ask an LLM to do arithmetic.
- Validation failures on LLM steps trigger automatic retry (up to max_attempts). Validation failures on expression steps are permanent.
- The manifest (MANIFEST.json) is the single source of truth. Never cache PIDs or state — read from disk.
- Seeded randomness uses `hash(unit_id + step_name) & 0x7FFFFFFF`, not sequential integers. Sequential seeds create correlated random streams.
- Config + templates + schemas are snapshot-copied to the run directory on init, enabling independent re-runs.

## Detailed context

For deeper context, read these files in order:

1. `ai_context/DEVELOPMENT_CONTEXT.md` — Session state, recent work, bootstrap instructions
2. `ai_context/PROJECT_CONTEXT.md` — System architecture, design patterns, folder structure
3. `ai_context/QUALITY.md` — Quality standards, coverage targets, fitness-to-purpose scenarios
4. `scripts/CONTEXT.md` — Orchestrator internals
5. `scripts/tui/CONTEXT.md` — TUI application structure
6. `pipelines/TOOLKIT.md` — AI-facing reference for pipeline creation
