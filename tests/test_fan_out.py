"""Tests for fan-out framework behavior."""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        for row in rows:
            f.write(json.dumps(row) + "\n")


def test_is_fan_out_step_detection():
    from orchestrate import is_fan_out_step
    cfg = {"pipeline": {"steps": [{"name": "expand", "scope": "fan_out"}]}}
    assert is_fan_out_step(cfg, "expand") is True
    assert is_fan_out_step(cfg, "missing") is False


def test_fan_out_child_id_and_fields(tmp_path):
    from orchestrate import run_fan_out_step

    run_dir = tmp_path / "run"
    chunk_dir = run_dir / "chunks" / "chunk_000"
    chunk_dir.mkdir(parents=True)
    (run_dir / "RUN_LOG.txt").write_text("")

    parents = [{"unit_id": "p1", "name": "A", "items": ["x", "y"]}]
    write_jsonl(chunk_dir / "step1_validated.jsonl", parents)

    manifest = {"pipeline": ["step1", "expand", "evaluate"], "chunks": {"chunk_000": {"state": "expand_PENDING"}}}
    config = {"pipeline": {"steps": [{"name": "step1"}, {"name": "expand", "scope": "fan_out"}, {"name": "evaluate"}]}, "processing": {"chunk_size": 10}}
    step_cfg = {"field": "items", "child_field": "item"}

    child_count, new_chunk_count, _, _ = run_fan_out_step(run_dir, "chunk_000", "expand", step_cfg, config, manifest, run_dir / "RUN_LOG.txt")
    assert child_count == 2
    assert new_chunk_count == 1

    new_chunk = run_dir / "chunks" / "chunk_001"
    out_file = new_chunk / "expand_validated.jsonl"
    assert out_file.exists()
    rows = [json.loads(line) for line in out_file.read_text().strip().splitlines()]
    assert rows[0]["unit_id"] == "p1__fan000"
    assert rows[1]["unit_id"] == "p1__fan001"
    assert rows[0]["item"] == "x"
    assert rows[0]["_fan_parent_id"] == "p1"
    assert rows[0]["_fan_index"] == 0


def test_fan_out_respects_chunk_size(tmp_path):
    from orchestrate import run_fan_out_step

    run_dir = tmp_path / "run"
    chunk_dir = run_dir / "chunks" / "chunk_000"
    chunk_dir.mkdir(parents=True)
    (run_dir / "RUN_LOG.txt").write_text("")
    write_jsonl(chunk_dir / "prev_validated.jsonl", [{"unit_id": "p", "items": [1, 2, 3, 4, 5]}])

    manifest = {"pipeline": ["prev", "expand", "evaluate"], "chunks": {"chunk_000": {"state": "expand_PENDING"}}}
    config = {"pipeline": {"steps": [{"name": "prev"}, {"name": "expand", "scope": "fan_out"}, {"name": "evaluate"}]}, "processing": {"chunk_size": 2}}
    step_cfg = {"field": "items", "child_field": "item"}

    child_count, new_chunk_count, _, _ = run_fan_out_step(run_dir, "chunk_000", "expand", step_cfg, config, manifest, run_dir / "RUN_LOG.txt")
    assert child_count == 5
    assert new_chunk_count == 3
    assert (run_dir / "chunks" / "chunk_001").exists()
    assert (run_dir / "chunks" / "chunk_002").exists()
    assert (run_dir / "chunks" / "chunk_003").exists()


def test_fan_out_empty_array_creates_no_children(tmp_path):
    from orchestrate import run_fan_out_step

    run_dir = tmp_path / "run"
    chunk_dir = run_dir / "chunks" / "chunk_000"
    chunk_dir.mkdir(parents=True)
    (run_dir / "RUN_LOG.txt").write_text("")
    write_jsonl(chunk_dir / "prev_validated.jsonl", [{"unit_id": "p", "items": []}])

    manifest = {"pipeline": ["prev", "expand"], "chunks": {"chunk_000": {"state": "expand_PENDING"}}}
    config = {"pipeline": {"steps": [{"name": "prev"}, {"name": "expand", "scope": "fan_out"}]}, "processing": {"chunk_size": 2}}
    step_cfg = {"field": "items", "child_field": "item"}
    child_count, new_chunk_count, _, _ = run_fan_out_step(run_dir, "chunk_000", "expand", step_cfg, config, manifest, run_dir / "RUN_LOG.txt")
    assert child_count == 0
    assert new_chunk_count == 0


# --- Restored fan-out helper function tests ---


def test_get_fan_out_step_config():
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


def test_get_fan_out_step_config_missing():
    from orchestrate import get_fan_out_step_config
    config = {
        "pipeline": {"steps": [{"name": "generate", "scope": "chunk"}]}
    }
    assert get_fan_out_step_config(config, "generate") is None


def test_fan_out_missing_field(tmp_path):
    from orchestrate import run_fan_out_step

    run_dir = tmp_path / "run"
    chunk_dir = run_dir / "chunks" / "chunk_000"
    chunk_dir.mkdir(parents=True)
    log_file = run_dir / "RUN_LOG.txt"
    log_file.touch()

    step_config = {}  # No 'field' specified
    manifest = {"pipeline": ["expand"], "chunks": {"chunk_000": {"state": "expand_PENDING"}}}
    config = {"pipeline": {"steps": [{"name": "expand", "scope": "fan_out"}]}, "processing": {"chunk_size": 10}}

    child_count, _, _, _ = run_fan_out_step(
        run_dir, "chunk_000", "expand", step_config, config, manifest, log_file
    )
    assert child_count == 0


# --- Restored config validator tests ---


def test_fan_out_scope_is_valid():
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
    scope_errors = [e for e in errors if "invalid scope" in e.lower()]
    assert len(scope_errors) == 0


def test_fan_out_in_chunk_scope_steps():
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


# --- Restored report/verify fan-out boundary tests ---


def _write_config(run_dir: Path, config: dict) -> Path:
    import yaml
    config_dir = run_dir / "config"
    config_dir.mkdir(parents=True, exist_ok=True)
    config_path = config_dir / "config.yaml"
    config_path.write_text(yaml.dump(config))
    return config_path


def _write_manifest(run_dir: Path, manifest: dict) -> Path:
    manifest_path = run_dir / "MANIFEST.json"
    run_dir.mkdir(parents=True, exist_ok=True)
    with open(manifest_path, "w") as f:
        json.dump(manifest, f, indent=2)
    return manifest_path


def test_report_shows_fan_out_boundary(tmp_path):
    from run_tools import generate_report

    run_dir = tmp_path / "run"
    chunks_dir = run_dir / "chunks" / "chunk_000"
    chunks_dir.mkdir(parents=True)

    _write_config(run_dir, {
        "pipeline": {
            "steps": [
                {"name": "generate"},
                {"name": "expand", "scope": "fan_out", "field": "items", "child_field": "item"},
                {"name": "evaluate"},
            ]
        }
    })

    write_jsonl(chunks_dir / "units.jsonl", [
        {"unit_id": "p1", "items": ["a", "b"]},
        {"unit_id": "p2", "items": ["c"]},
    ])
    write_jsonl(chunks_dir / "generate_validated.jsonl", [
        {"unit_id": "p1", "items": ["a", "b"], "output": "x"},
        {"unit_id": "p2", "items": ["c"], "output": "y"},
    ])
    write_jsonl(chunks_dir / "expand_validated.jsonl", [
        {"unit_id": "p1__fan000", "item": "a", "_fan_parent_id": "p1"},
        {"unit_id": "p1__fan001", "item": "b", "_fan_parent_id": "p1"},
        {"unit_id": "p2__fan000", "item": "c", "_fan_parent_id": "p2"},
    ])
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
    _write_manifest(run_dir, manifest)

    result = generate_report(run_dir)
    assert "error" not in result
    text = result["text"]
    assert "units created from" in text
    assert "2 parents" in text


def test_verify_handles_fan_out(tmp_path):
    from run_tools import verify_run

    run_dir = tmp_path / "run"
    chunks_dir = run_dir / "chunks" / "chunk_000"
    chunks_dir.mkdir(parents=True)

    _write_config(run_dir, {
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
    _write_manifest(run_dir, manifest)

    result = verify_run(run_dir)
    assert "error" not in result
    expand_step = [s for s in result["steps"] if s["step"] == "expand"][0]
    assert expand_step["scope"] == "fan_out"
    assert expand_step["valid"] == 2
    assert expand_step["expected"] == 1


# --- Restored revalidate existence tests ---


def test_revalidate_function_exists():
    from orchestrate import revalidate_failures
    assert callable(revalidate_failures)


def test_revalidate_argparse():
    import orchestrate
    source_path = Path(orchestrate.__file__)
    source = source_path.read_text()
    assert "--revalidate" in source
