"""
Tests for Work Package 2: Analysis Tooling
- 2A: Cross-run comparison (compare_runs)
- 2B: Failure breakdown by field (--failures-by)
- 2C: Hand-by-hand diff across runs (compare_hands)
"""

import json
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

from run_tools import compare_runs, compare_hands, generate_report, _resolve_run_dir


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


def make_unit(unit_id: str, **extra) -> dict:
    record = {"unit_id": unit_id}
    record.update(extra)
    return record


def build_run(run_dir: Path, provider="gemini", model="gemini-2.0-flash-001",
              mode="realtime", units=None, step1_valid=None, step1_failed=None,
              pipeline=None, display_name=None):
    """Build a synthetic run directory for testing."""
    if pipeline is None:
        pipeline = ["step1"]
    if units is None:
        units = [make_unit(f"u{i}") for i in range(5)]
    if step1_valid is None:
        step1_valid = units

    manifest = {
        "pipeline": pipeline,
        "chunks": {
            "chunk_000": {
                "state": "VALIDATED",
                "items": len(units),
                "valid": len(step1_valid),
                "failed": len(step1_failed) if step1_failed else 0,
                "retries": 0,
                "input_tokens": 100,
                "output_tokens": 200,
            }
        },
        "metadata": {
            "pipeline_name": "test",
            "provider": provider,
            "model": model,
            "mode": mode,
            "start_time": "2024-01-01T00:00:00Z",
            "initial_input_tokens": 1000,
            "initial_output_tokens": 500,
            "retry_input_tokens": 0,
            "retry_output_tokens": 0,
        },
        "status": "complete",
        "created": "2024-01-01T00:00:00Z",
        "updated": "2024-01-01T00:00:00Z",
    }
    if display_name:
        manifest["metadata"]["display_name"] = display_name

    write_manifest(run_dir, manifest)
    chunk_dir = run_dir / "chunks" / "chunk_000"
    write_jsonl(chunk_dir / "units.jsonl", units)
    write_jsonl(chunk_dir / "step1_validated.jsonl", step1_valid)
    if step1_failed:
        write_jsonl(chunk_dir / "step1_failures.jsonl", step1_failed)
    return manifest


# =============================================================================
# 2A: Cross-run comparison tests
# =============================================================================

class TestCompareRuns:
    """Tests for compare_runs()."""

    def test_comparison_with_two_runs(self, tmp_path, monkeypatch):
        """compare_runs produces correct comparison for 2 runs."""
        monkeypatch.chdir(tmp_path)

        run1 = tmp_path / "runs" / "run_a"
        run2 = tmp_path / "runs" / "run_b"
        build_run(run1, provider="gemini", model="gemini-2.0-flash-001",
                  display_name="Gemini Run")
        build_run(run2, provider="openai", model="gpt-4o-mini",
                  display_name="OpenAI Run")

        result = compare_runs([str(run1), str(run2)])
        assert "error" not in result
        assert "text" in result
        assert len(result["runs"]) == 2

        text = result["text"]
        assert "Cross-Run Comparison" in text
        assert "Gemini Run" in text
        assert "OpenAI Run" in text
        assert "Provider/Model" in text
        assert "Pass Rate" in text
        assert "Cost" in text

    def test_comparison_with_three_runs(self, tmp_path, monkeypatch):
        """compare_runs handles 3+ runs correctly."""
        monkeypatch.chdir(tmp_path)

        runs = []
        for name, prov, model in [
            ("run_a", "gemini", "gemini-2.0-flash-001"),
            ("run_b", "openai", "gpt-4o-mini"),
            ("run_c", "anthropic", "claude-sonnet-4-20250514"),
        ]:
            rd = tmp_path / "runs" / name
            build_run(rd, provider=prov, model=model)
            runs.append(str(rd))

        result = compare_runs(runs)
        assert len(result["runs"]) == 3
        assert "error" not in result

    def test_comparison_different_step_counts(self, tmp_path, monkeypatch):
        """compare_runs handles runs with different pipeline lengths."""
        monkeypatch.chdir(tmp_path)

        # Run 1: single step
        run1 = tmp_path / "runs" / "run_1step"
        build_run(run1, pipeline=["step1"])

        # Run 2: two steps
        run2 = tmp_path / "runs" / "run_2step"
        units = [make_unit(f"u{i}") for i in range(5)]
        manifest = {
            "pipeline": ["step1", "step2"],
            "chunks": {
                "chunk_000": {
                    "state": "VALIDATED",
                    "items": 5,
                    "valid": 4,
                    "failed": 1,
                    "retries": 0,
                    "input_tokens": 100,
                    "output_tokens": 200,
                }
            },
            "metadata": {
                "pipeline_name": "test",
                "provider": "openai",
                "model": "gpt-4o-mini",
                "mode": "realtime",
                "initial_input_tokens": 1000,
                "initial_output_tokens": 500,
                "retry_input_tokens": 0,
                "retry_output_tokens": 0,
            },
            "status": "complete",
            "created": "2024-01-01T00:00:00Z",
            "updated": "2024-01-01T00:00:00Z",
        }
        write_manifest(run2, manifest)
        chunk_dir = run2 / "chunks" / "chunk_000"
        write_jsonl(chunk_dir / "units.jsonl", units)
        write_jsonl(chunk_dir / "step1_validated.jsonl", units)
        write_jsonl(chunk_dir / "step2_validated.jsonl",
                    [make_unit(f"u{i}") for i in range(4)])
        write_jsonl(chunk_dir / "step2_failures.jsonl",
                    [make_unit("u4", error="fail")])

        result = compare_runs([str(run1), str(run2)])
        assert "error" not in result
        # Run 1: 5/5 = 100%, Run 2: 4/5 = 80%
        assert result["runs"][0]["pass_rate"] == 100.0
        assert result["runs"][1]["pass_rate"] == 80.0

    def test_comparison_saves_markdown(self, tmp_path, monkeypatch):
        """compare_runs saves a markdown file."""
        monkeypatch.chdir(tmp_path)

        run1 = tmp_path / "runs" / "run_a"
        run2 = tmp_path / "runs" / "run_b"
        build_run(run1)
        build_run(run2)

        result = compare_runs([str(run1), str(run2)])
        md_path = result.get("markdown_path")
        assert md_path is not None
        assert Path(md_path).exists()
        content = Path(md_path).read_text()
        assert "Cross-Run Comparison" in content

    def test_comparison_fewer_than_two_runs_errors(self, tmp_path):
        """compare_runs returns error with fewer than 2 runs."""
        result = compare_runs(["single_run"])
        assert "error" in result

    def test_comparison_nonexistent_run_errors(self, tmp_path, monkeypatch):
        """compare_runs returns error for non-existent run directories."""
        monkeypatch.chdir(tmp_path)
        result = compare_runs(["/nonexistent/run1", "/nonexistent/run2"])
        assert "error" in result

    def test_comparison_with_strategy_data(self, tmp_path, monkeypatch):
        """compare_runs includes strategy comparison data when available."""
        monkeypatch.chdir(tmp_path)

        run1 = tmp_path / "runs" / "run_a"
        run2 = tmp_path / "runs" / "run_b"
        build_run(run1, display_name="Run A")
        build_run(run2, display_name="Run B")

        # Add strategy_comparison.txt to run1
        strat_text = """
Blackjack Strategy Comparison
=============================

Group       |  Total | dealer_wins | player_wins |     push |      Net
----------------------------------------------------------------------
The Pro     |      4 |       50.0% |       25.0% |    25.0% |       -1
The Gambler |      4 |       75.0% |        0.0% |    25.0% |       -3
----------------------------------------------------------------------
Total: 8 results
"""
        (run1 / "strategy_comparison.txt").write_text(strat_text)

        result = compare_runs([str(run1), str(run2)])
        text = result["text"]
        # Should contain strategy data from run1
        assert "The Pro Net" in text or "The Pro" in text


# =============================================================================
# 2B: Failure breakdown by field tests
# =============================================================================

class TestFailuresByField:
    """Tests for --failures-by parameter in generate_report()."""

    def test_failures_by_custom_field(self, tmp_path):
        """generate_report groups failures by a custom field when specified."""
        run_dir = tmp_path / "field_run"
        run_dir.mkdir()

        units = [
            make_unit("pro__rep0000", strategy_name="The Pro"),
            make_unit("pro__rep0001", strategy_name="The Pro"),
            make_unit("gambler__rep0000", strategy_name="The Gambler"),
            make_unit("gambler__rep0001", strategy_name="The Gambler"),
            make_unit("coward__rep0000", strategy_name="The Coward"),
        ]

        valid = [units[0], units[2], units[4]]
        failed = [
            make_unit("pro__rep0001", strategy_name="The Pro", error="bad"),
            make_unit("gambler__rep0001", strategy_name="The Gambler", error="bad"),
        ]

        manifest = {
            "pipeline": ["step1"],
            "chunks": {
                "chunk_000": {
                    "state": "VALIDATED",
                    "items": 5,
                    "valid": 3,
                    "failed": 2,
                    "retries": 0,
                    "input_tokens": 0,
                    "output_tokens": 0,
                }
            },
            "metadata": {
                "pipeline_name": "test",
                "provider": "gemini",
                "model": "gemini-2.0-flash-001",
                "initial_input_tokens": 100,
                "initial_output_tokens": 50,
                "retry_input_tokens": 0,
                "retry_output_tokens": 0,
            },
            "status": "complete",
        }
        write_manifest(run_dir, manifest)
        chunk_dir = run_dir / "chunks" / "chunk_000"
        write_jsonl(chunk_dir / "units.jsonl", units)
        write_jsonl(chunk_dir / "step1_validated.jsonl", valid)
        write_jsonl(chunk_dir / "step1_failures.jsonl", failed)

        result = generate_report(run_dir, failures_by="strategy_name")
        text = result["text"]
        assert "FAILURES BY STRATEGY_NAME" in text
        assert "The Pro" in text
        assert "The Gambler" in text

    def test_failures_by_missing_field_shows_missing(self, tmp_path):
        """generate_report shows (missing) when field not in failure records."""
        run_dir = tmp_path / "missing_field"
        run_dir.mkdir()

        units = [make_unit("u0"), make_unit("u1")]
        failed = [make_unit("u1", error="bad")]

        manifest = {
            "pipeline": ["step1"],
            "chunks": {
                "chunk_000": {
                    "state": "VALIDATED",
                    "items": 2,
                    "valid": 1,
                    "failed": 1,
                    "retries": 0,
                    "input_tokens": 0,
                    "output_tokens": 0,
                }
            },
            "metadata": {
                "pipeline_name": "test",
                "provider": "gemini",
                "model": "gemini-2.0-flash-001",
                "initial_input_tokens": 0,
                "initial_output_tokens": 0,
                "retry_input_tokens": 0,
                "retry_output_tokens": 0,
            },
            "status": "complete",
        }
        write_manifest(run_dir, manifest)
        chunk_dir = run_dir / "chunks" / "chunk_000"
        write_jsonl(chunk_dir / "units.jsonl", units)
        write_jsonl(chunk_dir / "step1_validated.jsonl", [units[0]])
        write_jsonl(chunk_dir / "step1_failures.jsonl", failed)

        result = generate_report(run_dir, failures_by="nonexistent_field")
        text = result["text"]
        assert "FAILURES BY NONEXISTENT_FIELD" in text
        assert "(missing)" in text

    def test_failures_by_upstream_field(self, tmp_path):
        """generate_report groups by upstream field present in failure records."""
        run_dir = tmp_path / "upstream_run"
        run_dir.mkdir()

        # Simulate units with upstream fields carried forward
        units = [
            make_unit("u0", difficulty="easy"),
            make_unit("u1", difficulty="hard"),
            make_unit("u2", difficulty="hard"),
        ]
        valid = [units[0]]
        failed = [
            make_unit("u1", difficulty="hard", error="bad"),
            make_unit("u2", difficulty="hard", error="bad"),
        ]

        manifest = {
            "pipeline": ["step1"],
            "chunks": {
                "chunk_000": {
                    "state": "VALIDATED",
                    "items": 3,
                    "valid": 1,
                    "failed": 2,
                    "retries": 0,
                    "input_tokens": 0,
                    "output_tokens": 0,
                }
            },
            "metadata": {
                "pipeline_name": "test",
                "provider": "gemini",
                "model": "gemini-2.0-flash-001",
                "initial_input_tokens": 0,
                "initial_output_tokens": 0,
                "retry_input_tokens": 0,
                "retry_output_tokens": 0,
            },
            "status": "complete",
        }
        write_manifest(run_dir, manifest)
        chunk_dir = run_dir / "chunks" / "chunk_000"
        write_jsonl(chunk_dir / "units.jsonl", units)
        write_jsonl(chunk_dir / "step1_validated.jsonl", valid)
        write_jsonl(chunk_dir / "step1_failures.jsonl", failed)

        result = generate_report(run_dir, failures_by="difficulty")
        text = result["text"]
        assert "FAILURES BY DIFFICULTY" in text
        assert "hard" in text

    def test_default_failures_by_item(self, tmp_path):
        """generate_report defaults to failures by item when no field specified."""
        run_dir = tmp_path / "default_run"
        run_dir.mkdir()

        units = [
            make_unit("item_a__rep0000"),
            make_unit("item_b__rep0000"),
        ]
        failed = [make_unit("item_a__rep0000", error="bad")]

        manifest = {
            "pipeline": ["step1"],
            "chunks": {
                "chunk_000": {
                    "state": "VALIDATED",
                    "items": 2,
                    "valid": 1,
                    "failed": 1,
                    "retries": 0,
                    "input_tokens": 0,
                    "output_tokens": 0,
                }
            },
            "metadata": {
                "pipeline_name": "test",
                "provider": "gemini",
                "model": "gemini-2.0-flash-001",
                "initial_input_tokens": 0,
                "initial_output_tokens": 0,
                "retry_input_tokens": 0,
                "retry_output_tokens": 0,
            },
            "status": "complete",
        }
        write_manifest(run_dir, manifest)
        chunk_dir = run_dir / "chunks" / "chunk_000"
        write_jsonl(chunk_dir / "units.jsonl", units)
        write_jsonl(chunk_dir / "step1_validated.jsonl", [units[1]])
        write_jsonl(chunk_dir / "step1_failures.jsonl", failed)

        result = generate_report(run_dir)
        text = result["text"]
        assert "FAILURES BY ITEM" in text
        assert "item_a" in text


# =============================================================================
# 2C: Hand-by-hand diff tests
# =============================================================================

class TestCompareHands:
    """Tests for compare_hands()."""

    def test_matching_units_by_id(self, tmp_path):
        """compare_hands matches units across runs by unit_id."""
        run1 = tmp_path / "run1"
        run2 = tmp_path / "run2"

        units = [
            make_unit("u0", result="player_wins", player_final_total=20),
            make_unit("u1", result="dealer_wins", player_final_total=18),
        ]
        build_run(run1, step1_valid=units)
        build_run(run2, step1_valid=units)  # Same outcomes

        result = compare_hands(run1, run2, step="step1")
        assert "error" not in result
        assert result["common_count"] == 2
        assert result["identical_count"] == 2
        assert result["divergent_count"] == 0

    def test_detecting_divergences(self, tmp_path):
        """compare_hands detects divergences in results."""
        run1 = tmp_path / "run1"
        run2 = tmp_path / "run2"

        units1 = [
            make_unit("u0", result="player_wins", player_final_total=20),
            make_unit("u1", result="dealer_wins", player_final_total=18),
        ]
        units2 = [
            make_unit("u0", result="dealer_wins", player_final_total=15),  # Different!
            make_unit("u1", result="dealer_wins", player_final_total=18),  # Same
        ]
        build_run(run1, step1_valid=units1)
        build_run(run2, step1_valid=units2)

        result = compare_hands(run1, run2, step="step1")
        assert result["divergent_count"] == 1
        assert result["identical_count"] == 1
        assert result["divergent"][0]["unit_id"] == "u0"
        assert "result" in result["divergent"][0]["diffs"]

    def test_handling_different_valid_sets(self, tmp_path):
        """compare_hands handles runs where some units failed in one but not the other."""
        run1 = tmp_path / "run1"
        run2 = tmp_path / "run2"

        units1 = [make_unit("u0"), make_unit("u1"), make_unit("u2")]
        units2 = [make_unit("u0"), make_unit("u1")]  # u2 missing (failed in run2)

        build_run(run1, step1_valid=units1)
        build_run(run2, units=[make_unit(f"u{i}") for i in range(3)],
                  step1_valid=units2)

        result = compare_hands(run1, run2, step="step1")
        assert result["common_count"] == 2
        assert len(result["only_in_run1"]) == 1
        assert "u2" in result["only_in_run1"]

    def test_single_unit_detail(self, tmp_path):
        """compare_hands shows detailed diff for a single unit_id."""
        run1 = tmp_path / "run1"
        run2 = tmp_path / "run2"

        units1 = [make_unit("u0", result="player_wins", player_final_total=20,
                            first_action="hit")]
        units2 = [make_unit("u0", result="dealer_wins", player_final_total=22,
                            first_action="stand")]

        build_run(run1, step1_valid=units1)
        build_run(run2, step1_valid=units2)

        result = compare_hands(run1, run2, unit_id="u0", step="step1")
        assert "error" not in result
        text = result["text"]
        assert "Detailed Comparison" in text
        assert "u0" in text
        assert "player_wins" in text or "Run 1" in text

    def test_sample_parameter(self, tmp_path):
        """compare_hands --sample N limits divergent output."""
        run1 = tmp_path / "run1"
        run2 = tmp_path / "run2"

        # Create 10 divergent units
        units1 = [make_unit(f"u{i}", result="player_wins") for i in range(10)]
        units2 = [make_unit(f"u{i}", result="dealer_wins") for i in range(10)]

        build_run(run1, units=[make_unit(f"u{i}") for i in range(10)],
                  step1_valid=units1)
        build_run(run2, units=[make_unit(f"u{i}") for i in range(10)],
                  step1_valid=units2)

        result = compare_hands(run1, run2, sample=3, step="step1")
        # Text should only show 3 divergent pairs
        text = result["text"]
        assert result["divergent_count"] == 10
        # Count how many "u" entries appear in the DIVERGENT HANDS section
        divergent_section = text.split("DIVERGENT HANDS")[1] if "DIVERGENT HANDS" in text else ""
        uid_lines = [l for l in divergent_section.split("\n") if l.strip().startswith("u")]
        assert len(uid_lines) == 3

    def test_nonexistent_run_errors(self, tmp_path):
        """compare_hands returns error for non-existent run directories."""
        result = compare_hands(tmp_path / "no1", tmp_path / "no2")
        assert "error" in result

    def test_no_common_units(self, tmp_path):
        """compare_hands returns error when no common unit IDs found."""
        run1 = tmp_path / "run1"
        run2 = tmp_path / "run2"

        build_run(run1, step1_valid=[make_unit("a")])
        build_run(run2, units=[make_unit("b")], step1_valid=[make_unit("b")])

        result = compare_hands(run1, run2, step="step1")
        assert "error" in result
        assert "No common unit IDs" in result["error"]

    def test_unit_id_not_in_both_runs(self, tmp_path):
        """compare_hands returns error when specified unit_id not in both runs."""
        run1 = tmp_path / "run1"
        run2 = tmp_path / "run2"

        build_run(run1, step1_valid=[make_unit("u0")])
        build_run(run2, step1_valid=[make_unit("u0")])

        result = compare_hands(run1, run2, unit_id="u999", step="step1")
        assert "error" in result


# =============================================================================
# Run name resolution tests
# =============================================================================

class TestResolveRunDir:
    """Tests for _resolve_run_dir()."""

    def test_direct_path_exists(self, tmp_path, monkeypatch):
        """Direct path that exists is returned as-is."""
        monkeypatch.chdir(tmp_path)
        rd = tmp_path / "my_run"
        rd.mkdir()
        result = _resolve_run_dir(str(rd))
        assert result == rd

    def test_prepends_runs_prefix(self, tmp_path, monkeypatch):
        """Short name gets runs/ prepended."""
        monkeypatch.chdir(tmp_path)
        rd = tmp_path / "runs" / "my_run"
        rd.mkdir(parents=True)
        result = _resolve_run_dir("my_run")
        assert result == Path("runs/my_run")

    def test_prefix_match(self, tmp_path, monkeypatch):
        """Short name matches a unique prefix in runs/."""
        monkeypatch.chdir(tmp_path)
        rd = tmp_path / "runs" / "blackjack_gemini_20240101"
        rd.mkdir(parents=True)
        result = _resolve_run_dir("blackjack_gem")
        assert result.resolve() == rd.resolve()
