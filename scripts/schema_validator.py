#!/usr/bin/env python3
"""
schema_validator.py - Generic JSON Schema validator for JSONL streams.

A pure validation pipe that knows nothing about the data it validates.
Reads JSONL from stdin, validates against a JSON Schema, writes valid
objects to stdout and errors to stderr.

Usage:
    cat data.jsonl | python schema_validator.py --schema schema.json > valid.jsonl
    cat data.jsonl | python schema_validator.py --schema schema.json --strict
    cat data.jsonl | python schema_validator.py --schema schema.json --quiet 2>/dev/null

Features:
    - Supports JSON Schema Draft 2020-12
    - Intelligent type coercion (Postel's Law): coerces LLM output types
      to match schema expectations before validation (str→int, str→float,
      str→bool, float→int) with [COERCE] telemetry on stderr
    - Preserves original JSON objects unchanged (no field reordering)
    - Detailed validation error messages
    - Strict mode for fail-fast behavior
    - Summary statistics on completion
"""

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any, Iterator

try:
    from jsonschema import Draft202012Validator, ValidationError
    from jsonschema.exceptions import SchemaError
except ImportError:
    print("Error: jsonschema library required. Install with: pip install jsonschema", file=sys.stderr)
    sys.exit(1)

from octobatch_utils import log_error


def log_info(message: str):
    """Log info message to stderr."""
    print(json.dumps({"info": message}), file=sys.stderr)


def load_schema(schema_path: Path) -> dict:
    """Load and parse a JSON Schema file."""
    with open(schema_path) as f:
        return json.load(f)


def create_validator(schema: dict) -> Draft202012Validator:
    """Create a JSON Schema validator for Draft 2020-12."""
    # Validate the schema itself first
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema)


# ---------------------------------------------------------------------------
# Type coercion — Postel's Law for LLM output
# ---------------------------------------------------------------------------

def _resolve_schema_node(node: dict, defs: dict) -> dict:
    """Resolve $ref pointers to their target schema node."""
    seen = set()
    while "$ref" in node:
        ref = node["$ref"]
        if ref in seen:
            break  # Circular reference guard
        seen.add(ref)
        # Only handle local refs like "#/$defs/woundScore"
        if ref.startswith("#/$defs/"):
            def_name = ref[len("#/$defs/"):]
            if def_name in defs:
                node = defs[def_name]
            else:
                break
        elif ref.startswith("#/"):
            # Other local ref — not supported, bail out
            break
        else:
            break
    return node


def _coerce_value(value: Any, expected_type: str, path: str) -> tuple[Any, bool]:
    """Attempt to coerce *value* to *expected_type*.

    Returns (coerced_value, True) on success, or (original_value, False) if
    coercion is not applicable or fails.
    """
    if expected_type == "integer":
        if isinstance(value, str):
            try:
                coerced = int(value)
                print(f'[COERCE] {path}: "{value}" (str) -> {coerced} (int)', file=sys.stderr)
                return coerced, True
            except (ValueError, OverflowError):
                return value, False
        if isinstance(value, float) and value == int(value) and not (value != value):  # NaN guard
            coerced = int(value)
            print(f"[COERCE] {path}: {value} (float) -> {coerced} (int)", file=sys.stderr)
            return coerced, True

    elif expected_type == "number":
        if isinstance(value, str):
            try:
                coerced = float(value)
                print(f'[COERCE] {path}: "{value}" (str) -> {coerced} (float)', file=sys.stderr)
                return coerced, True
            except (ValueError, OverflowError):
                return value, False

    elif expected_type == "boolean":
        if isinstance(value, str):
            lower = value.lower()
            if lower == "true":
                print(f'[COERCE] {path}: "{value}" (str) -> True (bool)', file=sys.stderr)
                return True, True
            elif lower == "false":
                print(f'[COERCE] {path}: "{value}" (str) -> False (bool)', file=sys.stderr)
                return False, True

    return value, False


def coerce_data(data: Any, schema_node: dict, defs: dict, path: str = "$") -> Any:
    """Recursively walk *data* alongside *schema_node*, coercing types in-place.

    Returns the (possibly mutated) data.  Coercion is best-effort: if it
    fails the original value is left for jsonschema to report.
    """
    schema_node = _resolve_schema_node(schema_node, defs)

    expected_type = schema_node.get("type")

    # --- Leaf coercion -------------------------------------------------------
    if expected_type in ("integer", "number", "boolean"):
        coerced, changed = _coerce_value(data, expected_type, path)
        if changed:
            return coerced
        return data

    # --- Enum normalization --------------------------------------------------
    if "enum" in schema_node and isinstance(data, str):
        enum_values = schema_node["enum"]
        if data not in enum_values:
            normalized = data
            # Strip "shadow " prefix (case-insensitive)
            if normalized.lower().startswith("shadow "):
                normalized = normalized[7:]
            # Split on " | " delimiter, take first part
            if " | " in normalized:
                normalized = normalized.split(" | ")[0]
            # Strip whitespace and lowercase for comparison
            normalized = normalized.strip().lower()
            for ev in enum_values:
                if ev.lower() == normalized:
                    print(f'[COERCE] {path}: "{data}" -> "{ev}" (enum normalization)', file=sys.stderr)
                    return ev

    # --- Object traversal ----------------------------------------------------
    if expected_type == "object" and isinstance(data, dict):
        properties = schema_node.get("properties", {})
        additional_schema = schema_node.get("additionalProperties")

        for key, value in data.items():
            child_path = f"{path}.{key}"
            if key in properties:
                data[key] = coerce_data(value, properties[key], defs, child_path)
            elif isinstance(additional_schema, dict):
                data[key] = coerce_data(value, additional_schema, defs, child_path)
        return data

    # --- String → array coercion ---------------------------------------------
    if expected_type == "array" and isinstance(data, str):
        # Try json.loads first (maybe it's a JSON array string)
        try:
            parsed = json.loads(data)
            if isinstance(parsed, list):
                print(f'[COERCE] {path}: string -> array (parsed JSON string)', file=sys.stderr)
                items_schema = schema_node.get("items")
                if isinstance(items_schema, dict):
                    for i, item in enumerate(parsed):
                        parsed[i] = coerce_data(item, items_schema, defs, f"{path}[{i}]")
                return parsed
        except (json.JSONDecodeError, ValueError):
            pass
        # Fall back to wrapping as single-element array
        items_schema = schema_node.get("items")
        if isinstance(items_schema, dict):
            resolved_items = _resolve_schema_node(items_schema, defs)
            if resolved_items.get("type") == "object":
                properties = resolved_items.get("properties", {})
                if "tag" in properties:
                    wrapped = [{"tag": data}]
                    print(f'[COERCE] {path}: "{data}" (string) -> [{{"tag": ...}}] (array)', file=sys.stderr)
                    return wrapped
        # Generic fallback: wrap as single-element array
        print(f'[COERCE] {path}: "{data}" (string) -> ["{data}"] (single-element array)', file=sys.stderr)
        return [data]

    # --- Array traversal -----------------------------------------------------
    if expected_type == "array" and isinstance(data, list):
        items_schema = schema_node.get("items")
        if isinstance(items_schema, dict):
            for i, item in enumerate(data):
                data[i] = coerce_data(item, items_schema, defs, f"{path}[{i}]")
        return data

    # --- No type or unrecognised — try top-level object if data is a dict ----
    if expected_type is None and isinstance(data, dict):
        properties = schema_node.get("properties", {})
        additional_schema = schema_node.get("additionalProperties")
        if properties or additional_schema:
            for key, value in data.items():
                child_path = f"{path}.{key}"
                if key in properties:
                    data[key] = coerce_data(value, properties[key], defs, child_path)
                elif isinstance(additional_schema, dict):
                    data[key] = coerce_data(value, additional_schema, defs, child_path)
        return data

    return data


def _unwrap_response(data: dict, schema: dict) -> dict:
    """Unwrap double-wrapped JSON responses from LLMs.

    Some LLMs (especially Gemini) nest the actual response inside a
    ``response`` key as a JSON string with markdown backticks::

        {"response": "```json\\n{\\"coherence_scores\\": [...]}\\n```"}

    If a required top-level key is missing but ``response`` exists as a
    string, unwrap the inner JSON and merge its keys into the top-level
    object.
    """
    if not isinstance(data, dict):
        return data

    response_val = data.get("response")
    if not isinstance(response_val, str):
        return data

    # Check if any required key is missing
    required = schema.get("required", [])
    if not required:
        return data

    missing = [k for k in required if k not in data]
    if not missing:
        return data  # All required keys present, no unwrapping needed

    # Strip markdown code fences
    inner_str = response_val.strip()
    if inner_str.startswith("```"):
        first_newline = inner_str.find("\n")
        if first_newline != -1:
            inner_str = inner_str[first_newline + 1:]
        else:
            inner_str = inner_str[3:]
    if inner_str.endswith("```"):
        inner_str = inner_str[:-3]
    inner_str = inner_str.strip()

    # Remove trailing commas before } or ] (invalid JSON but common LLM output)
    cleaned = re.sub(r',\s*([}\]])', r'\1', inner_str)
    if cleaned != inner_str:
        print("[COERCE] Removed trailing commas from JSON response", file=sys.stderr)
        inner_str = cleaned

    try:
        inner = json.loads(inner_str)
    except (json.JSONDecodeError, ValueError) as e:
        print(f"[COERCE] Unwrap parse failed: {e}", file=sys.stderr)
        return data

    if not isinstance(inner, dict):
        return data

    # Check if any of the missing required keys are in the inner dict
    resolved = [k for k in missing if k in inner]
    if not resolved:
        return data

    # Merge inner keys into data (don't overwrite existing keys except response)
    for key, value in inner.items():
        if key not in data or key == "response":
            data[key] = value

    # Remove the response wrapper
    if "response" in data and "response" not in inner:
        del data["response"]

    print("[COERCE] Unwrapped nested JSON response", file=sys.stderr)
    return data


# ---------------------------------------------------------------------------
# Validation helpers
# ---------------------------------------------------------------------------

def format_validation_error(error: ValidationError) -> dict:
    """Format a validation error into a readable structure."""
    return {
        "path": list(error.absolute_path) if error.absolute_path else ["$"],
        "rule": f"schema_{error.validator}" if error.validator else "schema",
        "message": error.message,
        "validator": error.validator,
        "validator_value": str(error.validator_value)[:100] if error.validator_value else None,
        "instance_type": type(error.instance).__name__ if error.instance is not None else "null",
    }


def format_all_errors(errors: list[ValidationError]) -> list[dict]:
    """Format all validation errors, including nested errors."""
    formatted = []
    for error in errors:
        formatted.append(format_validation_error(error))
        # Include context from nested errors if present
        if error.context:
            for suberror in error.context:
                sub_formatted = format_validation_error(suberror)
                sub_formatted["parent_path"] = list(error.absolute_path)
                formatted.append(sub_formatted)
    return formatted


def validate_line(
    line: str,
    validator: Draft202012Validator,
    schema: dict,
    line_num: int,
) -> tuple[dict | None, list[dict] | None]:
    """
    Validate a single JSONL line (with type coercion).

    Returns:
        (parsed_data, None) if valid
        (parsed_data, errors) if invalid (data still returned for retry support)
        (None, errors) if JSON parse failed
    """
    line = line.strip()
    if not line:
        return None, None  # Skip empty lines

    # Parse JSON
    try:
        data = json.loads(line)
    except json.JSONDecodeError as e:
        return None, [{
            "path": ["$"],
            "rule": "schema_json_parse",
            "message": f"Invalid JSON: {e.msg}",
            "validator": "json_parse",
            "line_position": e.pos,
        }]

    # Unwrap double-wrapped JSON responses (e.g., {"response": "```json\n{...}\n```"})
    if isinstance(data, dict):
        data = _unwrap_response(data, schema)

    # Coerce types before validation
    defs = schema.get("$defs", {})
    data = coerce_data(data, schema, defs)

    # Validate against schema
    errors = list(validator.iter_errors(data))

    if errors:
        # Return data along with errors for retry support
        return data, format_all_errors(errors)

    return data, None


def process_stream(
    input_stream: Iterator[str],
    validator: Draft202012Validator,
    schema: dict,
    strict: bool = False,
    quiet: bool = False,
) -> tuple[int, int, list[str]]:
    """
    Process JSONL stream, validating each line.

    Args:
        input_stream: Iterator of lines
        validator: JSON Schema validator
        schema: Raw schema dict (for coercion traversal)
        strict: If True, stop on first error and write nothing
        quiet: If True, suppress info messages

    Returns:
        (valid_count, error_count, collected_valid) if strict mode
        (valid_count, error_count, []) otherwise (writes directly to stdout)
    """
    valid_count = 0
    error_count = 0
    collected_valid = [] if strict else None

    for line_num, line in enumerate(input_stream, 1):
        if not line.strip():
            continue

        data, errors = validate_line(line, validator, schema, line_num)

        if errors:
            error_count += 1

            # Write full failure record to stderr for retry support
            if data is not None:
                failure_record = {
                    "unit_id": data.get("unit_id"),
                    "failure_stage": "schema_validation",
                    "input": data,
                    "errors": errors,
                    "retry_count": data.get("retry_count", 0)
                }
                print(json.dumps(failure_record), file=sys.stderr)
            else:
                # JSON parse error - no data available
                # Preserve the raw text that failed to parse for debugging
                failure_record = {
                    "unit_id": None,
                    "failure_stage": "schema_validation",
                    "input": None,
                    "raw_response": line.strip(),
                    "errors": errors,
                    "retry_count": 0
                }
                print(json.dumps(failure_record), file=sys.stderr)

            if strict:
                # In strict mode, return immediately on first error
                return valid_count, error_count, []

        elif data is not None:
            valid_count += 1

            if strict:
                # In strict mode, collect valid items (write later if all pass)
                collected_valid.append(json.dumps(data))
            else:
                # In normal mode, write coerced data immediately
                print(json.dumps(data))

    return valid_count, error_count, collected_valid if strict else []


def main():
    parser = argparse.ArgumentParser(
        description="Validate JSONL stream against a JSON Schema.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
    # Basic validation - valid lines to stdout, errors to stderr
    cat data.jsonl | python schema_validator.py --schema schema.json > valid.jsonl

    # Positional schema path also works
    cat data.jsonl | python schema_validator.py schema.json

    # Strict mode - fail fast, nothing to stdout if any errors
    cat data.jsonl | python schema_validator.py --schema schema.json --strict

    # Quiet mode - no summary, just validation
    cat data.jsonl | python schema_validator.py --schema schema.json --quiet 2>/dev/null

    # Check exit code for scripting
    if cat data.jsonl | python schema_validator.py --schema schema.json --quiet; then
        echo "All valid"
    else
        echo "Validation failed"
    fi
        """
    )

    parser.add_argument(
        "schema_positional",
        nargs="?",
        type=Path,
        help="Path to JSON Schema file (positional alternative to --schema)"
    )

    parser.add_argument(
        "--schema", "-s",
        type=Path,
        help="Path to JSON Schema file (Draft 2020-12)"
    )

    parser.add_argument(
        "--strict",
        action="store_true",
        help="Strict mode: exit on first error, write nothing to stdout"
    )

    parser.add_argument(
        "--quiet", "-q",
        action="store_true",
        help="Quiet mode: suppress summary statistics"
    )

    args = parser.parse_args()

    # Resolve schema path: --schema flag takes precedence, then positional
    schema_path = args.schema or args.schema_positional
    if schema_path is None:
        parser.error("a schema path is required (positional or --schema)")

    # Load schema
    try:
        schema = load_schema(schema_path)
    except FileNotFoundError:
        log_error(f"Schema file not found: {schema_path}")
        sys.exit(1)
    except json.JSONDecodeError as e:
        log_error(f"Invalid JSON in schema file: {e.msg}")
        sys.exit(1)

    # Create validator
    try:
        validator = create_validator(schema)
    except SchemaError as e:
        log_error(f"Invalid JSON Schema: {e.message}")
        sys.exit(1)

    # Process input
    valid_count, error_count, collected_valid = process_stream(
        sys.stdin,
        validator,
        schema,
        strict=args.strict,
        quiet=args.quiet,
    )

    # In strict mode, only write if all valid
    if args.strict and error_count == 0 and collected_valid:
        for line in collected_valid:
            print(line)

    # Log summary
    if not args.quiet:
        summary = {
            "summary": {
                "valid": valid_count,
                "invalid": error_count,
                "total": valid_count + error_count,
                "schema": str(schema_path),
                "strict_mode": args.strict,
            }
        }
        print(json.dumps(summary), file=sys.stderr)

    # Exit code
    sys.exit(0 if error_count == 0 else 1)


if __name__ == "__main__":
    main()
