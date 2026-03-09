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


def build_run(run_dir: Path, pipeline=None, units=None, step1_valid=None, step1_failed=None):
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
            "provider": "gemini",
            "model": "gemini-2.0-flash-001",
            "mode": "realtime",
            "initial_input_tokens": 1000,
            "initial_output_tokens": 500,
            "retry_input_tokens": 0,
            "retry_output_tokens": 0,
        },
        "status": "complete",
    }
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
