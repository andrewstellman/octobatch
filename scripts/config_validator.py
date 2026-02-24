#!/usr/bin/env python3
"""
config_validator.py - Config validation for batch processing pipelines.

Validates configuration files including:
- Basic structure (pipeline, processing, items)
- Template file existence
- Schema file existence
- Validation expression syntax

Usage:
    python config_validator.py --config config/example_config.yaml

Output:
    JSON to stdout with validation results.
    Exit code 0 if valid, 1 if errors found.
"""

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

import yaml

from octobatch_utils import create_interpreter
from expression_evaluator import create_validation_interpreter, validate_expression as validate_expr

# Check if asteval is available
try:
    from asteval import Interpreter
    ASTEVAL_AVAILABLE = True
except ImportError:
    ASTEVAL_AVAILABLE = False


def validate_config(config: dict) -> list[str]:
    """
    Validate that config has required fields for orchestration.

    Supports three unit generation strategies:
    - permutation (default): Requires positions
    - cross_product: Requires positions with source_key
    - direct: Positions optional

    Returns list of error messages (empty if valid).
    """
    errors = []

    # Check pipeline
    if "pipeline" not in config:
        errors.append("Missing 'pipeline' section")
    elif "steps" not in config.get("pipeline", {}):
        errors.append("Missing 'pipeline.steps'")
    elif not config["pipeline"]["steps"]:
        errors.append("'pipeline.steps' is empty")
    else:
        # Check each step has a name and valid scope
        valid_scopes = ("chunk", "run", "expression")
        for i, step in enumerate(config["pipeline"]["steps"]):
            if "name" not in step:
                errors.append(f"Pipeline step {i} missing 'name'")
            else:
                step_name = step["name"]
                scope = step.get("scope", "chunk")
                if scope not in valid_scopes:
                    errors.append(f"Pipeline step '{step_name}' has invalid scope '{scope}'. Valid: {valid_scopes}")
                # Expression steps must have an expressions block
                if scope == "expression":
                    if "expressions" not in step:
                        errors.append(f"Expression step '{step_name}' missing 'expressions' block")
                    elif not isinstance(step["expressions"], dict):
                        errors.append(f"Expression step '{step_name}' expressions must be a dict")
                    elif not step["expressions"]:
                        errors.append(f"Expression step '{step_name}' expressions block is empty")
                # Validate optional per-step provider/model overrides
                valid_providers = ("gemini", "openai", "anthropic")
                if "provider" in step:
                    if not isinstance(step["provider"], str) or step["provider"].lower() not in valid_providers:
                        errors.append(
                            f"Pipeline step '{step_name}' has invalid provider '{step['provider']}'. "
                            f"Valid: {valid_providers}"
                        )
                if "model" in step:
                    if not isinstance(step["model"], str):
                        errors.append(f"Pipeline step '{step_name}' model must be a string")

    # Check processing
    if "processing" not in config:
        errors.append("Missing 'processing' section")
    else:
        processing = config["processing"]
        strategy = processing.get("strategy", "permutation")

        # Validate strategy value
        valid_strategies = ("permutation", "cross_product", "direct")
        if strategy not in valid_strategies:
            errors.append(f"Invalid 'processing.strategy': '{strategy}'. Valid: {valid_strategies}")

        if "chunk_size" not in processing:
            errors.append("Missing 'processing.chunk_size'")

        # positions required for permutation and cross_product strategies
        if strategy in ("permutation", "cross_product"):
            if "positions" not in processing:
                errors.append(f"Missing 'processing.positions' (required for {strategy} strategy)")

        if "items" not in processing:
            errors.append("Missing 'processing.items'")
        elif "source" not in processing.get("items", {}):
            # Check for self-referential config (has 'key' instead of 'source')
            if "key" not in processing.get("items", {}):
                errors.append("Missing 'processing.items.source' or 'processing.items.key'")

        # Validate repeat count if present
        repeat = processing.get("repeat")
        if repeat is not None:
            if not isinstance(repeat, int):
                errors.append("processing.repeat must be an integer")
            elif repeat < 1:
                errors.append("processing.repeat must be at least 1")

        # Validate expressions dict if present
        expressions = processing.get("expressions")
        if expressions is not None:
            if not isinstance(expressions, dict):
                errors.append("processing.expressions must be a dict of {name: expression}")
            else:
                for name, expr in expressions.items():
                    if not isinstance(name, str):
                        errors.append(f"Expression name must be string, got {type(name).__name__}")
                    if not isinstance(expr, str):
                        errors.append(f"Expression '{name}' must be a string, got {type(expr).__name__}")
                    else:
                        # Validate expression syntax using validation interpreter with mock random
                        is_valid, error = validate_expr(expr)
                        if not is_valid:
                            errors.append(f"Expression '{name}' is invalid: {error}")

    # Check prompts, schemas, and api sections — required when pipeline has LLM steps
    steps = config.get("pipeline", {}).get("steps", [])
    has_llm_steps = any(
        step.get("scope", "chunk") not in ("expression", "run")
        for step in steps
        if "name" in step
    )
    if has_llm_steps:
        if "prompts" not in config:
            errors.append("Missing 'prompts' section (required when pipeline has LLM steps)")
        if "schemas" not in config:
            errors.append("Missing 'schemas' section (required when pipeline has LLM steps)")
        if "api" not in config:
            errors.append("Missing 'api' section (required when pipeline has LLM steps)")

    return errors


def get_pipeline_steps(config: dict) -> list[str]:
    """Extract ordered list of pipeline step names from config."""
    steps = config.get("pipeline", {}).get("steps", [])
    return [step["name"] for step in steps if "name" in step]


def get_chunk_scope_steps(config: dict) -> list[str]:
    """
    Extract ordered list of chunk-scope pipeline step names.

    Steps with scope: chunk (or no scope specified) are chunk-scope.
    Expression steps (scope: expression) are also processed at chunk level.
    """
    steps = config.get("pipeline", {}).get("steps", [])
    return [
        step["name"] for step in steps
        if "name" in step and step.get("scope", "chunk") in ("chunk", "expression")
    ]


def get_expression_steps(config: dict) -> list[dict]:
    """
    Extract list of expression-only pipeline step configs.

    Expression steps transform data without calling LLMs.
    Returns the full step config dict for each expression step.
    """
    steps = config.get("pipeline", {}).get("steps", [])
    return [
        step for step in steps
        if "name" in step and step.get("scope") == "expression"
    ]


def get_run_scope_steps(config: dict) -> list[dict]:
    """
    Extract list of run-scope pipeline step configs.

    Returns the full step config dict for each run-scope step.
    """
    steps = config.get("pipeline", {}).get("steps", [])
    return [
        step for step in steps
        if "name" in step and step.get("scope") == "run"
    ]


def get_step_config(config: dict, step_name: str) -> dict | None:
    """
    Get the full config dict for a specific pipeline step by name.

    Returns None if step not found.
    """
    steps = config.get("pipeline", {}).get("steps", [])
    for step in steps:
        if step.get("name") == step_name:
            return step
    return None


def get_item_source_path(config: dict, config_path: Path) -> Path | None:
    """
    Get the path to the item source file referenced by config.

    Returns None if config is self-referential (uses 'key' instead of 'source').
    """
    items_config = config.get("processing", {}).get("items", {})

    if "source" in items_config:
        source = items_config["source"]
        # Resolve relative to config file's directory
        return config_path.parent / source

    # Self-referential config (e.g., example_config.yaml with 'key')
    return None


def _load_item_field_mocks(config: dict, config_path: Path) -> dict[str, Any]:
    """
    Load mock values from the first item in the items source file.

    Returns dict of {field_name: actual_value} for use in expression validation.
    Returns empty dict if items cannot be loaded.
    """
    item_source_path = get_item_source_path(config, config_path)
    if not item_source_path or not item_source_path.exists():
        return {}

    try:
        with open(item_source_path) as f:
            items_data = yaml.safe_load(f)
    except (yaml.YAMLError, OSError):
        return {}

    if not isinstance(items_data, dict):
        return {}

    # Get the items key from config (e.g., "scenarios", "strategies")
    items_key = config.get("processing", {}).get("items", {}).get("key", "")
    if not items_key or items_key not in items_data:
        return {}

    items_list = items_data[items_key]
    if not isinstance(items_list, list) or not items_list:
        return {}

    # Use the first item as a template for mock values
    first_item = items_list[0]
    if not isinstance(first_item, dict):
        return {}

    mocks = {}
    for key, value in first_item.items():
        mocks[key] = value
    return mocks


def generate_mock_value(field_name: str, type_info: dict) -> Any:
    """
    Generate mock value based on type information from config.

    Uses type definitions from config's types/ranges sections.
    This is purely type-driven - no field name pattern matching.

    Type mappings:
        - integer: Returns range midpoint if defined, otherwise 5
        - number: Returns 5.0
        - string: Returns "sample"
        - array: Returns list with sample dict items (for iteration)
        - object: Returns dict with sample keys (for .get() calls)
        - boolean: Returns True
        - Unknown type: Returns "mock_value" string
    """
    types = type_info.get("types", {})
    ranges = type_info.get("ranges", {})

    field_type = types.get(field_name)

    if field_type == "integer":
        # Use range midpoint if available
        if field_name in ranges:
            range_vals = ranges[field_name]
            if isinstance(range_vals, list) and len(range_vals) == 2:
                return (range_vals[0] + range_vals[1]) // 2
        return 5
    elif field_type == "number":
        return 5.0
    elif field_type == "string":
        return "sample"
    elif field_type == "array":
        # Generic array with dict items that support common operations
        # Includes .get() method for expressions like item.get('key')
        return [
            {"type": "a", "value": 10, "status": "complete", "get": lambda k, d=None: {"type": "a", "value": 10, "status": "complete"}.get(k, d)},
            {"type": "b", "value": 5, "status": "pending", "get": lambda k, d=None: {"type": "b", "value": 5, "status": "pending"}.get(k, d)},
        ]
    elif field_type == "object":
        # Generic object with sample keys
        return {"key1": 1, "key2": 2, "key3": 3}
    elif field_type == "boolean":
        return True
    else:
        # No type information - return generic string mock value
        return "mock_value"


def extract_variable_names(expression: str) -> set[str]:
    """
    Extract variable names from a Python expression.

    Uses regex to find potential variable names.
    """
    # Find all potential identifiers
    identifiers = set(re.findall(r'\b([a-zA-Z_][a-zA-Z0-9_]*)\b', expression))

    # Filter out Python keywords and builtins
    keywords = {
        'and', 'or', 'not', 'in', 'is', 'True', 'False', 'None',
        'for', 'if', 'else', 'elif', 'lambda', 'def', 'class',
        'return', 'yield', 'import', 'from', 'as', 'with', 'try',
        'except', 'finally', 'raise', 'assert', 'pass', 'break',
        'continue', 'global', 'nonlocal', 'del', 'while',
        # Builtins we allow
        'len', 'sum', 'min', 'max', 'abs', 'all', 'any', 'sorted',
        'set', 'list', 'dict', 'str', 'int', 'float', 'bool',
        'get', 'values', 'keys', 'items', 'lower', 'upper',
    }

    return identifiers - keywords


def build_mock_context(step_config: dict) -> dict:
    """
    Build mock context from step's validation config.

    Creates type-appropriate mock values for all variables
    used in expressions.
    """
    context = {}

    # Extract type hints from config
    type_info = {
        "types": step_config.get("types", {}),
        "ranges": step_config.get("ranges", {})
    }

    # Find all variables from required fields
    for field in step_config.get("required", []):
        context[field] = generate_mock_value(field, type_info)

    # Find variables from types section
    for field in type_info["types"]:
        if field not in context:
            context[field] = generate_mock_value(field, type_info)

    # Find variables from ranges section
    for field in type_info["ranges"]:
        if field not in context:
            context[field] = generate_mock_value(field, type_info)

    # Find variables from expressions in rules
    for rule in step_config.get("rules", []):
        for expr_field in ("expr", "when"):
            if expr_field in rule:
                variables = extract_variable_names(rule[expr_field])
                for var in variables:
                    if var not in context:
                        context[var] = generate_mock_value(var, type_info)

    # Add special _computed variable
    context["_computed"] = 0

    return context


def extract_expressions(config: dict) -> list[dict]:
    """
    Extract all expressions from config with their locations.

    Returns list of dicts with step, rule, field, and expression.
    Includes both validation rules and expression step definitions.
    """
    expressions = []

    # Extract from validation section
    validation = config.get("validation", {})

    for step_name, step_config in validation.items():
        if not isinstance(step_config, dict):
            continue

        for rule in step_config.get("rules", []):
            rule_name = rule.get("name", "unnamed")

            # Main expression (expr)
            if "expr" in rule:
                expressions.append({
                    "step": step_name,
                    "rule": rule_name,
                    "field": "expr",
                    "expression": rule["expr"]
                })

            # Condition expression (when)
            if "when" in rule:
                expressions.append({
                    "step": step_name,
                    "rule": rule_name,
                    "field": "when",
                    "expression": rule["when"]
                })

    # Extract from expression steps (scope: expression)
    for step in config.get("pipeline", {}).get("steps", []):
        if step.get("scope") == "expression":
            step_name = step.get("name", "unnamed")

            # Extract from init block (same format as expressions: dict of name: expression)
            for init_name, init_value in step.get("init", {}).items():
                if isinstance(init_value, str):
                    expressions.append({
                        "step": step_name,
                        "rule": init_name,
                        "field": "expression_step_init",
                        "expression": init_value
                    })

            # Extract from expressions block
            for expr_name, expr_value in step.get("expressions", {}).items():
                if isinstance(expr_value, str):
                    expressions.append({
                        "step": step_name,
                        "rule": expr_name,
                        "field": "expression_step",
                        "expression": expr_value
                    })

            # Extract loop_until condition (single expression string)
            loop_until = step.get("loop_until")
            if isinstance(loop_until, str):
                expressions.append({
                    "step": step_name,
                    "rule": "loop_until",
                    "field": "expression_step_loop_until",
                    "expression": loop_until
                })

    return expressions


def validate_expression(aeval, expression: str, context: dict) -> tuple[bool, str]:
    """
    Validate an expression by evaluating it with mock context.

    Returns (success, error_message).
    """
    # Set context
    for key, value in context.items():
        aeval.symtable[key] = value

    try:
        aeval.eval(expression)

        if aeval.error:
            error_msgs = [str(e.get_error()[1]) for e in aeval.error]
            aeval.error = []
            return False, "; ".join(error_msgs)

        return True, ""

    except Exception as e:
        return False, str(e)


def validate_config_run(config_path: Path) -> dict:
    """
    Validate a config file including all expressions.

    Returns dict with validation results.
    """
    errors = []
    warnings = []

    print(f"Validating config: {config_path}")
    print()

    # Step 1: Load config
    if not config_path.exists():
        print(f"✗ Config file not found: {config_path}")
        return {"valid": False, "errors": [f"Config file not found: {config_path}"]}

    try:
        with open(config_path) as f:
            config = yaml.safe_load(f)
    except yaml.YAMLError as e:
        print(f"✗ Invalid YAML: {e}")
        return {"valid": False, "errors": [f"Invalid YAML: {e}"]}

    # Step 2: Validate basic structure
    structure_errors = validate_config(config)
    if structure_errors:
        print("✗ Config structure invalid:")
        for err in structure_errors:
            print(f"    - {err}")
            errors.append(err)
    else:
        print("✓ Config structure valid")

    # Step 3: Check pipeline
    pipeline_steps = get_pipeline_steps(config)
    expression_steps = get_expression_steps(config)
    expression_step_names = [s["name"] for s in expression_steps]
    if pipeline_steps:
        step_info = []
        for step_name in pipeline_steps:
            if step_name in expression_step_names:
                step_info.append(f"{step_name}[expr]")
            else:
                step_info.append(step_name)
        print(f"✓ Pipeline: {len(pipeline_steps)} steps ({', '.join(step_info)})")
    else:
        print("✗ Pipeline: No steps defined")
        errors.append("No pipeline steps defined")

    # Step 4: Check template files
    config_dir = config_path.parent
    templates_config = config.get("prompts", {})
    template_dir = templates_config.get("template_dir", "templates")
    templates = templates_config.get("templates", {})

    templates_found = 0
    templates_missing = []

    for step_name, template_file in templates.items():
        template_path = config_dir / template_dir / template_file
        if template_path.exists():
            templates_found += 1
        else:
            templates_missing.append(f"{template_file} (step: {step_name})")

    if templates_missing:
        print(f"✗ Templates: {len(templates_missing)} missing")
        for t in templates_missing:
            print(f"    - {t}")
            errors.append(f"Template not found: {t}")
    elif templates_found > 0:
        print(f"✓ Templates: {templates_found} found, all exist")
    else:
        print("✓ Templates: none configured")

    # Step 5: Check schema files
    schemas_config = config.get("schemas", {})
    schema_dir = schemas_config.get("schema_dir", "schemas")
    schema_files = schemas_config.get("files", {})

    schemas_found = 0
    schemas_missing = []

    for step_name, schema_file in schema_files.items():
        schema_path = config_dir / schema_dir / schema_file
        if schema_path.exists():
            schemas_found += 1
        else:
            schemas_missing.append(f"{schema_file} (step: {step_name})")

    if schemas_missing:
        print(f"✗ Schemas: {len(schemas_missing)} missing")
        for s in schemas_missing:
            print(f"    - {s}")
            errors.append(f"Schema not found: {s}")
    elif schemas_found > 0:
        print(f"✓ Schemas: {schemas_found} found, all exist")
    else:
        print("✓ Schemas: none configured")

    # Step 5b: 4-Point Link Rule — cross-reference check for non-expression, non-run-scope steps
    if pipeline_steps:
        validation_rules = config.get("validation", {})
        run_scope_step_names = {s["name"] for s in get_run_scope_steps(config)}
        exempt_steps = set(expression_step_names) | run_scope_step_names
        link_errors = []
        for step_name in pipeline_steps:
            if step_name in exempt_steps:
                continue  # Expression and run-scope steps are exempt
            missing_links = []
            if step_name not in templates:
                missing_links.append("template")
            if step_name not in schema_files:
                missing_links.append("schema")
            if step_name not in validation_rules:
                missing_links.append("validation rules")
            if missing_links:
                link_errors.append(f"{step_name}: missing {', '.join(missing_links)}")

        if link_errors:
            print(f"✗ 4-Point Link Rule: {len(link_errors)} steps incomplete")
            for le in link_errors:
                print(f"    - {le}")
                errors.append(f"4-Point Link Rule violation: {le}")
        else:
            llm_step_count = len([s for s in pipeline_steps if s not in exempt_steps])
            if llm_step_count > 0:
                print(f"✓ 4-Point Link Rule: all {llm_step_count} LLM steps have template, schema, and validation")

    # Step 6: Validate expressions
    expressions = []
    if not ASTEVAL_AVAILABLE:
        print("⚠ Expressions: asteval not available, skipping expression validation")
        warnings.append("asteval not available, expression validation skipped")
    else:
        # Use validation interpreter which includes mock random module
        aeval = create_validation_interpreter()
        expressions = extract_expressions(config)
        expression_errors = []

        validation_config = config.get("validation", {})

        # Separate validation and expression step expressions
        expression_step_fields = {"expression_step", "expression_step_init", "expression_step_loop_until"}
        validation_expressions = [e for e in expressions if e["field"] not in expression_step_fields]
        expression_step_expressions = [e for e in expressions if e["field"] in expression_step_fields]

        # Validate expression step expressions with sequential evaluation.
        # Group by step, then evaluate in order: init -> expressions -> loop_until.
        # Results from each expression are injected into the namespace so
        # subsequent expressions can reference them (mirrors runtime behavior).
        import ast
        from collections import defaultdict

        # Load item field mocks for expression context
        item_field_mocks = _load_item_field_mocks(config, config_path)

        # Group by step name, preserving evaluation order
        step_groups = defaultdict(lambda: {"init": [], "expressions": [], "loop_until": []})
        for expr_info in expression_step_expressions:
            step = expr_info["step"]
            field = expr_info["field"]
            if field == "expression_step_init":
                step_groups[step]["init"].append(expr_info)
            elif field == "expression_step":
                step_groups[step]["expressions"].append(expr_info)
            elif field == "expression_step_loop_until":
                step_groups[step]["loop_until"].append(expr_info)

        for step_name, group in step_groups.items():
            # Fresh interpreter per step (with MockRandom)
            step_aeval = create_validation_interpreter()

            # Seed with item fields and standard placeholders
            for key, value in item_field_mocks.items():
                step_aeval.symtable[key] = value
            step_aeval.symtable.setdefault("unit_id", "mock_unit")
            step_aeval.symtable.setdefault("_repetition_seed", 42)
            step_aeval.symtable.setdefault("_repetition_id", 0)

            # Process in order: init, then expressions, then loop_until
            for phase in ("init", "expressions", "loop_until"):
                for expr_info in group[phase]:
                    expression = expr_info["expression"]
                    rule = expr_info["rule"]
                    field = expr_info["field"]

                    # Syntax check first
                    try:
                        ast.parse(expression, mode='eval')
                    except SyntaxError as e:
                        expression_errors.append({
                            "step": step_name,
                            "rule": rule,
                            "field": field,
                            "expression": expression,
                            "error": f"Syntax error: {e.msg}"
                        })
                        continue

                    # Evaluate with accumulated namespace
                    try:
                        result = step_aeval.eval(expression)
                        if step_aeval.error:
                            error_msgs = [str(e.get_error()[1]) for e in step_aeval.error]
                            step_aeval.error = []
                            expression_errors.append({
                                "step": step_name,
                                "rule": rule,
                                "field": field,
                                "expression": expression,
                                "error": "; ".join(error_msgs)
                            })
                            # Inject fallback so downstream expressions don't cascade
                            if phase != "loop_until":
                                step_aeval.symtable[rule] = 0
                        elif phase != "loop_until":
                            # Inject result so subsequent expressions can reference it
                            step_aeval.symtable[rule] = result
                    except Exception as e:
                        expression_errors.append({
                            "step": step_name,
                            "rule": rule,
                            "field": field,
                            "expression": expression,
                            "error": str(e)
                        })
                        # Inject fallback so downstream expressions don't cascade
                        if phase != "loop_until":
                            step_aeval.symtable[rule] = 0

        for expr_info in validation_expressions:
            step = expr_info["step"]
            rule = expr_info["rule"]
            field = expr_info["field"]
            expression = expr_info["expression"]

            # Build mock context for this step
            step_config = validation_config.get(step, {})
            context = build_mock_context(step_config)

            # Validate expression
            success, error_msg = validate_expression(aeval, expression, context)

            if not success:
                expression_errors.append({
                    "step": step,
                    "rule": rule,
                    "field": field,
                    "expression": expression,
                    "error": error_msg
                })

        if expression_errors:
            print(f"✗ Expressions: {len(expression_errors)} errors")
            for err in expression_errors:
                print(f"    Step '{err['step']}', rule '{err['rule']}' ({err['field']}):")
                print(f"      Expression: {err['expression']}")
                print(f"      Error: {err['error']}")
                errors.append(
                    f"Expression error in step '{err['step']}', rule '{err['rule']}': {err['error']}"
                )
        elif validation_expressions or expression_step_expressions:
            parts = []
            if validation_expressions:
                parts.append(f"{len(validation_expressions)} validation")
            if expression_step_expressions:
                parts.append(f"{len(expression_step_expressions)} expression step")
            print(f"✓ Expressions: {', '.join(parts)}")
        else:
            print("✓ Expressions: none configured")

    # Step 7: Check item source file
    item_source_path = get_item_source_path(config, config_path)
    if item_source_path:
        if item_source_path.exists():
            print(f"✓ Item source: {item_source_path.name} exists")
        else:
            print(f"✗ Item source: {item_source_path.name} not found")
            errors.append(f"Item source not found: {item_source_path}")

    # Summary
    print()
    if errors:
        print(f"Found {len(errors)} error(s). Config is invalid.")
    else:
        print("Config is valid.")

    return {
        "valid": len(errors) == 0,
        "errors": errors,
        "warnings": warnings,
        "pipeline_steps": pipeline_steps,
        "templates_found": templates_found,
        "schemas_found": schemas_found,
        "expressions_validated": len(expressions) if ASTEVAL_AVAILABLE else 0
    }


def main():
    parser = argparse.ArgumentParser(
        description="Validate batch processing config files"
    )

    parser.add_argument(
        "--config", "-c",
        required=True,
        type=Path,
        help="Path to config.yaml file"
    )

    parser.add_argument(
        "--json",
        action="store_true",
        help="Output only JSON (suppress human-readable output)"
    )

    args = parser.parse_args()

    # Run validation
    result = validate_config_run(args.config)

    # Output JSON result
    if args.json:
        print(json.dumps(result, indent=2))

    # Exit with appropriate code
    sys.exit(0 if result["valid"] else 1)


if __name__ == "__main__":
    main()
