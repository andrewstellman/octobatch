#!/usr/bin/env python3
"""
validator.py - Generic expression-based validator for batch processing pipelines.

A single validator that works with any config, replacing domain-specific validators.
Uses declarative rules for common checks and asteval for custom business logic.

Usage:
    cat data.jsonl | python validator.py --config config/example_config.yaml --step generate
    cat outputs.jsonl | python validator.py --config config/example_config.yaml --step validate

Config structure:
    validation:
      <step_name>:
        required: [field1, field2]          # Required fields
        types:                               # Type validation
          field1: integer
          field2: string
        ranges:                              # Numeric ranges [min, max]
          field1: [0, 100]
        enums:                               # Allowed values
          field1: [value1, value2]
        rules:                               # Expression-based rules
          - name: rule_name
            expr: "field1 > field2"
            error: "field1 ({field1}) must be greater than field2 ({field2})"
            when: "some_condition"           # Optional: only run if true
            level: warning                   # Optional: 'error' (default) or 'warning'
"""

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

import yaml
from asteval import Interpreter

from octobatch_utils import create_interpreter, load_config, log_error


# =============================================================================
# Logging Helpers
# =============================================================================

def log_validation_failure(
    line_num: int,
    rule: str,
    message: str,
    expression: str = None,
    actual_values: dict = None
):
    """Log a validation failure with structured details."""
    error_obj = {
        "validation_failure": {
            "line": line_num,
            "rule": rule,
            "message": message,
        }
    }
    if expression:
        error_obj["validation_failure"]["expression"] = expression
    if actual_values:
        error_obj["validation_failure"]["actual_values"] = actual_values
    print(json.dumps(error_obj), file=sys.stderr)


# =============================================================================
# Configuration
# =============================================================================

def get_step_validation_config(config: dict, step: str) -> dict:
    """Get validation configuration for a specific step."""
    return config.get("validation", {}).get(step, {})


# =============================================================================
# Type Validation
# =============================================================================

TYPE_VALIDATORS = {
    "integer": lambda v: isinstance(v, int) and not isinstance(v, bool),
    "float": lambda v: isinstance(v, (int, float)) and not isinstance(v, bool),
    "number": lambda v: isinstance(v, (int, float)) and not isinstance(v, bool),
    "string": lambda v: isinstance(v, str),
    "boolean": lambda v: isinstance(v, bool),
    "array": lambda v: isinstance(v, list),
    "list": lambda v: isinstance(v, list),
    "object": lambda v: isinstance(v, dict),
    "dict": lambda v: isinstance(v, dict),
}

TYPE_NAMES = {
    "integer": "integer",
    "float": "float",
    "number": "number",
    "string": "string",
    "boolean": "boolean",
    "array": "array",
    "list": "list",
    "object": "object",
    "dict": "dict",
}


def validate_type(value: Any, expected_type: str) -> tuple[bool, str]:
    """
    Validate that a value matches the expected type.

    Returns:
        (is_valid, error_message)
    """
    validator = TYPE_VALIDATORS.get(expected_type.lower())
    if validator is None:
        return False, f"Unknown type: {expected_type}"

    if validator(value):
        return True, ""

    actual_type = type(value).__name__
    return False, f"Expected {expected_type}, got {actual_type}"


# =============================================================================
# Expression Engine
# =============================================================================

# create_interpreter is imported from octobatch_utils

# Builtins added by create_interpreter() - used for context clearing
_INTERPRETER_BUILTINS = {
    'sum', 'len', 'min', 'max', 'abs', 'round', 'all', 'any',
    'sorted', 'list', 'dict', 'set', 'str', 'int', 'float', 'bool',
    'isinstance', 'enumerate', 'zip', 'range'
}


def set_interpreter_context(aeval: Interpreter, data: dict):
    """
    Set the data context for the interpreter.

    All fields from data become available as variables in expressions.
    """
    # Clear previous data (keep builtins)
    keys_to_remove = [k for k in aeval.symtable.keys()
                      if not k.startswith('_') and k not in _INTERPRETER_BUILTINS]
    for k in keys_to_remove:
        del aeval.symtable[k]

    # Add data fields
    for key, value in data.items():
        aeval.symtable[key] = value


def evaluate_expression(aeval: Interpreter, expr: str, data: dict) -> tuple[bool, Any, str]:
    """
    Evaluate an expression in the context of the data.

    Returns:
        (success, result, error_message)
    """
    set_interpreter_context(aeval, data)

    try:
        result = aeval.eval(expr)

        if aeval.error:
            error_msgs = [str(e.get_error()[1]) for e in aeval.error]
            aeval.error = []  # Clear errors
            return False, None, "; ".join(error_msgs)

        return True, result, ""

    except Exception as e:
        return False, None, str(e)


def format_error_message(template: str, data: dict, computed: Any = None) -> str:
    """
    Format error message template with actual values.

    Substitutes {variable} with actual values from data.
    Special variable {_computed} holds computed expression result.
    """
    # Build substitution dict
    subs = dict(data)
    if computed is not None:
        subs["_computed"] = computed

    # Find all {variable} patterns
    pattern = r'\{([^}]+)\}'

    def replace_var(match):
        var_name = match.group(1)
        if var_name in subs:
            return str(subs[var_name])
        return match.group(0)  # Keep original if not found

    return re.sub(pattern, replace_var, template)


# =============================================================================
# Declarative Rule Validation
# =============================================================================

def validate_required(data: dict, required_fields: list) -> list[dict]:
    """
    Validate that all required fields are present.

    Returns list of error objects (empty if valid).
    """
    errors = []

    for field in required_fields:
        if field not in data or data[field] is None:
            errors.append({
                "path": f"$.{field}",
                "rule": f"required_{field}",
                "message": f"Missing required field: '{field}'"
            })

    return errors


def validate_types(data: dict, type_config: dict) -> list[dict]:
    """
    Validate field types.

    Returns list of error objects (empty if valid).
    """
    errors = []

    for field, expected_type in type_config.items():
        if field not in data:
            continue  # Skip missing fields (handled by required)

        is_valid, error_msg = validate_type(data[field], expected_type)
        if not is_valid:
            errors.append({
                "path": f"$.{field}",
                "rule": f"type_{field}",
                "message": f"Field '{field}': {error_msg}"
            })

    return errors


def validate_ranges(data: dict, range_config: dict) -> list[dict]:
    """
    Validate numeric ranges.

    Returns list of error objects (empty if valid).
    """
    errors = []

    for field, (min_val, max_val) in range_config.items():
        if field not in data:
            continue

        value = data[field]
        if not isinstance(value, (int, float)):
            continue  # Type validation handles this

        if value < min_val or value > max_val:
            errors.append({
                "path": f"$.{field}",
                "rule": f"range_{field}",
                "message": f"Field '{field}' value {value} outside range [{min_val}, {max_val}]"
            })

    return errors


def validate_enums(data: dict, enum_config: dict) -> list[dict]:
    """
    Validate enum (allowed values).

    Returns list of error objects (empty if valid).
    """
    errors = []

    for field, allowed_values in enum_config.items():
        if field not in data:
            continue

        value = data[field]
        # Handle case-insensitive string comparison
        if isinstance(value, str):
            value_lower = value.lower()
            allowed_lower = [v.lower() if isinstance(v, str) else v for v in allowed_values]
            if value_lower not in allowed_lower:
                errors.append({
                    "path": f"$.{field}",
                    "rule": f"enum_{field}",
                    "message": f"Field '{field}' value '{value}' not in allowed values: {allowed_values}"
                })
        else:
            if value not in allowed_values:
                errors.append({
                    "path": f"$.{field}",
                    "rule": f"enum_{field}",
                    "message": f"Field '{field}' value '{value}' not in allowed values: {allowed_values}"
                })

    return errors


# =============================================================================
# Expression Rule Validation
# =============================================================================

def validate_expression_rules(
    data: dict,
    rules: list,
    aeval: Interpreter,
    line_num: int
) -> tuple[list[dict], list[dict]]:
    """
    Validate expression-based rules.

    Returns:
        (list of error objects, list of warning objects)
    """
    errors = []
    warnings = []

    for rule in rules:
        rule_name = rule.get("name", "unnamed_rule")
        expr = rule.get("expr", "")
        error_template = rule.get("error", f"Rule '{rule_name}' failed")
        when_expr = rule.get("when")
        level = rule.get("level", "error")

        # Check 'when' condition if present
        if when_expr:
            success, when_result, err_msg = evaluate_expression(aeval, when_expr, data)
            if not success:
                # 'when' evaluation failed - add warning and skip rule
                warnings.append({
                    "path": "$",
                    "rule": rule_name,
                    "message": f"when clause failed to evaluate: {err_msg}",
                    "when_expression": when_expr
                })
                continue
            if not when_result:
                # Condition not met, skip this rule
                continue

        # Evaluate the main expression
        success, result, err_msg = evaluate_expression(aeval, expr, data)

        if not success:
            # Expression evaluation failed
            error_msg = format_error_message(error_template, data)
            if level == "warning":
                warnings.append({
                    "path": "$",
                    "rule": rule_name,
                    "message": f"{error_msg} (expression error: {err_msg})"
                })
            else:
                errors.append({
                    "path": "$",
                    "rule": rule_name,
                    "message": f"{error_msg} (expression error: {err_msg})"
                })
            continue

        # Check if result is boolean false (validation failed)
        if result is False or (isinstance(result, bool) and not result):
            # For expressions that compute a value, try to extract it
            computed = None
            # Check for comparison operators and extract left side
            for op in ["==", "!=", ">=", "<=", ">", "<"]:
                if op in expr:
                    parts = expr.split(op, 1)
                    if len(parts) == 2:
                        left_expr = parts[0].strip()
                        comp_success, comp_result, _ = evaluate_expression(aeval, left_expr, data)
                        if comp_success:
                            computed = comp_result
                    break

            error_msg = format_error_message(error_template, data, computed)

            if level == "warning":
                warnings.append({
                    "path": "$",
                    "rule": rule_name,
                    "message": error_msg
                })
            else:
                errors.append({
                    "path": "$",
                    "rule": rule_name,
                    "message": error_msg
                })

    return errors, warnings


def _truncate_value(value, max_list_items: int = 5, max_string_len: int = 200):
    """
    Truncate a value for logging purposes.

    - Lists with more than max_list_items: show first items + "... (N more items)"
    - Strings longer than max_string_len: truncate with "... (truncated)"
    - Dicts: truncate each value recursively
    """
    if isinstance(value, str):
        if len(value) > max_string_len:
            return value[:max_string_len] + f"... (truncated, {len(value)} chars total)"
        return value
    elif isinstance(value, list):
        if len(value) > max_list_items:
            truncated = [_truncate_value(v, max_list_items, max_string_len) for v in value[:max_list_items]]
            truncated.append(f"... ({len(value) - max_list_items} more items)")
            return truncated
        return [_truncate_value(v, max_list_items, max_string_len) for v in value]
    elif isinstance(value, dict):
        items = list(value.items())
        if len(items) > max_list_items:
            truncated = {k: _truncate_value(v, max_list_items, max_string_len) for k, v in items[:max_list_items]}
            truncated["..."] = f"({len(items) - max_list_items} more keys)"
            return truncated
        return {k: _truncate_value(v, max_list_items, max_string_len) for k, v in items}
    else:
        return value


def _get_relevant_values(data: dict, expr: str) -> dict:
    """Extract values from data that are referenced in the expression, with truncation."""
    relevant = {}
    for key, value in data.items():
        if key in expr:
            relevant[key] = _truncate_value(value)
    return relevant


# =============================================================================
# Main Validation
# =============================================================================

def validate_line(
    data: dict,
    validation_config: dict,
    aeval: Interpreter,
    line_num: int
) -> tuple[bool, list[dict], list[dict]]:
    """
    Validate a single data object against the validation config.

    Returns:
        (is_valid, list of error objects, list of warning objects)
    """
    all_errors = []
    all_warnings = []

    # 1. Required fields
    required = validation_config.get("required", [])
    if required:
        errors = validate_required(data, required)
        all_errors.extend(errors)

    # If missing required fields, skip further validation
    if all_errors:
        return False, all_errors, all_warnings

    # 2. Type validation
    types = validation_config.get("types", {})
    if types:
        errors = validate_types(data, types)
        all_errors.extend(errors)

    # 3. Range validation
    ranges = validation_config.get("ranges", {})
    if ranges:
        errors = validate_ranges(data, ranges)
        all_errors.extend(errors)

    # 4. Enum validation
    enums = validation_config.get("enums", {})
    if enums:
        errors = validate_enums(data, enums)
        all_errors.extend(errors)

    # 5. Expression rules
    rules = validation_config.get("rules", [])
    if rules:
        errors, warnings = validate_expression_rules(data, rules, aeval, line_num)
        all_errors.extend(errors)
        all_warnings.extend(warnings)

    is_valid = len(all_errors) == 0
    return is_valid, all_errors, all_warnings


def process_line(
    line: str,
    validation_config: dict,
    aeval: Interpreter,
    line_num: int
) -> tuple[dict | None, bool, list[dict], list[dict]]:
    """
    Process a single line of input.

    Returns:
        (data or None, is_valid, warnings, errors)
    """
    line = line.strip()
    if not line:
        return None, True, [], []  # Empty lines are valid (just skipped)

    try:
        data = json.loads(line)
    except json.JSONDecodeError as e:
        log_error(f"Invalid JSON on line {line_num}: {e.msg}")
        return None, False, [], [{"path": "$", "rule": "json_parse", "message": str(e.msg)}]

    is_valid, errors, warnings = validate_line(data, validation_config, aeval, line_num)

    if is_valid:
        return data, True, warnings, []
    else:
        return data, False, warnings, errors


# =============================================================================
# Main
# =============================================================================

def main():
    parser = argparse.ArgumentParser(
        description="Generic expression-based validator for batch processing.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
    # Validate generation output
    cat outputs.jsonl | python validator.py -c config/example_config.yaml -s generate

    # Validate scoring step output
    cat scores.jsonl | python validator.py -c config/example_config.yaml -s validate

    # Validate with custom config
    cat data.jsonl | python validator.py -c my_config.yaml -s my_step

Config Structure:
    validation:
      <step_name>:
        required: [field1, field2]
        types:
          field1: integer
        ranges:
          field1: [0, 100]
        enums:
          field1: [a, b, c]
        rules:
          - name: my_rule
            expr: "field1 > 0"
            error: "field1 must be positive, got {field1}"
            when: "field2 == 'check'"
            level: warning
        """
    )

    parser.add_argument(
        "--config", "-c",
        required=True,
        type=Path,
        help="Path to config.yaml with validation rules"
    )

    parser.add_argument(
        "--step", "-s",
        required=True,
        help="Pipeline step to validate"
    )

    parser.add_argument(
        "--quiet", "-q",
        action="store_true",
        help="Suppress summary output"
    )

    args = parser.parse_args()

    # Load configuration
    try:
        config = load_config(args.config)
    except FileNotFoundError:
        log_error(f"Config file not found: {args.config}")
        sys.exit(1)
    except Exception as e:
        log_error(f"Failed to load config: {e}")
        sys.exit(1)

    # Get validation config for this step
    validation_config = get_step_validation_config(config, args.step)
    if not validation_config:
        log_error(f"No validation config for step: '{args.step}'")
        sys.exit(1)

    # Create reusable interpreter
    aeval = create_interpreter()

    # Process input
    valid_count = 0
    error_count = 0
    warning_count = 0

    for line_num, line in enumerate(sys.stdin, 1):
        data, is_valid, warnings, errors = process_line(
            line, validation_config, aeval, line_num
        )

        if is_valid and data is not None:
            # Valid - embed warnings if any, then write to stdout
            if warnings:
                data["_warnings"] = warnings
                warning_count += len(warnings)
            print(json.dumps(data))
            valid_count += 1
        elif not is_valid:
            error_count += 1
            # Write full failure record to stderr for retry support
            if data is not None:
                failure_record = {
                    "unit_id": data.get("unit_id"),
                    "failure_stage": "validation",
                    "step": args.step,
                    "input": data,
                    "errors": errors,
                    "retry_count": data.get("retry_count", 0)
                }
                print(json.dumps(failure_record), file=sys.stderr)

    # Summary
    if not args.quiet:
        summary = {
            "summary": {
                "step": args.step,
                "valid": valid_count,
                "invalid": error_count,
                "warnings": warning_count,
                "total": valid_count + error_count,
            }
        }
        print(json.dumps(summary), file=sys.stderr)

    # Exit code: 0 if all valid, 1 if any failures
    sys.exit(0 if error_count == 0 else 1)


if __name__ == "__main__":
    main()
