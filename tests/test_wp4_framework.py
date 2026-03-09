"""
Tests for Work Package 4 Framework Features:
- 4A: Fan-out steps (scope: fan_out)
- 4B: Re-validate failures (--revalidate) — already implemented, tested here for coverage
- 4C: AI-driven stochastic integration testing
"""

import json
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))


# =============================================================================
# Helpers
# =============================================================================

def write_manifest(run_dir: Path, manifest: dict) -> Path:
    manifest_path = run_dir / "MANIFEST.json"
    run_dir.mkdir(parents=True, exist_ok=True)
    with open(manifest_path, "w") as f:
        json.dump(manifest, f, indent=2)
    return manifest_path


def write_jsonl(file_path: Path, records: list[dict]) -> Path:
    file_path.parent.mkdir(parents=True, exist_ok=True)
    with open(file_path, "w") as f:
        for record in records:
            f.write(json.dumps(record) + "\n")
    return file_path


def write_config(run_dir: Path, config: dict) -> Path:
    import yaml
    config_dir = run_dir / "config"
    config_dir.mkdir(parents=True, exist_ok=True)
    config_path = config_dir / "config.yaml"
    config_path.write_text(yaml.dump(config))
    return config_path


# =============================================================================
# 4A: Fan-out steps
# =============================================================================

class TestFanOutStepDetection:
    """Test fan-out step detection functions."""

    def test_is_fan_out_step_true(self):
        from orchestrate import is_fan_out_step
        config = {
            "pipeline": {"steps": [{"name": "expand", "scope": "fan_out", "field": "items"}]}
        }
        assert is_fan_out_step(config, "expand") is True

    def test_is_fan_out_step_false(self):
        from orchestrate import is_fan_out_step
        config = {
            "pipeline": {"steps": [{"name": "generate", "scope": "chunk"}]}
        }
        assert is_fan_out_step(config, "generate") is False

    def test_is_fan_out_step_expression_not_fan_out(self):
        from orchestrate import is_fan_out_step
        config = {
            "pipeline": {"steps": [{"name": "calc", "scope": "expression"}]}
        }
        assert is_fan_out_step(config, "calc") is False

    def test_get_fan_out_step_config(self):
        from orchestrate import get_fan_out_step_config
        config = {
            "pipeline": {"steps": [
                {"name": "expand", "scope": "fan_out", "field": "variants", "child_field": "variant"}
            ]}
        }
        result = get_fan_out_step_config(config, "expand")
        assert result is not None
        assert result["field"] == "variants"
        assert result["child_field"] == "variant"

    def test_get_fan_out_step_config_missing(self):
        from orchestrate import get_fan_out_step_config
        config = {
            "pipeline": {"steps": [{"name": "generate", "scope": "chunk"}]}
        }
        assert get_fan_out_step_config(config, "generate") is None


class TestFanOutExecution:
    """Test the run_fan_out_step function."""

    def test_fan_out_creates_correct_children(self, tmp_path):
        from orchestrate import run_fan_out_step

        run_dir = tmp_path / "run"
        chunk_dir = run_dir / "chunks" / "chunk_000"
        chunk_dir.mkdir(parents=True)
        log_file = run_dir / "RUN_LOG.txt"
        log_file.touch()

        # Parent units with array field
        parents = [
            {"unit_id": "parent_001", "name": "Alice", "items": ["a", "b", "c"]},
            {"unit_id": "parent_002", "name": "Bob", "items": ["x", "y"]},
        ]
        write_jsonl(chunk_dir / "step1_validated.jsonl", parents)

        step_config = {"field": "items", "child_field": "item"}
        config = {"pipeline": {"steps": [
            {"name": "step1"},
            {"name": "fan_out", "scope": "fan_out", "field": "items", "child_field": "item"},
        ]}}
        manifest = {"pipeline": ["step1", "fan_out"]}

        count, failed, _, _ = run_fan_out_step(
            run_dir, "chunk_000", "fan_out", step_config, config, manifest, log_file
        )

        assert count == 5  # 3 + 2
        assert failed == 0

        # Read output
        output_file = chunk_dir / "fan_out_validated.jsonl"
        assert output_file.exists()
        children = []
        with open(output_file) as f:
            for line in f:
                children.append(json.loads(line.strip()))

        assert len(children) == 5

    def test_child_unit_ids_format(self, tmp_path):
        """Child IDs follow {parent}__fan{NNN} format."""
        from orchestrate import run_fan_out_step

        run_dir = tmp_path / "run"
        chunk_dir = run_dir / "chunks" / "chunk_000"
        chunk_dir.mkdir(parents=True)
        log_file = run_dir / "RUN_LOG.txt"
        log_file.touch()

        parents = [{"unit_id": "unit_A", "variants": [10, 20, 30]}]
        write_jsonl(chunk_dir / "prev_validated.jsonl", parents)

        step_config = {"field": "variants", "child_field": "variant"}
        manifest = {"pipeline": ["prev", "expand"]}

        count, _, _, _ = run_fan_out_step(
            run_dir, "chunk_000", "expand", step_config,
            {"pipeline": {"steps": [{"name": "prev"}, {"name": "expand", "scope": "fan_out"}]}},
            manifest, log_file
        )

        assert count == 3
        output_file = chunk_dir / "expand_validated.jsonl"
        children = [json.loads(line) for line in output_file.read_text().strip().split("\n")]

        assert children[0]["unit_id"] == "unit_A__fan000"
        assert children[1]["unit_id"] == "unit_A__fan001"
        assert children[2]["unit_id"] == "unit_A__fan002"

    def test_child_inherits_parent_fields(self, tmp_path):
        """Child units inherit all parent fields."""
        from orchestrate import run_fan_out_step

        run_dir = tmp_path / "run"
        chunk_dir = run_dir / "chunks" / "chunk_000"
        chunk_dir.mkdir(parents=True)
        log_file = run_dir / "RUN_LOG.txt"
        log_file.touch()

        parents = [{"unit_id": "u1", "color": "red", "items": ["apple"]}]
        write_jsonl(chunk_dir / "units.jsonl", parents)

        step_config = {"field": "items", "child_field": "item"}
        manifest = {"pipeline": ["expand"]}

        run_fan_out_step(
            run_dir, "chunk_000", "expand", step_config,
            {"pipeline": {"steps": [{"name": "expand", "scope": "fan_out"}]}},
            manifest, log_file
        )

        children = [json.loads(line) for line in
                     (chunk_dir / "expand_validated.jsonl").read_text().strip().split("\n")]

        assert children[0]["color"] == "red"
        assert children[0]["item"] == "apple"
        assert children[0]["_fan_parent_id"] == "u1"
        assert children[0]["_fan_index"] == 0

    def test_child_contains_individual_element(self, tmp_path):
        """Child units have the individual array element in child_field."""
        from orchestrate import run_fan_out_step

        run_dir = tmp_path / "run"
        chunk_dir = run_dir / "chunks" / "chunk_000"
        chunk_dir.mkdir(parents=True)
        log_file = run_dir / "RUN_LOG.txt"
        log_file.touch()

        parents = [{"unit_id": "u1", "scores": [100, 200]}]
        write_jsonl(chunk_dir / "units.jsonl", parents)

        step_config = {"field": "scores", "child_field": "score"}
        manifest = {"pipeline": ["expand"]}

        run_fan_out_step(
            run_dir, "chunk_000", "expand", step_config,
            {"pipeline": {"steps": [{"name": "expand", "scope": "fan_out"}]}},
            manifest, log_file
        )

        children = [json.loads(line) for line in
                     (chunk_dir / "expand_validated.jsonl").read_text().strip().split("\n")]

        assert children[0]["score"] == 100
        assert children[1]["score"] == 200

    def test_fan_out_empty_array(self, tmp_path):
        """Fan-out with empty array produces 0 children for that parent."""
        from orchestrate import run_fan_out_step

        run_dir = tmp_path / "run"
        chunk_dir = run_dir / "chunks" / "chunk_000"
        chunk_dir.mkdir(parents=True)
        log_file = run_dir / "RUN_LOG.txt"
        log_file.touch()

        parents = [
            {"unit_id": "u1", "items": []},
            {"unit_id": "u2", "items": ["x"]},
        ]
        write_jsonl(chunk_dir / "units.jsonl", parents)

        step_config = {"field": "items", "child_field": "item"}
        manifest = {"pipeline": ["expand"]}

        count, _, _, _ = run_fan_out_step(
            run_dir, "chunk_000", "expand", step_config,
            {"pipeline": {"steps": [{"name": "expand", "scope": "fan_out"}]}},
            manifest, log_file
        )

        assert count == 1  # Only u2's child

    def test_fan_out_missing_field(self, tmp_path):
        """Fan-out step with missing 'field' config returns 0."""
        from orchestrate import run_fan_out_step

        run_dir = tmp_path / "run"
        chunk_dir = run_dir / "chunks" / "chunk_000"
        chunk_dir.mkdir(parents=True)
        log_file = run_dir / "RUN_LOG.txt"
        log_file.touch()

        step_config = {}  # No 'field' specified
        manifest = {"pipeline": ["expand"]}

        count, _, _, _ = run_fan_out_step(
            run_dir, "chunk_000", "expand", step_config,
            {"pipeline": {"steps": [{"name": "expand", "scope": "fan_out"}]}},
            manifest, log_file
        )
        assert count == 0


class TestFanOutConfigValidator:
    """Test config validator accepts fan_out scope."""

    def test_fan_out_scope_is_valid(self):
        from config_validator import validate_config
        config = {
            "pipeline": {
                "steps": [
                    {"name": "generate", "prompt_template": "gen.jinja2"},
                    {"name": "expand", "scope": "fan_out", "field": "variants", "child_field": "variant"},
                    {"name": "evaluate", "prompt_template": "eval.jinja2"},
                ]
            },
            "prompts": {"templates": {"generate": "gen.jinja2", "evaluate": "eval.jinja2"}},
            "schemas": {"files": {"generate": "gen.json", "evaluate": "eval.json"}},
        }
        errors, warnings = validate_config(config)
        # fan_out scope should NOT produce a "invalid scope" error
        scope_errors = [e for e in errors if "invalid scope" in e.lower()]
        assert len(scope_errors) == 0

    def test_fan_out_in_chunk_scope_steps(self):
        """Fan-out steps should appear in chunk scope step list."""
        from config_validator import get_chunk_scope_steps
        config = {
            "pipeline": {
                "steps": [
                    {"name": "generate"},
                    {"name": "expand", "scope": "fan_out"},
                    {"name": "evaluate"},
                ]
            }
        }
        steps = get_chunk_scope_steps(config)
        assert "expand" in steps


class TestFanOutInReport:
    """Test fan-out boundary in pipeline report."""

    def test_report_shows_fan_out_boundary(self, tmp_path):
        from run_tools import generate_report

        run_dir = tmp_path / "run"
        chunks_dir = run_dir / "chunks" / "chunk_000"
        chunks_dir.mkdir(parents=True)

        # Write config with fan-out step
        write_config(run_dir, {
            "pipeline": {
                "steps": [
                    {"name": "generate"},
                    {"name": "expand", "scope": "fan_out", "field": "items", "child_field": "item"},
                    {"name": "evaluate"},
                ]
            }
        })

        # Parent step: 2 valid parents
        write_jsonl(chunks_dir / "units.jsonl", [
            {"unit_id": "p1", "items": ["a", "b"]},
            {"unit_id": "p2", "items": ["c"]},
        ])
        write_jsonl(chunks_dir / "generate_validated.jsonl", [
            {"unit_id": "p1", "items": ["a", "b"], "output": "x"},
            {"unit_id": "p2", "items": ["c"], "output": "y"},
        ])

        # Fan-out step: 3 children
        write_jsonl(chunks_dir / "expand_validated.jsonl", [
            {"unit_id": "p1__fan000", "item": "a", "_fan_parent_id": "p1"},
            {"unit_id": "p1__fan001", "item": "b", "_fan_parent_id": "p1"},
            {"unit_id": "p2__fan000", "item": "c", "_fan_parent_id": "p2"},
        ])

        # Evaluate step: 3 valid
        write_jsonl(chunks_dir / "evaluate_validated.jsonl", [
            {"unit_id": "p1__fan000", "result": "ok"},
            {"unit_id": "p1__fan001", "result": "ok"},
            {"unit_id": "p2__fan000", "result": "ok"},
        ])

        manifest = {
            "pipeline": ["generate", "expand", "evaluate"],
            "chunks": {"chunk_000": {"state": "VALIDATED", "items": 2, "valid": 3, "failed": 0, "retries": 0}},
            "status": "complete",
            "metadata": {"pipeline_name": "test", "mode": "realtime", "provider": "gemini", "model": "gemini-2.0-flash"},
        }
        write_manifest(run_dir, manifest)

        result = generate_report(run_dir)
        assert "error" not in result
        text = result["text"]
        assert "units created from" in text
        assert "2 parents" in text


class TestFanOutInVerify:
    """Test fan-out boundary in verify_run."""

    def test_verify_handles_fan_out(self, tmp_path):
        from run_tools import verify_run

        run_dir = tmp_path / "run"
        chunks_dir = run_dir / "chunks" / "chunk_000"
        chunks_dir.mkdir(parents=True)

        write_config(run_dir, {
            "pipeline": {
                "steps": [
                    {"name": "step1"},
                    {"name": "expand", "scope": "fan_out", "field": "items"},
                ]
            }
        })

        write_jsonl(chunks_dir / "units.jsonl", [
            {"unit_id": "p1", "items": ["a", "b"]},
        ])
        write_jsonl(chunks_dir / "step1_validated.jsonl", [
            {"unit_id": "p1", "items": ["a", "b"], "output": "ok"},
        ])
        write_jsonl(chunks_dir / "expand_validated.jsonl", [
            {"unit_id": "p1__fan000", "item": "a", "_fan_parent_id": "p1"},
            {"unit_id": "p1__fan001", "item": "b", "_fan_parent_id": "p1"},
        ])

        manifest = {
            "pipeline": ["step1", "expand"],
            "chunks": {"chunk_000": {"state": "VALIDATED", "items": 1, "valid": 2, "failed": 0, "retries": 0}},
            "status": "complete",
            "metadata": {"pipeline_name": "test"},
        }
        write_manifest(run_dir, manifest)

        result = verify_run(run_dir)
        assert "error" not in result

        # Find the fan-out step report
        expand_step = [s for s in result["steps"] if s["step"] == "expand"][0]
        assert expand_step["scope"] == "fan_out"
        assert expand_step["valid"] == 2
        assert expand_step["expected"] == 1  # 1 parent


# =============================================================================
# 4B: Re-validate failures
# =============================================================================

class TestRevalidateExists:
    """Test that --revalidate infrastructure exists."""

    def test_revalidate_function_exists(self):
        from orchestrate import revalidate_failures
        assert callable(revalidate_failures)

    def test_revalidate_argparse(self):
        """--revalidate flag should exist in argparse."""
        import orchestrate
        # Check that the source code contains the --revalidate flag
        source_path = Path(orchestrate.__file__)
        source = source_path.read_text()
        assert "--revalidate" in source


# =============================================================================
# 4C: Stochastic testing
# =============================================================================

class TestStochasticRunner:
    """Test the stochastic runner infrastructure."""

    def test_parse_scenarios(self):
        """Runner correctly parses QUALITY.md scenarios."""
        sys.path.insert(0, str(Path(__file__).parent))
        from stochastic_runner import parse_scenarios

        quality_md = Path(__file__).parent.parent / "ai_context" / "QUALITY.md"
        if not quality_md.exists():
            pytest.skip("QUALITY.md not found")

        scenarios = parse_scenarios(quality_md)
        assert len(scenarios) == 10

        # Check first scenario
        assert scenarios[0]["number"] == 1
        assert "Crash Recovery" in scenarios[0]["title"]

    def test_runner_handles_pass(self):
        """Runner handles passing scenarios."""
        sys.path.insert(0, str(Path(__file__).parent))
        from stochastic_runner import run_scenario

        scenario = {"number": 999, "title": "Test", "requirement": "", "verify": ""}

        # No test registered for 999 — should be skipped
        result = run_scenario(scenario, 3)
        assert result.get("skipped") is True

    def test_runner_handles_registered_test(self):
        """Runner runs registered tests."""
        sys.path.insert(0, str(Path(__file__).parent))
        from stochastic_runner import run_scenario, SCENARIO_TESTS

        # Use scenario 7 (seeded randomness) which is always available
        scenario = {"number": 7, "title": "Statistical Correctness", "requirement": "", "verify": ""}
        result = run_scenario(scenario, 3)
        assert result["passed"] == 3
        assert result["failed"] == 0

    def test_generate_regression_test_valid_pytest(self, tmp_path):
        """Generated regression tests are valid Python."""
        sys.path.insert(0, str(Path(__file__).parent))
        from stochastic_runner import generate_regression_test

        scenario = {"number": 1, "title": "Test Scenario", "requirement": "must work"}
        code = generate_regression_test(scenario, "something broke")

        # Write and verify it's valid Python
        test_file = tmp_path / "test_gen.py"
        test_file.write_text(code)

        import py_compile
        py_compile.compile(str(test_file), doraise=True)
