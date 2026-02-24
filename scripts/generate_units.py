#!/usr/bin/env python3
"""
generate_units.py - Generic unit generator for batch processing pipelines.

Supports three combination strategies:
  - permutation (default): All permutations without replacement (N items → N×(N-1)×... units)
  - cross_product: Cartesian product of items from different groups
  - direct: Each item becomes one unit (no combination)

Also supports repetition for Monte Carlo simulations:
  - repeat: N duplicates each unit N times with unique seeds

Usage:
    # Output all units to single file
    python generate_units.py --config config/example_config.yaml --output units.jsonl

    # Output chunked (20 units per chunk)
    python generate_units.py --config config/example_config.yaml --chunk-size 20 --output-dir runs/run_001/chunks/

    # Limit units for testing (first N units only)
    python generate_units.py --config config/example_config.yaml --max-units 3 --output units.jsonl

    # Output to stdout
    python generate_units.py --config config/example_config.yaml

Config structure for permutation strategy (default):
    processing:
      strategy: permutation  # optional, this is the default
      positions:
        - name: past_card
        - name: present_card
        - name: future_card
      items:
        source: "cards.yaml"
        key: "cards"
        name_field: "name"

Config structure for cross_product strategy:
    processing:
      strategy: cross_product
      positions:
        - name: character
          source_key: characters
        - name: situation
          source_key: situations
      items:
        source: "dialogs.yaml"
        name_field: "id"

Config structure for direct strategy:
    processing:
      strategy: direct
      items:
        source: "hands.yaml"
        key: "hands"
        name_field: "id"

Repetition for Monte Carlo simulations:
    processing:
      strategy: direct
      repeat: 1000  # Each unit becomes 1000 units with unique seeds
      items:
        source: "scenarios.yaml"
        key: "scenarios"
        name_field: "id"

    Each repeated unit gets:
      - _repetition_id: Integer 0 to N-1
      - _repetition_seed: Deterministic seed derived from unit_id
      - unit_id: Original ID + __repNNNN suffix
"""

import argparse
import hashlib
import itertools
import json
import sys
from pathlib import Path
from typing import Any

import yaml

from octobatch_utils import load_config, log_error


def log_info(message: str):
    """Log info message to stderr."""
    print(message, file=sys.stderr)


def load_yaml(path: Path) -> dict | list:
    """Load and parse a YAML file."""
    with open(path) as f:
        return yaml.safe_load(f)


def _sanitize_name(name: str) -> str:
    """Sanitize a name for use in unit IDs and filenames."""
    return str(name).replace(" ", "_").replace("/", "-")


def load_items_data(config: dict, config_path: Path) -> dict | list:
    """
    Load items data from the configured source file.

    Returns the entire source data structure (not just a single key).
    """
    processing = config.get("processing", {})
    items_config = processing.get("items", {})

    source = items_config.get("source")
    if not source:
        raise ValueError("processing.items.source is required in config")

    # Resolve source path relative to config file
    config_dir = config_path.parent
    source_path = config_dir / source

    return load_yaml(source_path)


def get_strategy(config: dict) -> str:
    """Get the unit generation strategy from config."""
    processing = config.get("processing", {})
    return processing.get("strategy", "permutation")


def get_positions(config: dict) -> list[dict]:
    """
    Get position definitions from config.

    Returns list of position dicts with at minimum a 'name' key.
    """
    processing = config.get("processing", {})
    positions_config = processing.get("positions", [])

    positions = []
    for pos in positions_config:
        if isinstance(pos, dict):
            if "name" not in pos:
                raise ValueError("Each position must have a 'name' field")
            positions.append(pos)
        elif isinstance(pos, str):
            positions.append({"name": pos})
        else:
            raise ValueError(f"Invalid position format: {pos}")

    return positions


def get_name_field(config: dict) -> str:
    """Get the field name to use for unit_id generation."""
    processing = config.get("processing", {})
    items_config = processing.get("items", {})
    return items_config.get("name_field", "name")


def get_items_key(config: dict) -> str | None:
    """Get the key for extracting items from source data."""
    processing = config.get("processing", {})
    items_config = processing.get("items", {})
    return items_config.get("key")


def get_repeat_count(config: dict) -> int:
    """Get the repeat count from config (default: 1)."""
    processing = config.get("processing", {})
    return processing.get("repeat", 1)


# -----------------------------------------------------------------------------
# Strategy: permutation
# -----------------------------------------------------------------------------

def generate_permutation_units(
    config: dict,
    items_data: dict | list,
    limit: int | None = None
) -> list[dict]:
    """
    Generate all permutations of items across positions.

    This is the default permutation-style generation:
    - All items can fill any position
    - Permutations without replacement
    - 22 items × 3 positions = 22×21×20 = 9,240 units

    Args:
        config: Pipeline configuration
        items_data: Loaded items data from source file
        limit: Optional limit on number of items to use

    Returns:
        List of unit dictionaries
    """
    positions = get_positions(config)
    if not positions:
        raise ValueError("processing.positions is required for permutation strategy")

    name_field = get_name_field(config)
    items_key = get_items_key(config)

    # Extract items from source data
    if items_key:
        if not isinstance(items_data, dict):
            raise ValueError(f"Source file must be a dict when 'key' is specified")
        items = items_data.get(items_key)
        if items is None:
            raise ValueError(f"Key '{items_key}' not found in source file")
    else:
        items = items_data

    if not isinstance(items, list):
        raise ValueError(f"Items must be a list, got {type(items).__name__}")

    # Apply limit
    if limit:
        items = items[:limit]

    num_positions = len(positions)
    if len(items) < num_positions:
        raise ValueError(
            f"Not enough items ({len(items)}) for {num_positions} positions. "
            f"Need at least {num_positions} items."
        )

    units = []
    seen_ids: set[str] = set()

    for perm in itertools.permutations(items, num_positions):
        # Build unit_id
        unit_id_parts = []
        for item in perm:
            name = item.get(name_field)
            if not name:
                raise ValueError(f"Item missing required field '{name_field}': {item}")
            unit_id_parts.append(_sanitize_name(name))
        unit_id = "-".join(unit_id_parts)

        # Check for duplicate
        if unit_id in seen_ids:
            raise ValueError(
                f"Duplicate unit_id detected: '{unit_id}'. "
                f"Ensure all items have unique values for '{name_field}'."
            )
        seen_ids.add(unit_id)

        # Build unit
        unit = {"unit_id": unit_id}
        for i, pos in enumerate(positions):
            unit[pos["name"]] = perm[i]

        units.append(unit)

    return units


# -----------------------------------------------------------------------------
# Strategy: cross_product
# -----------------------------------------------------------------------------

def generate_cross_product_units(
    config: dict,
    items_data: dict | list,
    limit: int | None = None
) -> list[dict]:
    """
    Generate cross product of items from different groups.

    Each position specifies which key in the source file to use:
    - positions: [{name: character, source_key: characters}, {name: situation, source_key: situations}]
    - Source file has: {characters: [...], situations: [...]}
    - Result: 3 characters × 4 situations = 12 units

    Args:
        config: Pipeline configuration
        items_data: Loaded items data from source file
        limit: Optional limit (applied per group)

    Returns:
        List of unit dictionaries
    """
    positions = get_positions(config)
    if not positions:
        raise ValueError("processing.positions is required for cross_product strategy")

    if not isinstance(items_data, dict):
        raise ValueError("Source file must be a dict for cross_product strategy")

    name_field = get_name_field(config)

    # Gather item lists for each position
    item_lists = []
    for position in positions:
        # Get source key: explicit source_key, or position name + 's' as fallback
        source_key = position.get("source_key", position["name"] + "s")
        if source_key not in items_data:
            raise ValueError(
                f"Missing '{source_key}' in source file for position '{position['name']}'. "
                f"Available keys: {list(items_data.keys())}"
            )
        items = items_data[source_key]
        if not isinstance(items, list):
            raise ValueError(f"'{source_key}' must be a list, got {type(items).__name__}")

        # Apply limit per group
        if limit:
            items = items[:limit]

        item_lists.append(items)

    units = []
    seen_ids: set[str] = set()

    for combo in itertools.product(*item_lists):
        # Build unit_id
        unit_id_parts = []
        for item in combo:
            name = item.get(name_field, item.get("id", item.get("name")))
            if not name:
                raise ValueError(f"Item missing field for unit_id (tried '{name_field}', 'id', 'name'): {item}")
            unit_id_parts.append(_sanitize_name(name))
        unit_id = "-".join(unit_id_parts)

        # Check for duplicate
        if unit_id in seen_ids:
            raise ValueError(f"Duplicate unit_id detected: '{unit_id}'")
        seen_ids.add(unit_id)

        # Build unit
        unit = {"unit_id": unit_id}
        for i, position in enumerate(positions):
            unit[position["name"]] = combo[i]

        units.append(unit)

    return units


# -----------------------------------------------------------------------------
# Strategy: direct
# -----------------------------------------------------------------------------

def generate_direct_units(
    config: dict,
    items_data: dict | list,
    limit: int | None = None
) -> list[dict]:
    """
    Use items directly as units (no combination).

    Each item in the source becomes one unit. The item data is accessible
    via the 'item' key in templates, or via position names if defined.

    Args:
        config: Pipeline configuration
        items_data: Loaded items data from source file
        limit: Optional limit on number of units

    Returns:
        List of unit dictionaries
    """
    name_field = get_name_field(config)
    items_key = get_items_key(config)
    positions = get_positions(config)  # May be empty for direct strategy

    # Extract items from source data
    if items_key:
        if not isinstance(items_data, dict):
            raise ValueError(f"Source file must be a dict when 'key' is specified")
        items = items_data.get(items_key)
        if items is None:
            raise ValueError(f"Key '{items_key}' not found in source file")
    else:
        items = items_data

    if not isinstance(items, list):
        raise ValueError(f"Items must be a list, got {type(items).__name__}")

    # Apply limit
    if limit:
        items = items[:limit]

    units = []
    seen_ids: set[str] = set()

    for item in items:
        # Build unit_id
        name = item.get(name_field, item.get("id", item.get("name")))
        if not name:
            raise ValueError(f"Item missing field for unit_id (tried '{name_field}', 'id', 'name'): {item}")
        unit_id = _sanitize_name(name)

        # Check for duplicate
        if unit_id in seen_ids:
            raise ValueError(f"Duplicate unit_id detected: '{unit_id}'")
        seen_ids.add(unit_id)

        # Build unit - item data is flattened into the unit
        # This means template can access {{ character.name }} if item has character key
        unit = {"unit_id": unit_id}

        # If positions are defined, map item keys to position names
        if positions:
            for pos in positions:
                pos_name = pos["name"]
                if pos_name in item:
                    unit[pos_name] = item[pos_name]

        # Also copy all top-level item data for direct access
        for key, value in item.items():
            if key not in unit:
                unit[key] = value

        units.append(unit)

    return units


# -----------------------------------------------------------------------------
# Main dispatcher
# -----------------------------------------------------------------------------

def generate_units(
    config: dict,
    items_data: dict | list,
    limit: int | None = None
) -> list[dict]:
    """
    Generate units based on configured strategy, with optional repetition.

    If processing.repeat is set to N (N > 1), each base unit is duplicated
    N times with unique _repetition_id and _repetition_seed fields.
    This enables Monte Carlo simulations where the same scenario runs
    multiple times with different random seeds.

    Args:
        config: Pipeline configuration
        items_data: Loaded items data from source file
        limit: Optional limit on items/units

    Returns:
        List of unit dictionaries
    """
    strategy = get_strategy(config)
    repeat_count = get_repeat_count(config)

    # Get base units from strategy
    if strategy == "permutation":
        base_units = generate_permutation_units(config, items_data, limit)
    elif strategy == "cross_product":
        base_units = generate_cross_product_units(config, items_data, limit)
    elif strategy == "direct":
        base_units = generate_direct_units(config, items_data, limit)
    else:
        raise ValueError(
            f"Unknown strategy: '{strategy}'. "
            f"Valid strategies: permutation, cross_product, direct"
        )

    # If no repetition, return base units as-is
    if repeat_count == 1:
        return base_units

    # Interleave repetitions across all items so --max-units gives a
    # representative sample. Order: item1_rep0, item2_rep0, ..., itemN_rep0,
    # item1_rep1, item2_rep1, ..., itemN_rep1, ...
    repeated_units = []
    # Pre-compute base seeds for each unit
    base_seeds = {}
    for unit in base_units:
        uid = unit.get("unit_id", "")
        base_seeds[uid] = int(hashlib.sha256(uid.encode()).hexdigest(), 16) & 0x7FFFFFFF

    for rep_id in range(repeat_count):
        for unit in base_units:
            unit_id = unit.get("unit_id", "")
            repeated_unit = unit.copy()
            repeated_unit["_repetition_id"] = rep_id
            repeated_unit["_repetition_seed"] = base_seeds[unit_id] + rep_id
            # Update unit_id to include repetition for uniqueness
            repeated_unit["unit_id"] = f"{unit_id}__rep{rep_id:04d}"
            repeated_units.append(repeated_unit)

    return repeated_units


# -----------------------------------------------------------------------------
# Output functions
# -----------------------------------------------------------------------------

def write_units_to_file(units: list[dict], output_path: Path):
    """Write units to a JSONL file."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w") as f:
        for unit in units:
            f.write(json.dumps(unit) + "\n")


def write_units_chunked(units: list[dict], output_dir: Path, chunk_size: int) -> int:
    """Write units to chunked directories."""
    output_dir.mkdir(parents=True, exist_ok=True)

    num_chunks = 0
    for i in range(0, len(units), chunk_size):
        chunk_units = units[i:i + chunk_size]
        chunk_dir = output_dir / f"chunk_{num_chunks:03d}"
        chunk_dir.mkdir(parents=True, exist_ok=True)

        chunk_file = chunk_dir / "units.jsonl"
        write_units_to_file(chunk_units, chunk_file)

        num_chunks += 1

    return num_chunks


def write_units_to_stdout(units: list[dict]):
    """Write units to stdout as JSONL."""
    for unit in units:
        print(json.dumps(unit))


# -----------------------------------------------------------------------------
# CLI
# -----------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Generate units for batch processing pipelines.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Strategies:
    permutation   All permutations without replacement (default)
    cross_product Cartesian product of items from different groups
    direct        Each item becomes one unit directly

Examples:
    # Generate permutation units (default)
    python generate_units.py --config pipelines/MyPipeline/config.yaml --output units.jsonl

    # Generate cross-product units
    python generate_units.py --config pipelines/NPC_Dialog/config.yaml --output units.jsonl

    # Generate direct units
    python generate_units.py --config pipelines/Poker/config.yaml --output units.jsonl

    # Chunked output
    python generate_units.py --config config.yaml --chunk-size 20 --output-dir chunks/

    # Limit for testing
    python generate_units.py --config config.yaml --max-units 3 --output test.jsonl
        """
    )

    parser.add_argument(
        "--config", "-c",
        required=True,
        type=Path,
        help="Path to config.yaml"
    )

    parser.add_argument(
        "--output", "-o",
        type=Path,
        help="Output file path (JSONL format)"
    )

    parser.add_argument(
        "--output-dir",
        type=Path,
        help="Output directory for chunked output"
    )

    parser.add_argument(
        "--chunk-size",
        type=int,
        default=100,
        help="Number of units per chunk (default: 100)"
    )

    parser.add_argument(
        "--max-units", "--limit",
        type=int,
        dest="max_units",
        help="Limit units for testing"
    )

    parser.add_argument(
        "--quiet", "-q",
        action="store_true",
        help="Suppress summary output"
    )

    args = parser.parse_args()

    # Validate arguments
    if args.output and args.output_dir:
        log_error("Cannot specify both --output and --output-dir")
        sys.exit(1)

    # Load configuration
    try:
        config = load_config(args.config)
    except FileNotFoundError:
        log_error(f"Config file not found: {args.config}")
        sys.exit(1)
    except Exception as e:
        log_error(f"Failed to load config: {e}")
        sys.exit(1)

    # Load items data
    try:
        items_data = load_items_data(config, args.config)
    except ValueError as e:
        log_error(str(e))
        sys.exit(1)
    except FileNotFoundError as e:
        log_error(f"Source file not found: {e}")
        sys.exit(1)

    # Generate units
    try:
        units = generate_units(config, items_data, None)  # Don't limit items, limit final units
    except ValueError as e:
        log_error(str(e))
        sys.exit(1)

    # Apply max_units to final units (for testing with subset of data)
    if args.max_units is not None and len(units) > args.max_units:
        original_count = len(units)
        units = units[:args.max_units]
        log_info(f"Applied max_units: {args.max_units} units (from {original_count})")

    # Output units
    num_chunks = 0
    if args.output_dir:
        num_chunks = write_units_chunked(units, args.output_dir, args.chunk_size)
    elif args.output:
        write_units_to_file(units, args.output)
    else:
        write_units_to_stdout(units)

    # Print summary
    if not args.quiet:
        strategy = get_strategy(config)
        repeat_count = get_repeat_count(config)
        summary = {
            "summary": {
                "strategy": strategy,
                "total_units": len(units),
            }
        }
        if repeat_count > 1:
            base_count = len(units) // repeat_count
            summary["summary"]["base_units"] = base_count
            summary["summary"]["repeat"] = repeat_count
        if args.output_dir:
            summary["summary"]["chunks"] = num_chunks
            summary["summary"]["chunk_size"] = args.chunk_size
        if args.max_units:
            summary["summary"]["max_units_applied"] = args.max_units

        log_info(json.dumps(summary))


if __name__ == "__main__":
    main()
