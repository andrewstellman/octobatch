"""
Pipeline discovery and loading utilities.

Scans the pipelines/ directory for valid pipeline configurations.
"""

from pathlib import Path
from typing import List, Dict, Any, Optional
import yaml


def get_pipelines_dir() -> Path:
    """
    Get the pipelines directory path.

    Returns the pipelines/ directory relative to the project root.
    Project root is determined by finding the parent of scripts/.
    """
    # This file is at scripts/tui/utils/pipelines.py
    # Project root is parent of scripts/
    project_root = Path(__file__).parent.parent.parent.parent
    return project_root / "pipelines"


def scan_pipelines() -> List[Dict[str, Any]]:
    """
    Scan pipelines/ directory for valid pipeline configurations.

    Returns list of dicts with:
    - name: folder name
    - path: Path to pipeline folder
    - config_path: Path to config.yaml
    - steps: list of pipeline step names
    - items_source: name of items file
    - step_count: number of pipeline steps (excluding run-scope)
    """
    pipelines_dir = get_pipelines_dir()
    pipelines = []

    if not pipelines_dir.exists():
        return pipelines

    for folder in sorted(pipelines_dir.iterdir()):
        if not folder.is_dir():
            continue

        config_path = folder / "config.yaml"
        if not config_path.exists():
            continue

        try:
            with open(config_path) as f:
                config = yaml.safe_load(f)

            # Extract pipeline steps
            pipeline_config = config.get("pipeline", {})
            steps = pipeline_config.get("steps", [])
            if isinstance(steps, list) and len(steps) > 0 and isinstance(steps[0], dict):
                # New format: list of step dicts
                step_names = [s.get("name", "") for s in steps if s.get("scope") != "run"]
            else:
                # Old format: list of step names
                step_names = [s for s in steps if isinstance(s, str)]

            # Get items source
            processing = config.get("processing", {})
            items_config = processing.get("items", {})
            if isinstance(items_config, dict):
                items_source = items_config.get("source", "")
            else:
                items_source = config.get("items_source", "")

            pipelines.append({
                "name": folder.name,
                "path": folder,
                "config_path": config_path,
                "steps": step_names,
                "items_source": items_source,
                "step_count": len(step_names),
            })
        except Exception:
            # Skip invalid configs
            continue

    return pipelines


def load_pipeline_config(pipeline_name: str) -> Dict[str, Any]:
    """
    Load full config for a named pipeline.

    Args:
        pipeline_name: Name of the pipeline folder

    Returns:
        Full configuration dict

    Raises:
        FileNotFoundError: If pipeline doesn't exist
    """
    pipelines_dir = get_pipelines_dir()
    config_path = pipelines_dir / pipeline_name / "config.yaml"

    if not config_path.exists():
        raise FileNotFoundError(f"Pipeline not found: {pipeline_name}")

    with open(config_path) as f:
        return yaml.safe_load(f)


def get_pipeline_path(pipeline_name: str) -> Path:
    """
    Get the path to a pipeline directory.

    Args:
        pipeline_name: Name of the pipeline folder

    Returns:
        Path to the pipeline directory

    Raises:
        FileNotFoundError: If pipeline doesn't exist
    """
    pipelines_dir = get_pipelines_dir()
    pipeline_path = pipelines_dir / pipeline_name

    if not pipeline_path.exists():
        raise FileNotFoundError(f"Pipeline not found: {pipeline_name}")

    return pipeline_path


def list_pipeline_names() -> List[str]:
    """
    Get list of available pipeline names.

    Returns:
        List of pipeline folder names
    """
    pipelines = scan_pipelines()
    return [p["name"] for p in pipelines]


# --- Pure utility functions (no file I/O) ---

def get_step_names(steps: List[Dict[str, Any]]) -> List[str]:
    """
    Extract step names from a list of step configurations.

    Args:
        steps: List of step dicts (must have 'name' key)

    Returns:
        List of step names, excluding run-scope steps
    """
    return [
        s.get("name", "")
        for s in steps
        if isinstance(s, dict) and s.get("scope") != "run"
    ]


def filter_chunks_for_step(
    chunks: List[Dict[str, Any]],
    step_index: int,
    pipeline: List[str]
) -> List[Dict[str, Any]]:
    """
    Filter chunks that are at or past a given step.

    Args:
        chunks: List of chunk dicts with 'state' key
        step_index: Index of the step to filter by
        pipeline: List of pipeline step names

    Returns:
        List of chunks at or past the given step
    """
    from .status import parse_chunk_state

    result = []
    for chunk in chunks:
        state = chunk.get("state", "PENDING")
        _, status, chunk_step_idx = parse_chunk_state(state, pipeline)

        # Include if chunk is at or past this step, or is complete
        if chunk_step_idx >= step_index or status == "complete":
            result.append(chunk)

    return result if result else chunks  # Return all if filtering yields nothing


def calculate_pipeline_progress(
    steps: List[Dict[str, Any]]
) -> tuple[int, int, float]:
    """
    Calculate overall pipeline progress from step data.

    Args:
        steps: List of step dicts with 'completed' and 'total' keys

    Returns:
        Tuple of (total_completed, total_items, progress_ratio)
    """
    total_completed = 0
    total_items = 0

    for step in steps:
        completed = step.get("completed", 0) or 0
        total = step.get("total", 0) or 0
        total_completed += completed
        total_items += total

    if total_items <= 0:
        return (0, 0, 0.0)

    ratio = min(total_completed / total_items, 1.0)
    return (total_completed, total_items, ratio)


def get_step_by_name(steps: List[Dict[str, Any]], name: str) -> Optional[Dict[str, Any]]:
    """
    Find a step by name.

    Args:
        steps: List of step dicts
        name: Step name to find

    Returns:
        Step dict or None if not found
    """
    for step in steps:
        if step.get("name") == name:
            return step
    return None


def get_step_by_index(steps: List[Dict[str, Any]], index: int) -> Optional[Dict[str, Any]]:
    """
    Get a step by index, safely handling out-of-bounds.

    Args:
        steps: List of step dicts
        index: Step index

    Returns:
        Step dict or None if index out of bounds
    """
    if 0 <= index < len(steps):
        return steps[index]
    return None
