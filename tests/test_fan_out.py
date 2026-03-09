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
