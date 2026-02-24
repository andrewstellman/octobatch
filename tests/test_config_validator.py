"""
Tests for config_validator.py — targeting uncovered paths for 80%+ coverage.

Covers:
- validate_config: all branch paths including expressions dict validation,
  per-step provider/model overrides, repeat validation, items.source/key,
  positions for permutation/cross_product, expression-only pipeline
- get_pipeline_steps, get_chunk_scope_steps, get_expression_steps,
  get_run_scope_steps, get_step_config
- get_item_source_path, _load_item_field_mocks
- generate_mock_value: all type branches
- extract_variable_names: filtering keywords/builtins
- build_mock_context: required, types, ranges, rule expressions
- extract_expressions: validation rules (expr, when), expression steps
  (init, expressions, loop_until)
- validate_expression: success, asteval error, exception
- validate_config_run: file not found, invalid YAML, structure errors,
  template/schema file checks, 4-Point Link Rule, expression validation,
  item source checks, summary output
"""

import sys
from pathlib import Path

import pytest
import yaml

# Add scripts directory to path
sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

from config_validator import (
    validate_config,
    get_pipeline_steps,
    get_chunk_scope_steps,
    get_expression_steps,
    get_run_scope_steps,
    get_step_config,
    get_item_source_path,
    _load_item_field_mocks,
    generate_mock_value,
    extract_variable_names,
    build_mock_context,
    extract_expressions,
    validate_expression,
    validate_config_run,
)


# =============================================================================
# Fixtures
# =============================================================================

@pytest.fixture
def minimal_llm_config():
    """Minimal config with one LLM (chunk-scope) step."""
    return {
        "pipeline": {
            "steps": [{"name": "generate"}]
        },
        "processing": {
            "strategy": "direct",
            "chunk_size": 50,
            "items": {"source": "items.yaml", "key": "items"},
        },
        "prompts": {
            "template_dir": "templates",
            "templates": {"generate": "generate.jinja2"},
        },
        "schemas": {
            "schema_dir": "schemas",
            "files": {"generate": "generate.json"},
        },
        "api": {"retry": {"max_attempts": 3}},
    }


@pytest.fixture
def expr_only_config():
    """Pipeline with only expression steps (no prompts/schemas/api needed)."""
    return {
        "pipeline": {
            "steps": [
                {"name": "calc", "scope": "expression", "expressions": {"x": "1 + 2"}},
            ]
        },
        "processing": {
            "strategy": "direct",
            "chunk_size": 10,
            "items": {"source": "items.yaml", "key": "items"},
        },
    }


@pytest.fixture
def mixed_config():
    """Config with expression, LLM, and run-scope steps."""
    return {
        "pipeline": {
            "steps": [
                {"name": "setup", "scope": "expression", "expressions": {"x": "42"}},
                {"name": "generate"},
                {"name": "aggregate", "scope": "run"},
            ]
        },
        "processing": {
            "strategy": "direct",
            "chunk_size": 50,
            "items": {"source": "items.yaml", "key": "items"},
        },
        "prompts": {
            "template_dir": "templates",
            "templates": {"generate": "gen.jinja2"},
        },
        "schemas": {
            "schema_dir": "schemas",
            "files": {"generate": "gen.json"},
        },
        "api": {"retry": {"max_attempts": 3}},
        "validation": {
            "generate": {
                "required": ["result"],
                "types": {"result": "string"},
                "rules": [{"name": "check", "expr": "len(result) > 0"}],
            }
        },
    }


# =============================================================================
# validate_config — branch coverage
# =============================================================================

class TestValidateConfigBranches:
    """Test every branch in validate_config."""

    def test_missing_pipeline(self):
        errors = validate_config({"processing": {"strategy": "direct", "chunk_size": 1, "items": {"source": "a.yaml", "key": "b"}}})
        assert any("Missing 'pipeline'" in e for e in errors)

    def test_missing_pipeline_steps(self):
        errors = validate_config({"pipeline": {}, "processing": {"strategy": "direct", "chunk_size": 1, "items": {"source": "a.yaml", "key": "b"}}})
        assert any("Missing 'pipeline.steps'" in e for e in errors)

    def test_empty_pipeline_steps(self):
        errors = validate_config({"pipeline": {"steps": []}, "processing": {"strategy": "direct", "chunk_size": 1, "items": {"source": "a.yaml", "key": "b"}}})
        assert any("'pipeline.steps' is empty" in e for e in errors)

    def test_step_missing_name(self):
        errors = validate_config({
            "pipeline": {"steps": [{"scope": "chunk"}]},
            "processing": {"strategy": "direct", "chunk_size": 1, "items": {"source": "a.yaml", "key": "b"}},
        })
        assert any("missing 'name'" in e for e in errors)

    def test_invalid_scope(self):
        errors = validate_config({
            "pipeline": {"steps": [{"name": "s1", "scope": "global"}]},
            "processing": {"strategy": "direct", "chunk_size": 1, "items": {"source": "a.yaml", "key": "b"}},
        })
        assert any("invalid scope" in e.lower() for e in errors)

    def test_expression_step_missing_expressions_block(self):
        errors = validate_config({
            "pipeline": {"steps": [{"name": "s1", "scope": "expression"}]},
            "processing": {"strategy": "direct", "chunk_size": 1, "items": {"source": "a.yaml", "key": "b"}},
        })
        assert any("missing 'expressions' block" in e for e in errors)

    def test_expression_step_non_dict_expressions(self):
        errors = validate_config({
            "pipeline": {"steps": [{"name": "s1", "scope": "expression", "expressions": [1, 2]}]},
            "processing": {"strategy": "direct", "chunk_size": 1, "items": {"source": "a.yaml", "key": "b"}},
        })
        assert any("expressions must be a dict" in e for e in errors)

    def test_expression_step_empty_expressions(self):
        errors = validate_config({
            "pipeline": {"steps": [{"name": "s1", "scope": "expression", "expressions": {}}]},
            "processing": {"strategy": "direct", "chunk_size": 1, "items": {"source": "a.yaml", "key": "b"}},
        })
        assert any("expressions block is empty" in e for e in errors)

    def test_valid_provider_override_gemini(self):
        errors = validate_config({
            "pipeline": {"steps": [{"name": "s1", "scope": "expression", "expressions": {"x": "1"}, "provider": "gemini"}]},
            "processing": {"strategy": "direct", "chunk_size": 1, "items": {"source": "a.yaml", "key": "b"}},
        })
        assert not any("provider" in e.lower() for e in errors)

    def test_valid_provider_override_openai(self):
        errors = validate_config({
            "pipeline": {"steps": [{"name": "s1", "scope": "expression", "expressions": {"x": "1"}, "provider": "openai"}]},
            "processing": {"strategy": "direct", "chunk_size": 1, "items": {"source": "a.yaml", "key": "b"}},
        })
        assert not any("provider" in e.lower() for e in errors)

    def test_valid_provider_override_anthropic(self):
        errors = validate_config({
            "pipeline": {"steps": [{"name": "s1", "scope": "expression", "expressions": {"x": "1"}, "provider": "Anthropic"}]},
            "processing": {"strategy": "direct", "chunk_size": 1, "items": {"source": "a.yaml", "key": "b"}},
        })
        assert not any("provider" in e.lower() for e in errors)

    def test_invalid_provider_override(self):
        errors = validate_config({
            "pipeline": {"steps": [{"name": "s1", "scope": "expression", "expressions": {"x": "1"}, "provider": "azure"}]},
            "processing": {"strategy": "direct", "chunk_size": 1, "items": {"source": "a.yaml", "key": "b"}},
        })
        assert any("invalid provider" in e.lower() for e in errors)

    def test_non_string_provider(self):
        errors = validate_config({
            "pipeline": {"steps": [{"name": "s1", "scope": "expression", "expressions": {"x": "1"}, "provider": 123}]},
            "processing": {"strategy": "direct", "chunk_size": 1, "items": {"source": "a.yaml", "key": "b"}},
        })
        assert any("invalid provider" in e.lower() for e in errors)

    def test_valid_model_string(self):
        errors = validate_config({
            "pipeline": {"steps": [{"name": "s1", "scope": "expression", "expressions": {"x": "1"}, "model": "gpt-4"}]},
            "processing": {"strategy": "direct", "chunk_size": 1, "items": {"source": "a.yaml", "key": "b"}},
        })
        assert not any("model" in e.lower() for e in errors)

    def test_non_string_model(self):
        errors = validate_config({
            "pipeline": {"steps": [{"name": "s1", "scope": "expression", "expressions": {"x": "1"}, "model": 42}]},
            "processing": {"strategy": "direct", "chunk_size": 1, "items": {"source": "a.yaml", "key": "b"}},
        })
        assert any("model must be a string" in e for e in errors)

    def test_missing_processing(self):
        errors = validate_config({
            "pipeline": {"steps": [{"name": "s1", "scope": "expression", "expressions": {"x": "1"}}]},
        })
        assert any("Missing 'processing'" in e for e in errors)

    def test_invalid_strategy(self):
        errors = validate_config({
            "pipeline": {"steps": [{"name": "s1", "scope": "expression", "expressions": {"x": "1"}}]},
            "processing": {"strategy": "random", "chunk_size": 1, "items": {"source": "a.yaml", "key": "b"}},
        })
        assert any("Invalid 'processing.strategy'" in e for e in errors)

    def test_missing_chunk_size(self):
        errors = validate_config({
            "pipeline": {"steps": [{"name": "s1", "scope": "expression", "expressions": {"x": "1"}}]},
            "processing": {"strategy": "direct", "items": {"source": "a.yaml", "key": "b"}},
        })
        assert any("Missing 'processing.chunk_size'" in e for e in errors)

    def test_permutation_requires_positions(self):
        errors = validate_config({
            "pipeline": {"steps": [{"name": "s1", "scope": "expression", "expressions": {"x": "1"}}]},
            "processing": {"strategy": "permutation", "chunk_size": 1, "items": {"source": "a.yaml", "key": "b"}},
        })
        assert any("positions" in e.lower() and "permutation" in e.lower() for e in errors)

    def test_cross_product_requires_positions(self):
        errors = validate_config({
            "pipeline": {"steps": [{"name": "s1", "scope": "expression", "expressions": {"x": "1"}}]},
            "processing": {"strategy": "cross_product", "chunk_size": 1, "items": {"source": "a.yaml", "key": "b"}},
        })
        assert any("positions" in e.lower() and "cross_product" in e.lower() for e in errors)

    def test_permutation_with_positions_is_valid(self):
        errors = validate_config({
            "pipeline": {"steps": [{"name": "s1", "scope": "expression", "expressions": {"x": "1"}}]},
            "processing": {
                "strategy": "permutation",
                "chunk_size": 1,
                "positions": [{"name": "p1"}],
                "items": {"source": "a.yaml", "key": "b"},
            },
        })
        assert not any("positions" in e.lower() for e in errors)

    def test_missing_items(self):
        errors = validate_config({
            "pipeline": {"steps": [{"name": "s1", "scope": "expression", "expressions": {"x": "1"}}]},
            "processing": {"strategy": "direct", "chunk_size": 1},
        })
        assert any("items" in e.lower() for e in errors)

    def test_missing_items_source_and_key(self):
        errors = validate_config({
            "pipeline": {"steps": [{"name": "s1", "scope": "expression", "expressions": {"x": "1"}}]},
            "processing": {"strategy": "direct", "chunk_size": 1, "items": {}},
        })
        assert any("items.source" in e.lower() or "items.key" in e.lower() for e in errors)

    def test_items_with_only_key_is_valid(self):
        """Self-referential config using only 'key' is valid."""
        errors = validate_config({
            "pipeline": {"steps": [{"name": "s1", "scope": "expression", "expressions": {"x": "1"}}]},
            "processing": {"strategy": "direct", "chunk_size": 1, "items": {"key": "scenarios"}},
        })
        assert not any("items.source" in e.lower() for e in errors)

    def test_repeat_non_integer(self):
        errors = validate_config({
            "pipeline": {"steps": [{"name": "s1", "scope": "expression", "expressions": {"x": "1"}}]},
            "processing": {"strategy": "direct", "chunk_size": 1, "items": {"source": "a.yaml", "key": "b"}, "repeat": "five"},
        })
        assert any("repeat must be an integer" in e for e in errors)

    def test_repeat_zero(self):
        errors = validate_config({
            "pipeline": {"steps": [{"name": "s1", "scope": "expression", "expressions": {"x": "1"}}]},
            "processing": {"strategy": "direct", "chunk_size": 1, "items": {"source": "a.yaml", "key": "b"}, "repeat": 0},
        })
        assert any("repeat must be at least 1" in e for e in errors)

    def test_repeat_negative(self):
        errors = validate_config({
            "pipeline": {"steps": [{"name": "s1", "scope": "expression", "expressions": {"x": "1"}}]},
            "processing": {"strategy": "direct", "chunk_size": 1, "items": {"source": "a.yaml", "key": "b"}, "repeat": -3},
        })
        assert any("repeat must be at least 1" in e for e in errors)

    def test_repeat_valid(self):
        errors = validate_config({
            "pipeline": {"steps": [{"name": "s1", "scope": "expression", "expressions": {"x": "1"}}]},
            "processing": {"strategy": "direct", "chunk_size": 1, "items": {"source": "a.yaml", "key": "b"}, "repeat": 5},
        })
        assert not any("repeat" in e.lower() for e in errors)

    def test_expressions_dict_non_dict(self):
        errors = validate_config({
            "pipeline": {"steps": [{"name": "s1", "scope": "expression", "expressions": {"x": "1"}}]},
            "processing": {
                "strategy": "direct", "chunk_size": 1,
                "items": {"source": "a.yaml", "key": "b"},
                "expressions": "not_a_dict",
            },
        })
        assert any("processing.expressions must be a dict" in e for e in errors)

    def test_expressions_dict_non_string_expr(self):
        errors = validate_config({
            "pipeline": {"steps": [{"name": "s1", "scope": "expression", "expressions": {"x": "1"}}]},
            "processing": {
                "strategy": "direct", "chunk_size": 1,
                "items": {"source": "a.yaml", "key": "b"},
                "expressions": {"calc": 42},
            },
        })
        assert any("must be a string" in e for e in errors)

    def test_expressions_dict_invalid_syntax(self):
        errors = validate_config({
            "pipeline": {"steps": [{"name": "s1", "scope": "expression", "expressions": {"x": "1"}}]},
            "processing": {
                "strategy": "direct", "chunk_size": 1,
                "items": {"source": "a.yaml", "key": "b"},
                "expressions": {"bad": "def nope():"},
            },
        })
        assert any("is invalid" in e.lower() for e in errors)

    def test_expressions_dict_valid(self):
        errors = validate_config({
            "pipeline": {"steps": [{"name": "s1", "scope": "expression", "expressions": {"x": "1"}}]},
            "processing": {
                "strategy": "direct", "chunk_size": 1,
                "items": {"source": "a.yaml", "key": "b"},
                "expressions": {"val": "1 + 2"},
            },
        })
        assert not any("expression" in e.lower() and "invalid" in e.lower() for e in errors)

    def test_llm_steps_require_prompts_schemas_api(self):
        """Chunk-scope step (LLM) requires prompts, schemas, api."""
        errors = validate_config({
            "pipeline": {"steps": [{"name": "gen"}]},
            "processing": {"strategy": "direct", "chunk_size": 1, "items": {"source": "a.yaml", "key": "b"}},
        })
        assert any("prompts" in e.lower() for e in errors)
        assert any("schemas" in e.lower() for e in errors)
        assert any("api" in e.lower() for e in errors)

    def test_expression_only_pipeline_no_prompts_needed(self, expr_only_config):
        """Expression-only pipeline skips prompts/schemas/api requirement."""
        errors = validate_config(expr_only_config)
        assert not any("prompts" in e.lower() for e in errors)
        assert not any("schemas" in e.lower() for e in errors)
        assert not any("api" in e.lower() for e in errors)

    def test_run_scope_steps_not_llm(self):
        """Run-scope steps are not LLM steps, so no prompts needed if no chunk steps."""
        errors = validate_config({
            "pipeline": {"steps": [
                {"name": "agg", "scope": "run"},
                {"name": "calc", "scope": "expression", "expressions": {"x": "1"}},
            ]},
            "processing": {"strategy": "direct", "chunk_size": 1, "items": {"source": "a.yaml", "key": "b"}},
        })
        assert not any("prompts" in e.lower() for e in errors)
        assert not any("schemas" in e.lower() for e in errors)
        assert not any("api" in e.lower() for e in errors)

    def test_multiple_errors_accumulated(self):
        """Multiple errors are all accumulated."""
        errors = validate_config({})
        assert len(errors) >= 2  # At least missing pipeline and processing


# =============================================================================
# get_pipeline_steps
# =============================================================================

class TestGetPipelineSteps:

    def test_returns_names_in_order(self):
        config = {"pipeline": {"steps": [{"name": "a"}, {"name": "b"}, {"name": "c"}]}}
        assert get_pipeline_steps(config) == ["a", "b", "c"]

    def test_empty_config(self):
        assert get_pipeline_steps({}) == []

    def test_empty_pipeline(self):
        assert get_pipeline_steps({"pipeline": {}}) == []

    def test_steps_without_name_skipped(self):
        config = {"pipeline": {"steps": [{"name": "a"}, {"scope": "chunk"}, {"name": "c"}]}}
        assert get_pipeline_steps(config) == ["a", "c"]


# =============================================================================
# get_chunk_scope_steps
# =============================================================================

class TestGetChunkScopeSteps:

    def test_includes_chunk_and_expression(self):
        config = {"pipeline": {"steps": [
            {"name": "a"},
            {"name": "b", "scope": "expression"},
            {"name": "c", "scope": "run"},
        ]}}
        result = get_chunk_scope_steps(config)
        assert "a" in result
        assert "b" in result
        assert "c" not in result

    def test_default_scope_is_chunk(self):
        config = {"pipeline": {"steps": [{"name": "a"}]}}
        assert get_chunk_scope_steps(config) == ["a"]

    def test_empty_config(self):
        assert get_chunk_scope_steps({}) == []


# =============================================================================
# get_expression_steps
# =============================================================================

class TestGetExpressionSteps:

    def test_returns_expression_steps_only(self):
        config = {"pipeline": {"steps": [
            {"name": "a", "scope": "expression", "expressions": {"x": "1"}},
            {"name": "b"},
            {"name": "c", "scope": "expression", "expressions": {"y": "2"}},
        ]}}
        result = get_expression_steps(config)
        names = [s["name"] for s in result]
        assert names == ["a", "c"]

    def test_empty_config(self):
        assert get_expression_steps({}) == []

    def test_no_expression_steps(self):
        config = {"pipeline": {"steps": [{"name": "a"}, {"name": "b"}]}}
        assert get_expression_steps(config) == []


# =============================================================================
# get_run_scope_steps
# =============================================================================

class TestGetRunScopeSteps:

    def test_returns_run_steps_only(self):
        config = {"pipeline": {"steps": [
            {"name": "a"},
            {"name": "b", "scope": "run"},
            {"name": "c", "scope": "expression", "expressions": {"x": "1"}},
        ]}}
        result = get_run_scope_steps(config)
        assert len(result) == 1
        assert result[0]["name"] == "b"

    def test_empty_config(self):
        assert get_run_scope_steps({}) == []


# =============================================================================
# get_step_config
# =============================================================================

class TestGetStepConfig:

    def test_found(self):
        config = {"pipeline": {"steps": [{"name": "a", "scope": "run"}, {"name": "b"}]}}
        result = get_step_config(config, "a")
        assert result is not None
        assert result["name"] == "a"
        assert result["scope"] == "run"

    def test_not_found_returns_none(self):
        config = {"pipeline": {"steps": [{"name": "a"}]}}
        assert get_step_config(config, "z") is None

    def test_empty_config(self):
        assert get_step_config({}, "anything") is None


# =============================================================================
# get_item_source_path
# =============================================================================

class TestGetItemSourcePath:

    def test_resolves_relative_to_config_dir(self, tmp_path):
        config = {"processing": {"items": {"source": "data/items.yaml"}}}
        config_path = tmp_path / "configs" / "pipeline.yaml"
        result = get_item_source_path(config, config_path)
        assert result == tmp_path / "configs" / "data" / "items.yaml"

    def test_self_referential_returns_none(self, tmp_path):
        config = {"processing": {"items": {"key": "scenarios"}}}
        config_path = tmp_path / "config.yaml"
        assert get_item_source_path(config, config_path) is None

    def test_empty_items_returns_none(self, tmp_path):
        config = {"processing": {"items": {}}}
        config_path = tmp_path / "config.yaml"
        assert get_item_source_path(config, config_path) is None

    def test_no_processing_returns_none(self, tmp_path):
        config = {}
        config_path = tmp_path / "config.yaml"
        assert get_item_source_path(config, config_path) is None


# =============================================================================
# _load_item_field_mocks
# =============================================================================

class TestLoadItemFieldMocks:

    def test_loads_first_item_fields(self, tmp_path):
        """Loads fields from first item in source file."""
        items_file = tmp_path / "items.yaml"
        items_file.write_text(yaml.dump({
            "scenarios": [
                {"id": "s1", "difficulty": 3, "name": "Easy"},
                {"id": "s2", "difficulty": 7, "name": "Hard"},
            ]
        }))
        config = {"processing": {"items": {"source": "items.yaml", "key": "scenarios"}}}
        config_path = tmp_path / "config.yaml"
        result = _load_item_field_mocks(config, config_path)
        assert result == {"id": "s1", "difficulty": 3, "name": "Easy"}

    def test_returns_empty_when_no_source(self, tmp_path):
        """Returns empty dict when no item source path."""
        config = {"processing": {"items": {"key": "items"}}}
        config_path = tmp_path / "config.yaml"
        result = _load_item_field_mocks(config, config_path)
        assert result == {}

    def test_returns_empty_when_file_missing(self, tmp_path):
        """Returns empty dict when source file does not exist."""
        config = {"processing": {"items": {"source": "missing.yaml", "key": "items"}}}
        config_path = tmp_path / "config.yaml"
        result = _load_item_field_mocks(config, config_path)
        assert result == {}

    def test_returns_empty_when_yaml_error(self, tmp_path):
        """Returns empty dict when YAML is invalid."""
        bad_file = tmp_path / "bad.yaml"
        bad_file.write_text(": : :\n  - [invalid\nyaml")
        config = {"processing": {"items": {"source": "bad.yaml", "key": "items"}}}
        config_path = tmp_path / "config.yaml"
        result = _load_item_field_mocks(config, config_path)
        assert result == {}

    def test_returns_empty_when_data_not_dict(self, tmp_path):
        """Returns empty dict when YAML root is not a dict."""
        items_file = tmp_path / "items.yaml"
        items_file.write_text("- one\n- two\n")
        config = {"processing": {"items": {"source": "items.yaml", "key": "items"}}}
        config_path = tmp_path / "config.yaml"
        result = _load_item_field_mocks(config, config_path)
        assert result == {}

    def test_returns_empty_when_key_missing(self, tmp_path):
        """Returns empty dict when items key not in data."""
        items_file = tmp_path / "items.yaml"
        items_file.write_text(yaml.dump({"other_key": [{"id": 1}]}))
        config = {"processing": {"items": {"source": "items.yaml", "key": "scenarios"}}}
        config_path = tmp_path / "config.yaml"
        result = _load_item_field_mocks(config, config_path)
        assert result == {}

    def test_returns_empty_when_key_empty_string(self, tmp_path):
        """Returns empty dict when items key is empty string."""
        items_file = tmp_path / "items.yaml"
        items_file.write_text(yaml.dump({"items": [{"id": 1}]}))
        config = {"processing": {"items": {"source": "items.yaml", "key": ""}}}
        config_path = tmp_path / "config.yaml"
        result = _load_item_field_mocks(config, config_path)
        assert result == {}

    def test_returns_empty_when_items_list_empty(self, tmp_path):
        """Returns empty dict when items list is empty."""
        items_file = tmp_path / "items.yaml"
        items_file.write_text(yaml.dump({"scenarios": []}))
        config = {"processing": {"items": {"source": "items.yaml", "key": "scenarios"}}}
        config_path = tmp_path / "config.yaml"
        result = _load_item_field_mocks(config, config_path)
        assert result == {}

    def test_returns_empty_when_items_not_list(self, tmp_path):
        """Returns empty dict when items value is not a list."""
        items_file = tmp_path / "items.yaml"
        items_file.write_text(yaml.dump({"scenarios": "not_a_list"}))
        config = {"processing": {"items": {"source": "items.yaml", "key": "scenarios"}}}
        config_path = tmp_path / "config.yaml"
        result = _load_item_field_mocks(config, config_path)
        assert result == {}

    def test_returns_empty_when_first_item_not_dict(self, tmp_path):
        """Returns empty dict when first item is not a dict."""
        items_file = tmp_path / "items.yaml"
        items_file.write_text(yaml.dump({"scenarios": ["just_a_string", "another"]}))
        config = {"processing": {"items": {"source": "items.yaml", "key": "scenarios"}}}
        config_path = tmp_path / "config.yaml"
        result = _load_item_field_mocks(config, config_path)
        assert result == {}


# =============================================================================
# generate_mock_value — all type branches
# =============================================================================

class TestGenerateMockValue:

    def test_integer_with_range(self):
        val = generate_mock_value("score", {"types": {"score": "integer"}, "ranges": {"score": [10, 20]}})
        assert val == 15

    def test_integer_without_range(self):
        val = generate_mock_value("count", {"types": {"count": "integer"}, "ranges": {}})
        assert val == 5

    def test_integer_with_malformed_range(self):
        """Integer with range that isn't a 2-element list falls back to default."""
        val = generate_mock_value("x", {"types": {"x": "integer"}, "ranges": {"x": [10]}})
        assert val == 5

    def test_integer_with_non_list_range(self):
        """Integer with non-list range falls back to default."""
        val = generate_mock_value("x", {"types": {"x": "integer"}, "ranges": {"x": "bad"}})
        assert val == 5

    def test_number_type(self):
        val = generate_mock_value("rate", {"types": {"rate": "number"}, "ranges": {}})
        assert val == 5.0
        assert isinstance(val, float)

    def test_string_type(self):
        val = generate_mock_value("name", {"types": {"name": "string"}, "ranges": {}})
        assert val == "sample"

    def test_array_type(self):
        val = generate_mock_value("items", {"types": {"items": "array"}, "ranges": {}})
        assert isinstance(val, list)
        assert len(val) == 2
        assert isinstance(val[0], dict)
        assert val[0]["type"] == "a"

    def test_object_type(self):
        val = generate_mock_value("data", {"types": {"data": "object"}, "ranges": {}})
        assert isinstance(val, dict)
        assert "key1" in val

    def test_boolean_type(self):
        val = generate_mock_value("flag", {"types": {"flag": "boolean"}, "ranges": {}})
        assert val is True

    def test_unknown_type(self):
        val = generate_mock_value("mystery", {"types": {}, "ranges": {}})
        assert val == "mock_value"

    def test_explicit_unknown_type(self):
        """Field with an unrecognized type string."""
        val = generate_mock_value("x", {"types": {"x": "custom_type"}, "ranges": {}})
        assert val == "mock_value"


# =============================================================================
# extract_variable_names
# =============================================================================

class TestExtractVariableNames:

    def test_simple_variables(self):
        result = extract_variable_names("x + y * z")
        assert result == {"x", "y", "z"}

    def test_filters_python_keywords(self):
        result = extract_variable_names("x and y or not z if True else False")
        assert "and" not in result
        assert "or" not in result
        assert "not" not in result
        assert "if" not in result
        assert "else" not in result
        assert "True" not in result
        assert "False" not in result
        assert {"x", "y", "z"} <= result

    def test_filters_builtins(self):
        result = extract_variable_names("len(data) + sum(values) + min(scores)")
        assert "len" not in result
        assert "sum" not in result
        assert "min" not in result
        assert "data" in result
        assert "scores" in result

    def test_filters_method_names(self):
        result = extract_variable_names("d.get('key')")
        assert "get" not in result
        assert "d" in result

    def test_underscored_names(self):
        result = extract_variable_names("_computed + my_var")
        assert "_computed" in result
        assert "my_var" in result

    def test_empty_expression(self):
        result = extract_variable_names("")
        assert result == set()

    def test_numeric_literal_only(self):
        result = extract_variable_names("42 + 3.14")
        assert result == set()

    def test_none_keyword(self):
        result = extract_variable_names("x is None")
        assert "None" not in result
        assert "x" in result


# =============================================================================
# build_mock_context
# =============================================================================

class TestBuildMockContext:

    def test_from_required_fields(self):
        step_config = {"required": ["name", "score"]}
        ctx = build_mock_context(step_config)
        assert "name" in ctx
        assert "score" in ctx
        assert "_computed" in ctx

    def test_from_types(self):
        step_config = {"types": {"age": "integer", "label": "string"}}
        ctx = build_mock_context(step_config)
        assert isinstance(ctx["age"], int)
        assert isinstance(ctx["label"], str)

    def test_from_ranges(self):
        step_config = {"types": {"val": "integer"}, "ranges": {"val": [0, 100]}}
        ctx = build_mock_context(step_config)
        assert ctx["val"] == 50

    def test_ranges_without_types_adds_field(self):
        """Field in ranges but not types or required still gets added."""
        step_config = {"ranges": {"extra": [5, 15]}}
        ctx = build_mock_context(step_config)
        assert "extra" in ctx

    def test_from_rule_expressions(self):
        step_config = {
            "rules": [
                {"name": "check", "expr": "total > 0", "when": "has_data == True"},
            ]
        }
        ctx = build_mock_context(step_config)
        assert "total" in ctx
        assert "has_data" in ctx

    def test_no_duplicate_from_required_and_types(self):
        """Field in both required and types is only generated once."""
        step_config = {
            "required": ["score"],
            "types": {"score": "integer"},
        }
        ctx = build_mock_context(step_config)
        assert "score" in ctx
        assert isinstance(ctx["score"], int)

    def test_empty_config(self):
        ctx = build_mock_context({})
        assert "_computed" in ctx
        assert len(ctx) == 1

    def test_always_includes_computed(self):
        ctx = build_mock_context({"required": ["x"]})
        assert ctx["_computed"] == 0


# =============================================================================
# extract_expressions
# =============================================================================

class TestExtractExpressions:

    def test_from_validation_rules_expr_and_when(self):
        config = {
            "validation": {
                "step1": {
                    "rules": [
                        {"name": "r1", "expr": "x > 0", "when": "x is not None"},
                    ]
                }
            }
        }
        result = extract_expressions(config)
        assert len(result) == 2
        fields = {e["field"] for e in result}
        assert fields == {"expr", "when"}

    def test_from_validation_rules_expr_only(self):
        config = {
            "validation": {
                "step1": {
                    "rules": [{"name": "r1", "expr": "x > 0"}]
                }
            }
        }
        result = extract_expressions(config)
        assert len(result) == 1
        assert result[0]["field"] == "expr"

    def test_validation_non_dict_step_config_skipped(self):
        """Non-dict step config in validation is skipped."""
        config = {
            "validation": {
                "step1": "not_a_dict",
            }
        }
        result = extract_expressions(config)
        assert result == []

    def test_unnamed_rule_gets_unnamed_label(self):
        config = {
            "validation": {
                "step1": {
                    "rules": [{"expr": "x > 0"}]
                }
            }
        }
        result = extract_expressions(config)
        assert result[0]["rule"] == "unnamed"

    def test_from_expression_step_init(self):
        config = {
            "pipeline": {"steps": [{
                "name": "calc",
                "scope": "expression",
                "init": {"counter": "0", "total": "100"},
                "expressions": {"val": "counter + 1"},
            }]}
        }
        result = extract_expressions(config)
        init_exprs = [e for e in result if e["field"] == "expression_step_init"]
        assert len(init_exprs) == 2

    def test_from_expression_step_expressions(self):
        config = {
            "pipeline": {"steps": [{
                "name": "calc",
                "scope": "expression",
                "expressions": {"a": "1", "b": "2"},
            }]}
        }
        result = extract_expressions(config)
        step_exprs = [e for e in result if e["field"] == "expression_step"]
        assert len(step_exprs) == 2

    def test_from_expression_step_loop_until(self):
        config = {
            "pipeline": {"steps": [{
                "name": "sim",
                "scope": "expression",
                "expressions": {"x": "1"},
                "loop_until": "x >= 10",
            }]}
        }
        result = extract_expressions(config)
        loop_exprs = [e for e in result if e["field"] == "expression_step_loop_until"]
        assert len(loop_exprs) == 1
        assert loop_exprs[0]["expression"] == "x >= 10"

    def test_non_string_init_value_skipped(self):
        """Non-string init values are skipped."""
        config = {
            "pipeline": {"steps": [{
                "name": "calc",
                "scope": "expression",
                "init": {"counter": 0},  # int, not string
                "expressions": {"val": "1"},
            }]}
        }
        result = extract_expressions(config)
        init_exprs = [e for e in result if e["field"] == "expression_step_init"]
        assert len(init_exprs) == 0

    def test_non_string_expression_value_skipped(self):
        """Non-string expression values are skipped."""
        config = {
            "pipeline": {"steps": [{
                "name": "calc",
                "scope": "expression",
                "expressions": {"val": 42},  # int, not string
            }]}
        }
        result = extract_expressions(config)
        step_exprs = [e for e in result if e["field"] == "expression_step"]
        assert len(step_exprs) == 0

    def test_non_string_loop_until_skipped(self):
        """Non-string loop_until is skipped."""
        config = {
            "pipeline": {"steps": [{
                "name": "sim",
                "scope": "expression",
                "expressions": {"x": "1"},
                "loop_until": 10,  # int, not string
            }]}
        }
        result = extract_expressions(config)
        loop_exprs = [e for e in result if e["field"] == "expression_step_loop_until"]
        assert len(loop_exprs) == 0

    def test_unnamed_expression_step(self):
        """Expression step without name gets 'unnamed' label."""
        config = {
            "pipeline": {"steps": [{
                "scope": "expression",
                "expressions": {"x": "1"},
            }]}
        }
        result = extract_expressions(config)
        assert len(result) == 1
        assert result[0]["step"] == "unnamed"

    def test_empty_config(self):
        result = extract_expressions({})
        assert result == []

    def test_no_validation_no_expression_steps(self):
        config = {
            "pipeline": {"steps": [{"name": "gen"}]},
        }
        result = extract_expressions(config)
        assert result == []

    def test_multiple_validation_steps(self):
        config = {
            "validation": {
                "s1": {"rules": [{"name": "r1", "expr": "a > 0"}]},
                "s2": {"rules": [{"name": "r2", "expr": "b > 0", "when": "True"}]},
            }
        }
        result = extract_expressions(config)
        assert len(result) == 3  # 1 from s1 + 2 from s2


# =============================================================================
# validate_expression
# =============================================================================

class TestValidateExpression:

    @pytest.fixture
    def aeval(self):
        from expression_evaluator import create_validation_interpreter
        return create_validation_interpreter()

    def test_success(self, aeval):
        success, err = validate_expression(aeval, "1 + 2", {})
        assert success is True
        assert err == ""

    def test_success_with_context(self, aeval):
        success, err = validate_expression(aeval, "x + y", {"x": 10, "y": 20})
        assert success is True
        assert err == ""

    def test_asteval_error(self, aeval):
        """Expression that triggers an asteval error (undefined name)."""
        success, err = validate_expression(aeval, "undefined_var_xyz", {})
        assert success is False
        assert len(err) > 0

    def test_context_makes_previously_undefined_valid(self, aeval):
        """Variable becomes valid when provided in context."""
        success, _ = validate_expression(aeval, "my_var + 1", {"my_var": 5})
        assert success is True


# =============================================================================
# validate_config_run — full integration
# =============================================================================

class TestValidateConfigRun:

    def test_file_not_found(self, tmp_path):
        result = validate_config_run(tmp_path / "nonexistent.yaml")
        assert result["valid"] is False
        assert any("not found" in e for e in result["errors"])

    def test_invalid_yaml(self, tmp_path):
        bad_file = tmp_path / "bad.yaml"
        bad_file.write_text(":\n  - [invalid\nyaml: {unclosed")
        result = validate_config_run(bad_file)
        assert result["valid"] is False
        assert any("YAML" in e or "yaml" in e for e in result["errors"])

    def test_structure_errors(self, tmp_path):
        """Config with missing required sections reports structure errors."""
        cfg = tmp_path / "config.yaml"
        cfg.write_text(yaml.dump({"pipeline": {"steps": []}}))
        result = validate_config_run(cfg)
        assert result["valid"] is False
        assert len(result["errors"]) > 0

    def test_valid_config_with_templates_and_schemas(self, tmp_path):
        """Valid config with existing template and schema files."""
        template_dir = tmp_path / "templates"
        template_dir.mkdir()
        schema_dir = tmp_path / "schemas"
        schema_dir.mkdir()

        (template_dir / "gen.jinja2").write_text("{{ content }}")
        (schema_dir / "gen.json").write_text("{}")

        items_file = tmp_path / "items.yaml"
        items_file.write_text(yaml.dump({"items": [{"id": 1}]}))

        config = {
            "pipeline": {"steps": [{"name": "gen"}]},
            "processing": {
                "strategy": "direct",
                "chunk_size": 50,
                "items": {"source": "items.yaml", "key": "items"},
            },
            "prompts": {
                "template_dir": "templates",
                "templates": {"gen": "gen.jinja2"},
            },
            "schemas": {
                "schema_dir": "schemas",
                "files": {"gen": "gen.json"},
            },
            "api": {"retry": {"max_attempts": 3}},
            "validation": {
                "gen": {
                    "required": ["result"],
                    "types": {"result": "string"},
                    "rules": [{"name": "check", "expr": "len(result) > 0"}],
                },
            },
        }
        cfg_file = tmp_path / "config.yaml"
        cfg_file.write_text(yaml.dump(config))
        result = validate_config_run(cfg_file)
        assert result["valid"] is True
        assert result["templates_found"] == 1
        assert result["schemas_found"] == 1
        assert result["expressions_validated"] > 0

    def test_missing_template_file(self, tmp_path):
        """Template file that does not exist."""
        template_dir = tmp_path / "templates"
        template_dir.mkdir()
        schema_dir = tmp_path / "schemas"
        schema_dir.mkdir()
        (schema_dir / "gen.json").write_text("{}")

        config = {
            "pipeline": {"steps": [{"name": "gen"}]},
            "processing": {
                "strategy": "direct",
                "chunk_size": 50,
                "items": {"source": "items.yaml", "key": "items"},
            },
            "prompts": {
                "template_dir": "templates",
                "templates": {"gen": "missing_template.jinja2"},
            },
            "schemas": {
                "schema_dir": "schemas",
                "files": {"gen": "gen.json"},
            },
            "api": {"retry": {"max_attempts": 3}},
            "validation": {
                "gen": {"rules": []},
            },
        }
        cfg_file = tmp_path / "config.yaml"
        cfg_file.write_text(yaml.dump(config))
        result = validate_config_run(cfg_file)
        assert result["valid"] is False
        assert any("Template not found" in e for e in result["errors"])

    def test_missing_schema_file(self, tmp_path):
        """Schema file that does not exist."""
        template_dir = tmp_path / "templates"
        template_dir.mkdir()
        schema_dir = tmp_path / "schemas"
        schema_dir.mkdir()
        (template_dir / "gen.jinja2").write_text("{{ content }}")

        config = {
            "pipeline": {"steps": [{"name": "gen"}]},
            "processing": {
                "strategy": "direct",
                "chunk_size": 50,
                "items": {"source": "items.yaml", "key": "items"},
            },
            "prompts": {
                "template_dir": "templates",
                "templates": {"gen": "gen.jinja2"},
            },
            "schemas": {
                "schema_dir": "schemas",
                "files": {"gen": "missing_schema.json"},
            },
            "api": {"retry": {"max_attempts": 3}},
            "validation": {
                "gen": {"rules": []},
            },
        }
        cfg_file = tmp_path / "config.yaml"
        cfg_file.write_text(yaml.dump(config))
        result = validate_config_run(cfg_file)
        assert result["valid"] is False
        assert any("Schema not found" in e for e in result["errors"])

    def test_no_templates_configured(self, tmp_path):
        """Config with no templates configured (expression-only pipeline)."""
        config = {
            "pipeline": {"steps": [
                {"name": "calc", "scope": "expression", "expressions": {"x": "1 + 2"}},
            ]},
            "processing": {
                "strategy": "direct",
                "chunk_size": 10,
                "items": {"source": "items.yaml", "key": "items"},
            },
        }
        cfg_file = tmp_path / "config.yaml"
        cfg_file.write_text(yaml.dump(config))
        result = validate_config_run(cfg_file)
        assert result["templates_found"] == 0

    def test_no_schemas_configured(self, tmp_path):
        """Config with no schemas configured (expression-only pipeline)."""
        config = {
            "pipeline": {"steps": [
                {"name": "calc", "scope": "expression", "expressions": {"x": "1 + 2"}},
            ]},
            "processing": {
                "strategy": "direct",
                "chunk_size": 10,
                "items": {"source": "items.yaml", "key": "items"},
            },
        }
        cfg_file = tmp_path / "config.yaml"
        cfg_file.write_text(yaml.dump(config))
        result = validate_config_run(cfg_file)
        assert result["schemas_found"] == 0

    def test_four_point_link_rule_violation(self, tmp_path):
        """LLM step missing template/schema/validation triggers 4-Point Link Rule."""
        config = {
            "pipeline": {"steps": [{"name": "gen"}]},
            "processing": {
                "strategy": "direct",
                "chunk_size": 50,
                "items": {"source": "items.yaml", "key": "items"},
            },
            "prompts": {"template_dir": "templates", "templates": {}},
            "schemas": {"schema_dir": "schemas", "files": {}},
            "api": {"retry": {"max_attempts": 3}},
        }
        cfg_file = tmp_path / "config.yaml"
        cfg_file.write_text(yaml.dump(config))
        result = validate_config_run(cfg_file)
        assert result["valid"] is False
        assert any("4-Point Link Rule" in e for e in result["errors"])

    def test_four_point_link_rule_passes(self, tmp_path):
        """All LLM steps have template, schema, and validation."""
        template_dir = tmp_path / "templates"
        template_dir.mkdir()
        schema_dir = tmp_path / "schemas"
        schema_dir.mkdir()
        (template_dir / "gen.jinja2").write_text("{{ content }}")
        (schema_dir / "gen.json").write_text("{}")

        config = {
            "pipeline": {"steps": [{"name": "gen"}]},
            "processing": {
                "strategy": "direct",
                "chunk_size": 50,
                "items": {"source": "items.yaml", "key": "items"},
            },
            "prompts": {"template_dir": "templates", "templates": {"gen": "gen.jinja2"}},
            "schemas": {"schema_dir": "schemas", "files": {"gen": "gen.json"}},
            "api": {"retry": {"max_attempts": 3}},
            "validation": {"gen": {"rules": []}},
        }
        cfg_file = tmp_path / "config.yaml"
        cfg_file.write_text(yaml.dump(config))
        result = validate_config_run(cfg_file)
        assert not any("4-Point Link Rule" in e for e in result["errors"])

    def test_four_point_link_expression_steps_exempt(self, tmp_path):
        """Expression and run-scope steps are exempt from 4-Point Link Rule."""
        config = {
            "pipeline": {"steps": [
                {"name": "calc", "scope": "expression", "expressions": {"x": "1"}},
                {"name": "agg", "scope": "run"},
            ]},
            "processing": {
                "strategy": "direct",
                "chunk_size": 50,
                "items": {"source": "items.yaml", "key": "items"},
            },
        }
        cfg_file = tmp_path / "config.yaml"
        cfg_file.write_text(yaml.dump(config))
        result = validate_config_run(cfg_file)
        assert not any("4-Point Link Rule" in e for e in result["errors"])

    def test_no_pipeline_steps(self, tmp_path):
        """No pipeline steps defined triggers error."""
        config = {
            "pipeline": {"steps": []},
            "processing": {
                "strategy": "direct",
                "chunk_size": 50,
                "items": {"source": "items.yaml", "key": "items"},
            },
        }
        cfg_file = tmp_path / "config.yaml"
        cfg_file.write_text(yaml.dump(config))
        result = validate_config_run(cfg_file)
        assert result["valid"] is False

    def test_expression_validation_errors_reported(self, tmp_path):
        """Invalid expressions in validation rules are reported."""
        template_dir = tmp_path / "templates"
        template_dir.mkdir()
        schema_dir = tmp_path / "schemas"
        schema_dir.mkdir()
        (template_dir / "gen.jinja2").write_text("{{ content }}")
        (schema_dir / "gen.json").write_text("{}")

        config = {
            "pipeline": {"steps": [{"name": "gen"}]},
            "processing": {
                "strategy": "direct",
                "chunk_size": 50,
                "items": {"source": "items.yaml", "key": "items"},
            },
            "prompts": {"template_dir": "templates", "templates": {"gen": "gen.jinja2"}},
            "schemas": {"schema_dir": "schemas", "files": {"gen": "gen.json"}},
            "api": {"retry": {"max_attempts": 3}},
            "validation": {
                "gen": {
                    "required": ["result"],
                    "types": {"result": "string"},
                    "rules": [{"name": "bad_rule", "expr": "result >>><<< 0"}],
                },
            },
        }
        cfg_file = tmp_path / "config.yaml"
        cfg_file.write_text(yaml.dump(config))
        result = validate_config_run(cfg_file)
        assert result["valid"] is False
        assert any("Expression error" in e or "expression" in e.lower() for e in result["errors"])

    def test_expression_step_validation(self, tmp_path):
        """Expression step expressions are validated."""
        items_file = tmp_path / "items.yaml"
        items_file.write_text(yaml.dump({"items": [{"id": 1}]}))
        config = {
            "pipeline": {"steps": [{
                "name": "calc",
                "scope": "expression",
                "init": {"counter": "0"},
                "expressions": {"counter": "counter + 1"},
                "loop_until": "counter >= 10",
            }]},
            "processing": {
                "strategy": "direct",
                "chunk_size": 10,
                "items": {"source": "items.yaml", "key": "items"},
            },
        }
        cfg_file = tmp_path / "config.yaml"
        cfg_file.write_text(yaml.dump(config))
        result = validate_config_run(cfg_file)
        assert result["valid"] is True
        assert result["expressions_validated"] > 0

    def test_expression_step_syntax_error(self, tmp_path):
        """Expression step with syntax error is reported."""
        config = {
            "pipeline": {"steps": [{
                "name": "calc",
                "scope": "expression",
                "expressions": {"bad": "def foo():"},
            }]},
            "processing": {
                "strategy": "direct",
                "chunk_size": 10,
                "items": {"source": "items.yaml", "key": "items"},
            },
        }
        cfg_file = tmp_path / "config.yaml"
        cfg_file.write_text(yaml.dump(config))
        result = validate_config_run(cfg_file)
        assert result["valid"] is False
        assert any("expression" in e.lower() for e in result["errors"])

    def test_expression_step_with_item_field_mocks(self, tmp_path):
        """Expression step uses item field mocks from source file."""
        items_file = tmp_path / "items.yaml"
        items_file.write_text(yaml.dump({
            "items": [{"id": "item1", "value": 42}]
        }))
        config = {
            "pipeline": {"steps": [{
                "name": "calc",
                "scope": "expression",
                "expressions": {"result": "value + 1"},
            }]},
            "processing": {
                "strategy": "direct",
                "chunk_size": 10,
                "items": {"source": "items.yaml", "key": "items"},
            },
        }
        cfg_file = tmp_path / "config.yaml"
        cfg_file.write_text(yaml.dump(config))
        result = validate_config_run(cfg_file)
        assert result["valid"] is True

    def test_item_source_exists(self, tmp_path):
        """Item source file exists -- reported in output."""
        items_file = tmp_path / "items.yaml"
        items_file.write_text(yaml.dump({"items": [{"id": 1}]}))
        config = {
            "pipeline": {"steps": [
                {"name": "calc", "scope": "expression", "expressions": {"x": "1"}},
            ]},
            "processing": {
                "strategy": "direct",
                "chunk_size": 10,
                "items": {"source": "items.yaml", "key": "items"},
            },
        }
        cfg_file = tmp_path / "config.yaml"
        cfg_file.write_text(yaml.dump(config))
        result = validate_config_run(cfg_file)
        assert result["valid"] is True

    def test_item_source_missing(self, tmp_path):
        """Item source file that doesn't exist triggers error."""
        config = {
            "pipeline": {"steps": [
                {"name": "calc", "scope": "expression", "expressions": {"x": "1"}},
            ]},
            "processing": {
                "strategy": "direct",
                "chunk_size": 10,
                "items": {"source": "nonexistent_items.yaml", "key": "items"},
            },
        }
        cfg_file = tmp_path / "config.yaml"
        cfg_file.write_text(yaml.dump(config))
        result = validate_config_run(cfg_file)
        assert result["valid"] is False
        assert any("Item source not found" in e for e in result["errors"])

    def test_self_referential_items_no_source_check(self, tmp_path):
        """Self-referential config (no source, only key) skips item source check."""
        config = {
            "pipeline": {"steps": [
                {"name": "calc", "scope": "expression", "expressions": {"x": "1"}},
            ]},
            "processing": {
                "strategy": "direct",
                "chunk_size": 10,
                "items": {"key": "items"},
            },
        }
        cfg_file = tmp_path / "config.yaml"
        cfg_file.write_text(yaml.dump(config))
        result = validate_config_run(cfg_file)
        assert not any("Item source" in e for e in result["errors"])

    def test_summary_valid(self, tmp_path):
        """Valid config returns valid=True, empty errors."""
        config = {
            "pipeline": {"steps": [
                {"name": "calc", "scope": "expression", "expressions": {"x": "1"}},
            ]},
            "processing": {
                "strategy": "direct",
                "chunk_size": 10,
                "items": {"source": "items.yaml", "key": "items"},
            },
        }
        items_file = tmp_path / "items.yaml"
        items_file.write_text(yaml.dump({"items": [{"id": 1}]}))
        cfg_file = tmp_path / "config.yaml"
        cfg_file.write_text(yaml.dump(config))
        result = validate_config_run(cfg_file)
        assert result["valid"] is True
        assert result["errors"] == []
        assert "pipeline_steps" in result
        assert result["pipeline_steps"] == ["calc"]

    def test_summary_invalid(self, tmp_path):
        """Invalid config returns valid=False with errors."""
        cfg_file = tmp_path / "config.yaml"
        cfg_file.write_text(yaml.dump({}))
        result = validate_config_run(cfg_file)
        assert result["valid"] is False
        assert len(result["errors"]) > 0

    def test_no_expressions_configured(self, tmp_path):
        """Config with no expressions reports 'none configured'."""
        template_dir = tmp_path / "templates"
        template_dir.mkdir()
        schema_dir = tmp_path / "schemas"
        schema_dir.mkdir()
        (template_dir / "gen.jinja2").write_text("{{ content }}")
        (schema_dir / "gen.json").write_text("{}")

        config = {
            "pipeline": {"steps": [{"name": "gen"}]},
            "processing": {
                "strategy": "direct",
                "chunk_size": 50,
                "items": {"source": "items.yaml", "key": "items"},
            },
            "prompts": {"template_dir": "templates", "templates": {"gen": "gen.jinja2"}},
            "schemas": {"schema_dir": "schemas", "files": {"gen": "gen.json"}},
            "api": {"retry": {"max_attempts": 3}},
            "validation": {"gen": {"rules": []}},
        }
        items_file = tmp_path / "items.yaml"
        items_file.write_text(yaml.dump({"items": [{"id": 1}]}))
        cfg_file = tmp_path / "config.yaml"
        cfg_file.write_text(yaml.dump(config))
        result = validate_config_run(cfg_file)
        assert result["expressions_validated"] == 0

    def test_expression_step_init_then_expressions_then_loop(self, tmp_path):
        """Full expression step with init, expressions, and loop_until all validated sequentially."""
        items_file = tmp_path / "items.yaml"
        items_file.write_text(yaml.dump({
            "items": [{"id": "x", "base": 10}]
        }))
        config = {
            "pipeline": {"steps": [{
                "name": "sim",
                "scope": "expression",
                "init": {"total": "0", "count": "0"},
                "expressions": {
                    "total": "total + base",
                    "count": "count + 1",
                },
                "loop_until": "count >= 5",
            }]},
            "processing": {
                "strategy": "direct",
                "chunk_size": 10,
                "items": {"source": "items.yaml", "key": "items"},
            },
        }
        cfg_file = tmp_path / "config.yaml"
        cfg_file.write_text(yaml.dump(config))
        result = validate_config_run(cfg_file)
        assert result["valid"] is True
        # 2 init + 2 expressions + 1 loop_until = 5 expressions
        assert result["expressions_validated"] == 5

    def test_expression_step_eval_error_injects_fallback(self, tmp_path):
        """Expression step that causes eval error injects fallback for downstream."""
        config = {
            "pipeline": {"steps": [{
                "name": "calc",
                "scope": "expression",
                "expressions": {
                    "bad": "undefined_var_xyz_abc",
                    "uses_bad": "bad + 1",  # Should use fallback value 0
                },
            }]},
            "processing": {
                "strategy": "direct",
                "chunk_size": 10,
                "items": {"source": "items.yaml", "key": "items"},
            },
        }
        cfg_file = tmp_path / "config.yaml"
        cfg_file.write_text(yaml.dump(config))
        result = validate_config_run(cfg_file)
        # At least one error for the undefined variable
        assert any("expression" in e.lower() for e in result["errors"])

    def test_validation_expression_with_mock_context(self, tmp_path):
        """Validation expression uses built mock context with types and ranges."""
        template_dir = tmp_path / "templates"
        template_dir.mkdir()
        schema_dir = tmp_path / "schemas"
        schema_dir.mkdir()
        (template_dir / "gen.jinja2").write_text("{{ content }}")
        (schema_dir / "gen.json").write_text("{}")

        config = {
            "pipeline": {"steps": [{"name": "gen"}]},
            "processing": {
                "strategy": "direct",
                "chunk_size": 50,
                "items": {"source": "items.yaml", "key": "items"},
            },
            "prompts": {"template_dir": "templates", "templates": {"gen": "gen.jinja2"}},
            "schemas": {"schema_dir": "schemas", "files": {"gen": "gen.json"}},
            "api": {"retry": {"max_attempts": 3}},
            "validation": {
                "gen": {
                    "required": ["score", "label"],
                    "types": {"score": "integer", "label": "string"},
                    "ranges": {"score": [0, 100]},
                    "rules": [
                        {"name": "score_valid", "expr": "score >= 0 and score <= 100"},
                        {"name": "label_check", "expr": "len(label) > 0", "when": "label is not None"},
                    ],
                },
            },
        }
        items_file = tmp_path / "items.yaml"
        items_file.write_text(yaml.dump({"items": [{"id": 1}]}))
        cfg_file = tmp_path / "config.yaml"
        cfg_file.write_text(yaml.dump(config))
        result = validate_config_run(cfg_file)
        assert result["valid"] is True
        assert result["expressions_validated"] == 3  # 2 expr + 1 when

    def test_pipeline_step_info_output(self, tmp_path):
        """Pipeline step info shows expression steps with [expr] suffix."""
        config = {
            "pipeline": {"steps": [
                {"name": "setup", "scope": "expression", "expressions": {"x": "1"}},
                {"name": "gen"},
            ]},
            "processing": {
                "strategy": "direct",
                "chunk_size": 50,
                "items": {"source": "items.yaml", "key": "items"},
            },
            "prompts": {"template_dir": "templates", "templates": {"gen": "gen.jinja2"}},
            "schemas": {"schema_dir": "schemas", "files": {"gen": "gen.json"}},
            "api": {"retry": {"max_attempts": 3}},
            "validation": {"gen": {"rules": []}},
        }
        template_dir = tmp_path / "templates"
        template_dir.mkdir()
        schema_dir = tmp_path / "schemas"
        schema_dir.mkdir()
        (template_dir / "gen.jinja2").write_text("{{ content }}")
        (schema_dir / "gen.json").write_text("{}")

        cfg_file = tmp_path / "config.yaml"
        cfg_file.write_text(yaml.dump(config))
        result = validate_config_run(cfg_file)
        assert result["pipeline_steps"] == ["setup", "gen"]

    def test_result_contains_all_keys(self, tmp_path):
        """Returned dict contains all expected keys."""
        config = {
            "pipeline": {"steps": [
                {"name": "calc", "scope": "expression", "expressions": {"x": "1"}},
            ]},
            "processing": {
                "strategy": "direct",
                "chunk_size": 10,
                "items": {"key": "items"},
            },
        }
        cfg_file = tmp_path / "config.yaml"
        cfg_file.write_text(yaml.dump(config))
        result = validate_config_run(cfg_file)
        assert "valid" in result
        assert "errors" in result
        assert "warnings" in result
        assert "pipeline_steps" in result
        assert "templates_found" in result
        assert "schemas_found" in result
        assert "expressions_validated" in result


# =============================================================================
# Additional coverage — non-string expression name, validate_expression
# exception, main() function
# =============================================================================

class TestNonStringExpressionName:
    """Cover line 133: Expression name not a string in processing.expressions."""

    def test_non_string_expression_name(self):
        """When a processing expressions dict has a non-string key, error is reported."""
        config = {
            "pipeline": {
                "steps": [{"name": "calc", "scope": "expression", "expressions": {"x": "1"}}]
            },
            "processing": {
                "strategy": "direct",
                "chunk_size": 10,
                "items": {"source": "a.yaml", "key": "b"},
                "expressions": {42: "1 + 2"},  # non-string key
            },
        }
        errors = validate_config(config)
        assert any("Expression name must be string" in e for e in errors)


class TestValidateExpressionException:
    """Cover lines 490-491: Exception branch in validate_expression."""

    def test_exception_during_eval(self):
        """When aeval.eval raises a non-asteval Exception, returns (False, msg)."""
        from unittest.mock import MagicMock

        mock_aeval = MagicMock()
        mock_aeval.symtable = {}
        mock_aeval.eval.side_effect = RuntimeError("unexpected crash")

        success, error = validate_expression(mock_aeval, "1 + 2", {"x": 10})
        assert success is False
        assert "unexpected crash" in error


class TestMainFunction:
    """Cover lines 795-822, 826: main() function."""

    def test_main_valid_config(self, tmp_path, monkeypatch, capsys):
        """main() exits 0 for valid config."""
        import config_validator

        config = {
            "pipeline": {
                "steps": [{"name": "calc", "scope": "expression", "expressions": {"x": "1"}}]
            },
            "processing": {
                "strategy": "direct",
                "chunk_size": 10,
                "items": {"key": "items"},
            },
        }
        cfg_file = tmp_path / "config.yaml"
        cfg_file.write_text(yaml.dump(config))

        monkeypatch.setattr("sys.argv", ["config_validator.py", "--config", str(cfg_file)])
        with pytest.raises(SystemExit) as exc_info:
            config_validator.main()
        assert exc_info.value.code == 0

    def test_main_invalid_config(self, tmp_path, monkeypatch, capsys):
        """main() exits 1 for invalid config."""
        import config_validator

        config = {"pipeline": {"steps": []}}
        cfg_file = tmp_path / "config.yaml"
        cfg_file.write_text(yaml.dump(config))

        monkeypatch.setattr("sys.argv", ["config_validator.py", "--config", str(cfg_file)])
        with pytest.raises(SystemExit) as exc_info:
            config_validator.main()
        assert exc_info.value.code == 1

    def test_main_json_output(self, tmp_path, monkeypatch, capsys):
        """main() with --json flag outputs JSON."""
        import json
        import config_validator

        config = {
            "pipeline": {
                "steps": [{"name": "calc", "scope": "expression", "expressions": {"x": "1"}}]
            },
            "processing": {
                "strategy": "direct",
                "chunk_size": 10,
                "items": {"key": "items"},
            },
        }
        cfg_file = tmp_path / "config.yaml"
        cfg_file.write_text(yaml.dump(config))

        monkeypatch.setattr("sys.argv", ["config_validator.py", "--config", str(cfg_file), "--json"])
        with pytest.raises(SystemExit) as exc_info:
            config_validator.main()
        assert exc_info.value.code == 0

        captured = capsys.readouterr()
        # The JSON output should be valid JSON somewhere in stdout
        # Find the JSON portion (after the human-readable output)
        lines = captured.out.strip().split("\n")
        # Look for the JSON block (starts with {)
        json_start = None
        for i, line in enumerate(lines):
            if line.strip().startswith("{"):
                json_start = i
                break
        assert json_start is not None
        json_text = "\n".join(lines[json_start:])
        parsed = json.loads(json_text)
        assert "valid" in parsed

    def test_main_missing_config_exits_1(self, tmp_path, monkeypatch, capsys):
        """main() exits 1 when config file doesn't exist."""
        import config_validator

        monkeypatch.setattr("sys.argv", ["config_validator.py", "-c", str(tmp_path / "nope.yaml")])
        with pytest.raises(SystemExit) as exc_info:
            config_validator.main()
        assert exc_info.value.code == 1
