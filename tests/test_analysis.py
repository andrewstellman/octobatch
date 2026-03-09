"""
Tests for analysis tooling:
- cross-run comparison
- failures-by-field reporting
- hand-by-hand diff
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

from run_tools import compare_runs, compare_hands, generate_report, _resolve_run_dir


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
    out = {"unit_id": unit_id}
    out.update(extra)
    return out


def build_run(run_dir: Path, pipeline=None, units=None, step1_valid=None,
              step1_failed=None, provider="gemini", model="gemini-2.0-flash-001",
              display_name=None):
    pipeline = pipeline or ["step1"]
    units = units or [make_unit(f"u{i}") for i in range(5)]
    step1_valid = step1_valid if step1_valid is not None else units
    step1_failed = step1_failed or []
    manifest = {
        "pipeline": pipeline,
        "chunks": {
            "chunk_000": {
                "state": "VALIDATED",
                "items": len(units),
                "valid": len(step1_valid),
                "failed": len(step1_failed),
                "retries": 0,
                "input_tokens": 0,
                "output_tokens": 0,
            }
        },
        "metadata": {
            "pipeline_name": "test",
            "provider": provider,
            "model": model,
            "mode": "realtime",
            "initial_input_tokens": 1000,
            "initial_output_tokens": 500,
            "retry_input_tokens": 0,
            "retry_output_tokens": 0,
        },
        "status": "complete",
    }
    if display_name:
        manifest["metadata"]["display_name"] = display_name
    write_manifest(run_dir, manifest)
    chunk_dir = run_dir / "chunks" / "chunk_000"
    write_jsonl(chunk_dir / "units.jsonl", units)
    write_jsonl(chunk_dir / "step1_validated.jsonl", step1_valid)
    if step1_failed:
        write_jsonl(chunk_dir / "step1_failures.jsonl", step1_failed)


def test_compare_runs_creates_markdown(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    run1 = tmp_path / "runs" / "run_a"
    run2 = tmp_path / "runs" / "run_b"
    build_run(run1)
    build_run(run2)
    result = compare_runs([str(run1), str(run2)])
    assert "error" not in result
    assert Path(result["markdown_path"]).exists()


def test_compare_runs_handles_different_step_counts(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    run1 = tmp_path / "runs" / "run1"
    run2 = tmp_path / "runs" / "run2"
    build_run(run1, pipeline=["step1"])
    build_run(run2, pipeline=["step1", "step2"])
    chunk2 = run2 / "chunks" / "chunk_000"
    write_jsonl(chunk2 / "step2_validated.jsonl", [make_unit("u0"), make_unit("u1")])
    result = compare_runs([str(run1), str(run2)])
    assert "error" not in result
    assert len(result["runs"]) == 2


def test_generate_report_failures_by_field(tmp_path):
    run_dir = tmp_path / "run"
    units = [make_unit("u0", strategy_name="A"), make_unit("u1", strategy_name="B")]
    build_run(run_dir, units=units, step1_valid=[units[0]], step1_failed=[make_unit("u1", strategy_name="B", error="bad")])
    result = generate_report(run_dir, failures_by="strategy_name")
    assert "FAILURES BY STRATEGY_NAME" in result["text"]


def test_compare_hands_matches_unit_ids(tmp_path):
    run1 = tmp_path / "run1"
    run2 = tmp_path / "run2"
    units = [make_unit("u0", result="player_wins"), make_unit("u1", result="dealer_wins")]
    build_run(run1, step1_valid=units)
    build_run(run2, step1_valid=units)
    result = compare_hands(run1, run2, step="step1")
    assert "error" not in result
    assert result["common_count"] == 2
    assert result["divergent_count"] == 0


def test_compare_hands_detects_divergence(tmp_path):
    run1 = tmp_path / "run1"
    run2 = tmp_path / "run2"
    build_run(run1, step1_valid=[make_unit("u0", result="player_wins")])
    build_run(run2, step1_valid=[make_unit("u0", result="dealer_wins")])
    result = compare_hands(run1, run2, step="step1")
    assert result["divergent_count"] == 1


def test_resolve_run_dir_prefix_match(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    target = tmp_path / "runs" / "blackjack_gemini_20240101"
    target.mkdir(parents=True)
    resolved = _resolve_run_dir("blackjack_gem")
    assert resolved.resolve() == target.resolve()


# --- Restored cross-run comparison tests ---


def test_comparison_with_two_runs(tmp_path, monkeypatch):
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


def test_comparison_with_three_runs(tmp_path, monkeypatch):
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


def test_comparison_fewer_than_two_runs_errors(tmp_path):
    result = compare_runs(["single_run"])
    assert "error" in result


def test_comparison_nonexistent_run_errors(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    result = compare_runs(["/nonexistent/run1", "/nonexistent/run2"])
    assert "error" in result


def test_comparison_with_strategy_data(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    run1 = tmp_path / "runs" / "run_a"
    run2 = tmp_path / "runs" / "run_b"
    build_run(run1, display_name="Run A")
    build_run(run2, display_name="Run B")
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
    assert "The Pro" in text


# --- Restored failures-by-field tests ---


def test_failures_by_missing_field_shows_missing(tmp_path):
    run_dir = tmp_path / "missing_field"
    units = [make_unit("u0"), make_unit("u1")]
    failed = [make_unit("u1", error="bad")]
    build_run(run_dir, units=units, step1_valid=[units[0]], step1_failed=failed)
    result = generate_report(run_dir, failures_by="nonexistent_field")
    text = result["text"]
    assert "FAILURES BY NONEXISTENT_FIELD" in text
    assert "(missing)" in text


def test_failures_by_upstream_field(tmp_path):
    run_dir = tmp_path / "upstream_run"
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
    build_run(run_dir, units=units, step1_valid=valid, step1_failed=failed)
    result = generate_report(run_dir, failures_by="difficulty")
    text = result["text"]
    assert "FAILURES BY DIFFICULTY" in text
    assert "hard" in text


def test_default_failures_by_item(tmp_path):
    run_dir = tmp_path / "default_run"
    units = [make_unit("item_a__rep0000"), make_unit("item_b__rep0000")]
    failed = [make_unit("item_a__rep0000", error="bad")]
    build_run(run_dir, units=units, step1_valid=[units[1]], step1_failed=failed)
    result = generate_report(run_dir)
    text = result["text"]
    assert "FAILURES BY ITEM" in text
    assert "item_a" in text


# --- Restored hand-by-hand diff tests ---


def test_compare_hands_handling_different_valid_sets(tmp_path):
    run1 = tmp_path / "run1"
    run2 = tmp_path / "run2"
    units1 = [make_unit("u0"), make_unit("u1"), make_unit("u2")]
    units2 = [make_unit("u0"), make_unit("u1")]
    build_run(run1, step1_valid=units1)
    build_run(run2, units=[make_unit(f"u{i}") for i in range(3)],
              step1_valid=units2)
    result = compare_hands(run1, run2, step="step1")
    assert result["common_count"] == 2
    assert len(result["only_in_run1"]) == 1
    assert "u2" in result["only_in_run1"]


def test_compare_hands_single_unit_detail(tmp_path):
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
    assert "u0" in text


def test_compare_hands_sample_parameter(tmp_path):
    run1 = tmp_path / "run1"
    run2 = tmp_path / "run2"
    units1 = [make_unit(f"u{i}", result="player_wins") for i in range(10)]
    units2 = [make_unit(f"u{i}", result="dealer_wins") for i in range(10)]
    build_run(run1, units=[make_unit(f"u{i}") for i in range(10)],
              step1_valid=units1)
    build_run(run2, units=[make_unit(f"u{i}") for i in range(10)],
              step1_valid=units2)
    result = compare_hands(run1, run2, sample=3, step="step1")
    assert result["divergent_count"] == 10
    text = result["text"]
    divergent_section = text.split("DIVERGENT HANDS")[1] if "DIVERGENT HANDS" in text else ""
    uid_lines = [l for l in divergent_section.split("\n") if l.strip().startswith("u")]
    assert len(uid_lines) == 3


def test_compare_hands_nonexistent_run_errors(tmp_path):
    result = compare_hands(tmp_path / "no1", tmp_path / "no2")
    assert "error" in result


def test_compare_hands_no_common_units(tmp_path):
    run1 = tmp_path / "run1"
    run2 = tmp_path / "run2"
    build_run(run1, step1_valid=[make_unit("a")])
    build_run(run2, units=[make_unit("b")], step1_valid=[make_unit("b")])
    result = compare_hands(run1, run2, step="step1")
    assert "error" in result
    assert "No common unit IDs" in result["error"]


def test_compare_hands_unit_id_not_in_both_runs(tmp_path):
    run1 = tmp_path / "run1"
    run2 = tmp_path / "run2"
    build_run(run1, step1_valid=[make_unit("u0")])
    build_run(run2, step1_valid=[make_unit("u0")])
    result = compare_hands(run1, run2, unit_id="u999", step="step1")
    assert "error" in result


# --- Restored run dir resolution tests ---


def test_resolve_run_dir_direct_path(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    rd = tmp_path / "my_run"
    rd.mkdir()
    result = _resolve_run_dir(str(rd))
    assert result == rd


def test_resolve_run_dir_prepends_runs_prefix(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    rd = tmp_path / "runs" / "my_run"
    rd.mkdir(parents=True)
    result = _resolve_run_dir("my_run")
    assert result == Path("runs/my_run")
