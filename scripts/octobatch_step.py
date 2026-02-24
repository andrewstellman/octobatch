#!/usr/bin/env python3
"""
octobatch_step.py - Pure data transformation for batch processing pipeline.

Reads JSONL from stdin, renders Jinja2 templates for the specified step,
and writes JSONL to stdout. This script is a "dumb pipe" - it knows nothing
about APIs, state, or validation.

Usage:
    cat units.jsonl | python octobatch_step.py --config config/example_config.yaml --step generate
    cat outputs.jsonl | python octobatch_step.py --config config/example_config.yaml --step validate

Input JSONL format:
    Each line is a JSON object with unit data (fields depend on your config's positions).
    Example: {"unit_id": "item1-item2-item3", "role_a": {...}, "role_b": {...}, "role_c": {...}}

Output JSONL format (all steps):
    {"prompt": "...", "metadata": {...}, "step": "generate", "unit_id": "..."}
"""

import argparse
import json
import sys
from pathlib import Path
from typing import Any

import yaml
from jinja2 import Environment, FileSystemLoader, TemplateError, StrictUndefined

from octobatch_utils import load_config, log_error


def get_template_path(config: dict, step: str) -> Path:
    """Get the template file path for the given step."""
    config_dir = Path(config.get("_config_dir", "."))
    template_dir = config_dir / config["prompts"]["template_dir"]
    template_file = config["prompts"]["templates"].get(step)

    if not template_file:
        raise ValueError(f"No template defined for step: {step}")

    return template_dir / template_file


def create_jinja_env(template_dir: Path) -> Environment:
    """Create a Jinja2 environment with the template directory."""
    return Environment(
        loader=FileSystemLoader(template_dir),
        # Don't auto-escape - we're generating prompts, not HTML
        autoescape=False,
        # Keep trailing newlines
        keep_trailing_newline=True,
        # Undefined variables should error
        undefined=StrictUndefined,
    )


def extract_metadata(input_data: dict, step: str) -> dict:
    """
    Extract metadata to pass through with the rendered prompt.

    Includes unit_id, step, and any other scalar tracking fields.
    The template context already gets all input data via prepare_template_context(),
    so metadata just needs tracking fields.
    """
    metadata = {"step": step}

    # Include common tracking fields if present
    for key in ["unit_id", "triple_id", "batch_id", "chunk_id"]:
        if key in input_data:
            metadata[key] = input_data[key]

    return metadata


def prepare_template_context(input_data: dict, step: str, config: dict) -> dict:
    """
    Prepare the context dictionary for template rendering.

    This function is domain-agnostic - it passes through all input data to the
    template and lets the template decide what variables it needs.

    Args:
        input_data: The parsed JSON input line
        step: The pipeline step name (unused, kept for API compatibility)
        config: The loaded configuration

    Returns:
        Context dictionary for Jinja2 template rendering
    """
    context = {}

    # Include all input data - let templates access whatever they need
    context.update(input_data)

    # Make entire config available to templates under 'config' namespace
    # Templates can access any config section (e.g., config.items, config.pipeline)
    context["config"] = config

    # Also flatten top-level config keys for convenience
    # (backwards compatible with existing templates)
    for key, value in config.items():
        if key not in context and key != "_config_dir":
            context[key] = value

    # Add global context from config (can override config keys)
    global_context = config.get("prompts", {}).get("global_context", {})
    context.update(global_context)

    return context


def process_line(line: str, template, step: str, config: dict, line_num: int) -> dict | None:
    """
    Process a single JSONL line and return the output object.
    Returns None if the line should be skipped (e.g., empty or error).
    """
    line = line.strip()
    if not line:
        return None

    try:
        input_data = json.loads(line)
    except json.JSONDecodeError as e:
        log_error(f"Invalid JSON on line {line_num}: {e}", {"line": line[:100]})
        return None

    try:
        # Extract metadata for passthrough
        metadata = extract_metadata(input_data, step)

        # Prepare template context
        context = prepare_template_context(input_data, step, config)

        # Render the template
        prompt = template.render(**context)

        # Build output object
        output = {
            "prompt": prompt,
            "metadata": metadata,
            "step": step,
        }

        # Include unit_id for tracking through batch API round trip
        if "unit_id" in input_data:
            output["unit_id"] = input_data["unit_id"]

        return output

    except TemplateError as e:
        log_error(f"Template error on line {line_num}: {e}", {"metadata": metadata if 'metadata' in dir() else None})
        return None
    except Exception as e:
        log_error(f"Error processing line {line_num}: {e}", {"line": line[:100]})
        return None


def main():
    parser = argparse.ArgumentParser(
        description="Transform input data into prompts using Jinja2 templates.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
    # Generate prompts from units
    cat units.jsonl | python octobatch_step.py --config config/example_config.yaml --step generate

    # Validate outputs from previous step
    cat outputs.jsonl | python octobatch_step.py --config config/example_config.yaml --step validate
        """
    )

    parser.add_argument(
        "--config", "-c",
        required=True,
        type=Path,
        help="Path to config.yaml"
    )

    parser.add_argument(
        "--step", "-s",
        required=True,
        help="Pipeline step to execute (must match a step name in config)"
    )

    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Enable verbose logging to stderr"
    )

    args = parser.parse_args()

    # Load configuration
    try:
        config = load_config(args.config)
        # Store config directory for relative path resolution
        config["_config_dir"] = args.config.parent
    except Exception as e:
        log_error(f"Failed to load config: {e}")
        sys.exit(1)

    # Validate step exists in config
    pipeline_steps = config.get("pipeline", {}).get("steps", [])
    valid_step_names = [s.get("name") for s in pipeline_steps if s.get("name")]

    if args.step not in valid_step_names:
        log_error(
            f"Unknown step: '{args.step}'. Valid steps from config: {valid_step_names}"
        )
        sys.exit(1)

    # Load template
    try:
        template_path = get_template_path(config, args.step)
        template_dir = template_path.parent
        template_name = template_path.name

        env = create_jinja_env(template_dir)
        template = env.get_template(template_name)

        if args.verbose:
            print(f"Loaded template: {template_path}", file=sys.stderr)

    except Exception as e:
        log_error(f"Failed to load template for step '{args.step}': {e}")
        sys.exit(1)

    # Process stdin line by line
    processed = 0
    errors = 0

    for line_num, line in enumerate(sys.stdin, 1):
        result = process_line(line, template, args.step, config, line_num)

        if result is not None:
            print(json.dumps(result))
            processed += 1
        else:
            if line.strip():  # Don't count empty lines as errors
                errors += 1

    # Log summary to stderr
    if args.verbose or errors > 0:
        summary = {
            "step": args.step,
            "processed": processed,
            "errors": errors,
        }
        print(json.dumps({"summary": summary}), file=sys.stderr)

    # Exit with error code if there were any errors
    if errors > 0:
        sys.exit(1)


if __name__ == "__main__":
    main()
