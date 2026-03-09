"""Tests for stochastic scenario runner."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))


def test_parse_scenarios():
    from stochastic_runner import parse_scenarios
    quality_md = Path(__file__).parent.parent / "ai_context" / "QUALITY.md"
    if not quality_md.exists():
        pytest.skip("QUALITY.md not found")
    scenarios = parse_scenarios(quality_md)
    assert len(scenarios) == 10
    assert scenarios[0]["number"] == 1


def test_runner_handles_registered_test():
    from stochastic_runner import run_scenario
    scenario = {"number": 7, "title": "Statistical Correctness", "requirement": "", "verify": ""}
    result = run_scenario(scenario, 3)
    assert result["passed"] == 3
    assert result["failed"] == 0


def test_generate_regression_test_valid_pytest(tmp_path):
    from stochastic_runner import generate_regression_test

    scenario = {"number": 1, "title": "Test Scenario", "requirement": "must work"}
    code = generate_regression_test(scenario, "something broke")
    test_file = tmp_path / "test_gen.py"
    test_file.write_text(code)

    import py_compile
    py_compile.compile(str(test_file), doraise=True)
