"""
Tests for config.yaml parsing (scripts/config_validator.py).

Covers:
- Valid config loads without error
- Missing required fields produce clear errors
- Pipeline step references are validated
- Expression step config (expressions dict, no prompt_template)
- Validation rules parsed correctly
"""

import sys
from pathlib import Path

import pytest

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
    extract_expressions,
    build_mock_context,
    extract_variable_names,
    generate_mock_value,
)


# =============================================================================
# Fixtures
# =============================================================================

@pytest.fixture
def valid_config():
    """A minimal valid config with one LLM step."""
    return {
        "pipeline": {
            "steps": [
                {"name": "generate"},
            ]
        },
        "processing": {
            "strategy": "direct",
            "chunk_size": 50,
            "items": {
                "source": "items.yaml",
                "key": "items",
                "name_field": "id",
            },
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
def blackjack_config():
    """Config modeled after the Blackjack pipeline with expression and LLM steps."""
    return {
        "pipeline": {
            "steps": [
                {"name": "deal_cards", "scope": "expression", "expressions": {"card": "'A'"}},
                {"name": "play_hand"},
                {"name": "verify_hand", "scope": "expression", "expressions": {"ok": "True"}},
                {"name": "analyze_difficulty"},
            ]
        },
        "processing": {
            "strategy": "direct",
            "chunk_size": 100,
            "repeat": 300,
            "items": {
                "source": "items.yaml",
                "key": "strategies",
                "name_field": "id",
            },
        },
        "prompts": {
            "template_dir": "templates",
            "templates": {
                "play_hand": "play_hand.jinja2",
                "analyze_difficulty": "analyze_difficulty.jinja2",
            },
        },
        "schemas": {
            "schema_dir": "schemas",
            "files": {
                "play_hand": "play_hand.json",
                "analyze_difficulty": "analyze_difficulty.json",
            },
        },
        "api": {"retry": {"max_attempts": 3}},
        "validation": {
            "play_hand": {
                "required": ["result"],
                "types": {"result": "string"},
            },
            "verify_hand": {
                "required": ["verification_passed"],
                "types": {"verification_passed": "boolean"},
                "rules": [{
                    "name": "hand_verification",
                    "expr": "verification_passed == True",
                    "error": "Verification failed",
                }],
            },
            "analyze_difficulty": {
                "required": ["difficulty"],
                "types": {"difficulty": "string"},
            },
        },
    }


# =============================================================================
# Valid Config
# =============================================================================

class TestValidConfig:
    """Tests that valid configs pass validation."""

    def test_minimal_valid_config(self, valid_config):
        """Minimal valid config produces no errors."""
        errors = validate_config(valid_config)
        assert errors == []

    def test_blackjack_config_valid(self, blackjack_config):
        """Blackjack-style config with expression steps is valid."""
        errors = validate_config(blackjack_config)
        assert errors == []

    def test_expression_only_pipeline(self):
        """Pipeline with only expression steps is valid (no prompts/schemas/api needed)."""
        config = {
            "pipeline": {
                "steps": [
                    {"name": "step1", "scope": "expression", "expressions": {"x": "1 + 1"}},
                ]
            },
            "processing": {
                "strategy": "direct",
                "chunk_size": 50,
                "items": {"source": "items.yaml", "key": "items"},
            },
        }
        errors = validate_config(config)
        assert errors == []

    def test_direct_strategy_no_positions(self):
        """Direct strategy doesn't require positions."""
        config = {
            "pipeline": {"steps": [{"name": "gen", "scope": "expression", "expressions": {"x": "1"}}]},
            "processing": {
                "strategy": "direct",
                "chunk_size": 10,
                "items": {"source": "items.yaml", "key": "items"},
            },
        }
        errors = validate_config(config)
        assert errors == []


# =============================================================================
# Missing Required Fields
# =============================================================================

class TestMissingFields:
    """Tests that missing required fields produce clear errors."""

    def test_missing_pipeline(self):
        """Missing pipeline section."""
        config = {
            "processing": {"strategy": "direct", "chunk_size": 50, "items": {"source": "x.yaml", "key": "y"}},
        }
        errors = validate_config(config)
        assert any("pipeline" in e.lower() for e in errors)

    def test_missing_pipeline_steps(self):
        """Pipeline without steps."""
        config = {
            "pipeline": {},
            "processing": {"strategy": "direct", "chunk_size": 50, "items": {"source": "x.yaml", "key": "y"}},
        }
        errors = validate_config(config)
        assert any("pipeline.steps" in e for e in errors)

    def test_empty_pipeline_steps(self):
        """Empty steps list."""
        config = {
            "pipeline": {"steps": []},
            "processing": {"strategy": "direct", "chunk_size": 50, "items": {"source": "x.yaml", "key": "y"}},
        }
        errors = validate_config(config)
        assert any("empty" in e.lower() for e in errors)

    def test_missing_processing(self):
        """Missing processing section."""
        config = {
            "pipeline": {"steps": [{"name": "gen", "scope": "expression", "expressions": {"x": "1"}}]},
        }
        errors = validate_config(config)
        assert any("processing" in e.lower() for e in errors)

    def test_missing_chunk_size(self):
        """Missing chunk_size."""
        config = {
            "pipeline": {"steps": [{"name": "gen", "scope": "expression", "expressions": {"x": "1"}}]},
            "processing": {
                "strategy": "direct",
                "items": {"source": "x.yaml", "key": "y"},
            },
        }
        errors = validate_config(config)
        assert any("chunk_size" in e for e in errors)

    def test_missing_items(self):
        """Missing items section."""
        config = {
            "pipeline": {"steps": [{"name": "gen", "scope": "expression", "expressions": {"x": "1"}}]},
            "processing": {
                "strategy": "direct",
                "chunk_size": 50,
            },
        }
        errors = validate_config(config)
        assert any("items" in e.lower() for e in errors)

    def test_step_missing_name(self):
        """Step without name produces error."""
        config = {
            "pipeline": {"steps": [{"scope": "expression"}]},
            "processing": {"strategy": "direct", "chunk_size": 10, "items": {"source": "x.yaml", "key": "y"}},
        }
        errors = validate_config(config)
        assert any("missing 'name'" in e.lower() for e in errors)

    def test_missing_prompts_for_llm_steps(self):
        """Missing prompts/schemas/api sections when LLM steps present."""
        config = {
            "pipeline": {"steps": [{"name": "generate"}]},
            "processing": {
                "strategy": "direct",
                "chunk_size": 50,
                "items": {"source": "x.yaml", "key": "y"},
            },
        }
        errors = validate_config(config)
        assert any("prompts" in e.lower() for e in errors)
        assert any("schemas" in e.lower() for e in errors)
        assert any("api" in e.lower() for e in errors)


# =============================================================================
# Pipeline Step Validation
# =============================================================================

class TestPipelineStepValidation:
    """Tests for pipeline step config validation."""

    def test_invalid_scope(self):
        """Invalid scope value produces error."""
        config = {
            "pipeline": {"steps": [{"name": "bad_step", "scope": "invalid_scope"}]},
            "processing": {"strategy": "direct", "chunk_size": 10, "items": {"source": "x.yaml", "key": "y"}},
        }
        errors = validate_config(config)
        assert any("invalid scope" in e.lower() for e in errors)

    def test_expression_step_missing_expressions(self):
        """Expression step without expressions block produces error."""
        config = {
            "pipeline": {"steps": [{"name": "bad_expr", "scope": "expression"}]},
            "processing": {"strategy": "direct", "chunk_size": 10, "items": {"source": "x.yaml", "key": "y"}},
        }
        errors = validate_config(config)
        assert any("expressions" in e.lower() for e in errors)

    def test_expression_step_empty_expressions(self):
        """Expression step with empty expressions dict produces error."""
        config = {
            "pipeline": {"steps": [{"name": "bad_expr", "scope": "expression", "expressions": {}}]},
            "processing": {"strategy": "direct", "chunk_size": 10, "items": {"source": "x.yaml", "key": "y"}},
        }
        errors = validate_config(config)
        assert any("empty" in e.lower() for e in errors)

    def test_expression_step_non_dict_expressions(self):
        """Expression step with non-dict expressions produces error."""
        config = {
            "pipeline": {"steps": [{"name": "bad_expr", "scope": "expression", "expressions": "not_a_dict"}]},
            "processing": {"strategy": "direct", "chunk_size": 10, "items": {"source": "x.yaml", "key": "y"}},
        }
        errors = validate_config(config)
        assert any("dict" in e.lower() for e in errors)

    def test_invalid_provider_override(self):
        """Invalid per-step provider override produces error."""
        config = {
            "pipeline": {"steps": [{"name": "gen", "scope": "expression", "expressions": {"x": "1"}, "provider": "invalid_provider"}]},
            "processing": {"strategy": "direct", "chunk_size": 10, "items": {"source": "x.yaml", "key": "y"}},
        }
        errors = validate_config(config)
        assert any("invalid provider" in e.lower() for e in errors)

    def test_invalid_model_type(self):
        """Per-step model must be a string."""
        config = {
            "pipeline": {"steps": [{"name": "gen", "scope": "expression", "expressions": {"x": "1"}, "model": 123}]},
            "processing": {"strategy": "direct", "chunk_size": 10, "items": {"source": "x.yaml", "key": "y"}},
        }
        errors = validate_config(config)
        assert any("model must be a string" in e.lower() for e in errors)


# =============================================================================
# Processing Validation
# =============================================================================

class TestProcessingValidation:
    """Tests for processing section validation."""

    def test_invalid_strategy(self):
        """Invalid strategy value produces error."""
        config = {
            "pipeline": {"steps": [{"name": "gen", "scope": "expression", "expressions": {"x": "1"}}]},
            "processing": {
                "strategy": "chaos",
                "chunk_size": 10,
                "items": {"source": "x.yaml", "key": "y"},
            },
        }
        errors = validate_config(config)
        assert any("strategy" in e.lower() for e in errors)

    def test_permutation_requires_positions(self):
        """Permutation strategy requires positions."""
        config = {
            "pipeline": {"steps": [{"name": "gen", "scope": "expression", "expressions": {"x": "1"}}]},
            "processing": {
                "strategy": "permutation",
                "chunk_size": 10,
                "items": {"source": "x.yaml", "key": "y"},
            },
        }
        errors = validate_config(config)
        assert any("positions" in e.lower() for e in errors)

    def test_invalid_repeat_type(self):
        """Non-integer repeat produces error."""
        config = {
            "pipeline": {"steps": [{"name": "gen", "scope": "expression", "expressions": {"x": "1"}}]},
            "processing": {
                "strategy": "direct",
                "chunk_size": 10,
                "repeat": "many",
                "items": {"source": "x.yaml", "key": "y"},
            },
        }
        errors = validate_config(config)
        assert any("repeat" in e.lower() for e in errors)

    def test_invalid_repeat_value(self):
        """repeat < 1 produces error."""
        config = {
            "pipeline": {"steps": [{"name": "gen", "scope": "expression", "expressions": {"x": "1"}}]},
            "processing": {
                "strategy": "direct",
                "chunk_size": 10,
                "repeat": 0,
                "items": {"source": "x.yaml", "key": "y"},
            },
        }
        errors = validate_config(config)
        assert any("repeat" in e.lower() for e in errors)

    def test_invalid_processing_expression_syntax(self):
        """Invalid processing expressions should report syntax errors."""
        config = {
            "pipeline": {"steps": [{"name": "gen", "scope": "expression", "expressions": {"x": "1"}}]},
            "processing": {
                "strategy": "direct",
                "chunk_size": 10,
                "items": {"source": "x.yaml", "key": "y"},
                "expressions": {"bad": "def nope():"},
            },
        }
        errors = validate_config(config)
        assert any("expression 'bad' is invalid" in e.lower() for e in errors)


# =============================================================================
# Pipeline Step Extraction
# =============================================================================

class TestPipelineExtraction:
    """Tests for pipeline step extraction helpers."""

    def test_get_pipeline_steps(self, blackjack_config):
        """Extracts ordered list of step names."""
        steps = get_pipeline_steps(blackjack_config)
        assert steps == ["deal_cards", "play_hand", "verify_hand", "analyze_difficulty"]

    def test_get_chunk_scope_steps(self, blackjack_config):
        """Gets chunk-scope and expression steps (both processed at chunk level)."""
        steps = get_chunk_scope_steps(blackjack_config)
        assert "deal_cards" in steps  # expression scope
        assert "play_hand" in steps  # chunk scope (default)
        assert "verify_hand" in steps  # expression scope
        assert "analyze_difficulty" in steps  # chunk scope (default)

    def test_get_expression_steps(self, blackjack_config):
        """Gets only expression-scope step configs."""
        expr_steps = get_expression_steps(blackjack_config)
        names = [s["name"] for s in expr_steps]
        assert "deal_cards" in names
        assert "verify_hand" in names
        assert "play_hand" not in names

    def test_get_run_scope_steps_empty(self, blackjack_config):
        """Returns empty list when no run-scope steps."""
        run_steps = get_run_scope_steps(blackjack_config)
        assert run_steps == []

    def test_get_run_scope_steps(self):
        """Returns run-scope step configs."""
        config = {
            "pipeline": {
                "steps": [
                    {"name": "gen", "scope": "expression", "expressions": {"x": "1"}},
                    {"name": "aggregate", "scope": "run"},
                ]
            }
        }
        run_steps = get_run_scope_steps(config)
        assert len(run_steps) == 1
        assert run_steps[0]["name"] == "aggregate"

    def test_get_step_config(self, blackjack_config):
        """Gets full config for a specific step."""
        step = get_step_config(blackjack_config, "deal_cards")
        assert step is not None
        assert step["name"] == "deal_cards"
        assert step["scope"] == "expression"
        assert "expressions" in step

    def test_get_step_config_missing(self, blackjack_config):
        """Returns None for nonexistent step."""
        assert get_step_config(blackjack_config, "nonexistent") is None

    def test_get_pipeline_steps_empty(self):
        """Returns empty list for config with no steps."""
        assert get_pipeline_steps({}) == []
        assert get_pipeline_steps({"pipeline": {}}) == []


# =============================================================================
# Item Source Path
# =============================================================================

class TestItemSourcePath:
    """Tests for get_item_source_path()."""

    def test_relative_source_resolved(self, tmp_path):
        """Source path resolved relative to config directory."""
        config = {"processing": {"items": {"source": "items.yaml"}}}
        config_path = tmp_path / "pipeline" / "config.yaml"
        result = get_item_source_path(config, config_path)
        assert result == tmp_path / "pipeline" / "items.yaml"

    def test_self_referential_returns_none(self, tmp_path):
        """Config without source (self-referential) returns None."""
        config = {"processing": {"items": {"key": "items"}}}
        config_path = tmp_path / "config.yaml"
        result = get_item_source_path(config, config_path)
        assert result is None


# =============================================================================
# Expression Extraction
# =============================================================================

class TestExtractExpressions:
    """Tests for extract_expressions()."""

    def test_extracts_validation_expressions(self):
        """Extracts expressions from validation rules."""
        config = {
            "validation": {
                "play_hand": {
                    "rules": [{
                        "name": "check_total",
                        "expr": "total > 0",
                        "when": "has_total == True",
                    }]
                }
            }
        }
        exprs = extract_expressions(config)
        assert len(exprs) == 2  # expr + when
        fields = {e["field"] for e in exprs}
        assert "expr" in fields
        assert "when" in fields

    def test_extracts_expression_step_expressions(self):
        """Extracts expressions from expression step definitions."""
        config = {
            "pipeline": {
                "steps": [{
                    "name": "deal",
                    "scope": "expression",
                    "expressions": {"card": "random.choice(['A','K','Q'])"},
                }]
            }
        }
        exprs = extract_expressions(config)
        assert len(exprs) == 1
        assert exprs[0]["field"] == "expression_step"
        assert exprs[0]["step"] == "deal"

    def test_extracts_init_and_loop_until(self):
        """Extracts init and loop_until expressions from expression steps."""
        config = {
            "pipeline": {
                "steps": [{
                    "name": "simulate",
                    "scope": "expression",
                    "init": {"counter": "0"},
                    "expressions": {"counter": "counter + 1"},
                    "loop_until": "counter >= 10",
                }]
            }
        }
        exprs = extract_expressions(config)
        fields = {e["field"] for e in exprs}
        assert "expression_step_init" in fields
        assert "expression_step" in fields
        assert "expression_step_loop_until" in fields


# =============================================================================
# Mock Context Building
# =============================================================================

class TestBuildMockContext:
    """Tests for build_mock_context()."""

    def test_includes_required_fields(self):
        """Mock context includes all required fields."""
        step_config = {"required": ["name", "score"]}
        context = build_mock_context(step_config)
        assert "name" in context
        assert "score" in context

    def test_uses_type_info(self):
        """Mock values respect type information."""
        step_config = {
            "required": ["score"],
            "types": {"score": "integer"},
            "ranges": {"score": [0, 100]},
        }
        context = build_mock_context(step_config)
        assert isinstance(context["score"], int)
        assert 0 <= context["score"] <= 100

    def test_includes_computed(self):
        """Mock context includes _computed variable."""
        context = build_mock_context({})
        assert "_computed" in context


# =============================================================================
# Variable Name Extraction
# =============================================================================

class TestExtractVariableNames:
    """Tests for extract_variable_names()."""

    def test_simple_expression(self):
        """Extracts variable names from simple expression."""
        names = extract_variable_names("x + y")
        assert "x" in names
        assert "y" in names

    def test_filters_keywords(self):
        """Filters out Python keywords."""
        names = extract_variable_names("x and y or not z")
        assert "x" in names
        assert "y" in names
        assert "z" in names
        assert "and" not in names
        assert "or" not in names
        assert "not" not in names

    def test_filters_builtins(self):
        """Filters out common builtins."""
        names = extract_variable_names("len(my_list) + sum(scores)")
        assert "my_list" in names
        assert "scores" in names
        assert "len" not in names
        assert "sum" not in names


# =============================================================================
# Generate Mock Value
# =============================================================================

class TestGenerateMockValue:
    """Tests for generate_mock_value()."""

    def test_integer_with_range(self):
        """Integer with range returns midpoint."""
        val = generate_mock_value("score", {"types": {"score": "integer"}, "ranges": {"score": [0, 100]}})
        assert val == 50

    def test_integer_without_range(self):
        """Integer without range returns 5."""
        val = generate_mock_value("score", {"types": {"score": "integer"}, "ranges": {}})
        assert val == 5

    def test_string_type(self):
        """String type returns 'sample'."""
        val = generate_mock_value("name", {"types": {"name": "string"}, "ranges": {}})
        assert val == "sample"

    def test_boolean_type(self):
        """Boolean type returns True."""
        val = generate_mock_value("flag", {"types": {"flag": "boolean"}, "ranges": {}})
        assert val is True

    def test_unknown_type(self):
        """Unknown type returns 'mock_value'."""
        val = generate_mock_value("mystery", {"types": {}, "ranges": {}})
        assert val == "mock_value"
