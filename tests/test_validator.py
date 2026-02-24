"""
Tests for the expression-based validator (scripts/validator.py).

Covers:
- Schema validation: required fields, type checking, range validation, enum validation
- Business logic rules: expression evaluation, conditional rules (when clauses), warning vs error levels
- Edge cases: missing fields, wrong types, empty input, malformed JSON
- asteval expression evaluation: arithmetic, comparisons, dict access, array operations
"""

import sys
from pathlib import Path

import pytest

# Add scripts directory to path so validator module is importable
sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

from validator import (
    validate_required,
    validate_types,
    validate_ranges,
    validate_enums,
    validate_expression_rules,
    validate_line,
    validate_type,
    evaluate_expression,
    format_error_message,
    set_interpreter_context,
    process_line,
    get_step_validation_config,
)
from octobatch_utils import create_interpreter


# =============================================================================
# Fixtures
# =============================================================================

@pytest.fixture
def aeval():
    """Create a fresh asteval interpreter for tests."""
    return create_interpreter()


@pytest.fixture
def blackjack_validation_config():
    """Validation config modeled after the Blackjack pipeline's play_hand step."""
    return {
        "required": [
            "action_log", "player_cards_used", "dealer_cards_used",
            "player_final_total", "dealer_final_total",
            "player_busted", "dealer_busted", "result",
            "first_action", "player_initial_total",
        ],
        "types": {
            "action_log": "array",
            "player_cards_used": "array",
            "dealer_cards_used": "array",
            "player_final_total": "number",
            "dealer_final_total": "number",
            "player_busted": "boolean",
            "dealer_busted": "boolean",
            "result": "string",
            "first_action": "string",
            "player_initial_total": "number",
        },
        "ranges": {
            "player_final_total": [2, 30],
            "dealer_final_total": [2, 30],
        },
        "enums": {
            "result": ["player_wins", "dealer_wins", "push"],
            "first_action": ["hit", "stand", "double_down", "split"],
        },
    }


# =============================================================================
# Required Fields Validation
# =============================================================================

class TestRequiredFields:
    """Tests for validate_required()."""

    def test_all_required_present(self):
        """No errors when all required fields are present."""
        data = {"name": "Alice", "age": 30}
        errors = validate_required(data, ["name", "age"])
        assert errors == []

    def test_missing_single_field(self):
        """Error for a single missing required field."""
        data = {"name": "Alice"}
        errors = validate_required(data, ["name", "age"])
        assert len(errors) == 1
        assert "age" in errors[0]["message"]
        assert errors[0]["rule"] == "required_age"

    def test_missing_multiple_fields(self):
        """Errors for all missing required fields."""
        data = {}
        errors = validate_required(data, ["name", "age", "email"])
        assert len(errors) == 3

    def test_none_value_treated_as_missing(self):
        """A field with None value is treated as missing."""
        data = {"name": None}
        errors = validate_required(data, ["name"])
        assert len(errors) == 1

    def test_empty_required_list(self):
        """No errors with empty required list."""
        errors = validate_required({"a": 1}, [])
        assert errors == []


# =============================================================================
# Type Validation
# =============================================================================

class TestTypeValidation:
    """Tests for validate_types() and validate_type()."""

    def test_integer_type_valid(self):
        """Integer validation passes for int values."""
        is_valid, _ = validate_type(42, "integer")
        assert is_valid

    def test_integer_type_rejects_float(self):
        """Integer validation rejects float values."""
        is_valid, msg = validate_type(3.14, "integer")
        assert not is_valid
        assert "float" in msg

    def test_integer_type_rejects_bool(self):
        """Integer validation rejects boolean (even though bool is subclass of int)."""
        is_valid, _ = validate_type(True, "integer")
        assert not is_valid

    def test_string_type_valid(self):
        """String validation passes for str values."""
        is_valid, _ = validate_type("hello", "string")
        assert is_valid

    def test_boolean_type_valid(self):
        """Boolean validation passes for bool values."""
        is_valid, _ = validate_type(True, "boolean")
        assert is_valid

    def test_array_type_valid(self):
        """Array validation passes for list values."""
        is_valid, _ = validate_type([1, 2, 3], "array")
        assert is_valid

    def test_object_type_valid(self):
        """Object validation passes for dict values."""
        is_valid, _ = validate_type({"a": 1}, "object")
        assert is_valid

    def test_number_type_accepts_int_and_float(self):
        """Number type accepts both int and float."""
        assert validate_type(42, "number")[0]
        assert validate_type(3.14, "number")[0]

    def test_unknown_type_returns_error(self):
        """Unknown type string produces an error."""
        is_valid, msg = validate_type(42, "bigint")
        assert not is_valid
        assert "Unknown type" in msg

    def test_validate_types_skips_missing_fields(self):
        """validate_types() skips fields not present in data."""
        data = {"name": "Alice"}
        errors = validate_types(data, {"name": "string", "age": "integer"})
        assert errors == []  # age missing but not checked here

    def test_validate_types_reports_wrong_type(self):
        """validate_types() catches wrong types."""
        data = {"age": "thirty"}
        errors = validate_types(data, {"age": "integer"})
        assert len(errors) == 1
        assert "type_age" in errors[0]["rule"]


# =============================================================================
# Range Validation
# =============================================================================

class TestRangeValidation:
    """Tests for validate_ranges()."""

    def test_value_in_range(self):
        """No errors when value is within range."""
        data = {"score": 50}
        errors = validate_ranges(data, {"score": [0, 100]})
        assert errors == []

    def test_value_at_boundaries(self):
        """No errors when value is at exact min or max."""
        assert validate_ranges({"x": 0}, {"x": [0, 100]}) == []
        assert validate_ranges({"x": 100}, {"x": [0, 100]}) == []

    def test_value_below_min(self):
        """Error when value is below minimum."""
        data = {"score": -5}
        errors = validate_ranges(data, {"score": [0, 100]})
        assert len(errors) == 1
        assert "outside range" in errors[0]["message"]

    def test_value_above_max(self):
        """Error when value exceeds maximum."""
        data = {"score": 150}
        errors = validate_ranges(data, {"score": [0, 100]})
        assert len(errors) == 1

    def test_skips_non_numeric_fields(self):
        """Range validation skips non-numeric values (type validation handles that)."""
        data = {"score": "high"}
        errors = validate_ranges(data, {"score": [0, 100]})
        assert errors == []


# =============================================================================
# Enum Validation
# =============================================================================

class TestEnumValidation:
    """Tests for validate_enums()."""

    def test_valid_enum_value(self):
        """No errors for valid enum value."""
        data = {"status": "active"}
        errors = validate_enums(data, {"status": ["active", "inactive"]})
        assert errors == []

    def test_invalid_enum_value(self):
        """Error for value not in enum."""
        data = {"status": "deleted"}
        errors = validate_enums(data, {"status": ["active", "inactive"]})
        assert len(errors) == 1
        assert "not in allowed values" in errors[0]["message"]

    def test_case_insensitive_string_enum(self):
        """String enum comparison is case-insensitive."""
        data = {"result": "Player_Wins"}
        errors = validate_enums(data, {"result": ["player_wins", "dealer_wins"]})
        assert errors == []

    def test_numeric_enum(self):
        """Enum validation works for numeric values."""
        data = {"level": 3}
        errors = validate_enums(data, {"level": [1, 2, 3]})
        assert errors == []

    def test_numeric_enum_invalid(self):
        """Numeric enum validation catches invalid values."""
        data = {"level": 5}
        errors = validate_enums(data, {"level": [1, 2, 3]})
        assert len(errors) == 1


# =============================================================================
# Expression Evaluation
# =============================================================================

class TestExpressionEvaluation:
    """Tests for asteval expression evaluation."""

    def test_arithmetic_expression(self, aeval):
        """Basic arithmetic expression evaluates correctly."""
        success, result, _ = evaluate_expression(aeval, "2 + 3", {})
        assert success
        assert result == 5

    def test_comparison_expression(self, aeval):
        """Comparison expression evaluates to boolean."""
        success, result, _ = evaluate_expression(aeval, "x > 10", {"x": 15})
        assert success
        assert result is True

    def test_comparison_false(self, aeval):
        """Comparison returns False when condition not met."""
        success, result, _ = evaluate_expression(aeval, "x > 10", {"x": 5})
        assert success
        assert result is False

    def test_dict_access_in_expression(self, aeval):
        """Expression can access dict keys."""
        data = {"scores": {"math": 90, "english": 85}}
        success, result, _ = evaluate_expression(aeval, "scores['math']", data)
        assert success
        assert result == 90

    def test_array_operations(self, aeval):
        """Expression can use array operations like len and sum."""
        data = {"items": [1, 2, 3, 4, 5]}
        success, result, _ = evaluate_expression(aeval, "sum(items)", data)
        assert success
        assert result == 15

    def test_len_builtin(self, aeval):
        """len() works in expressions."""
        data = {"cards": ["A", "K", "Q"]}
        success, result, _ = evaluate_expression(aeval, "len(cards)", data)
        assert success
        assert result == 3

    def test_invalid_expression(self, aeval):
        """Invalid expression returns failure."""
        success, _, error = evaluate_expression(aeval, "undefined_var + 1", {})
        assert not success
        assert error  # Should have an error message

    def test_context_isolation_between_calls(self, aeval):
        """Context from one call doesn't leak into next."""
        evaluate_expression(aeval, "x + 1", {"x": 10})
        # x should not be available in a clean context
        success, _, _ = evaluate_expression(aeval, "x + 1", {})
        assert not success


# =============================================================================
# Expression Rule Validation
# =============================================================================

class TestExpressionRules:
    """Tests for validate_expression_rules()."""

    def test_passing_rule(self, aeval):
        """Rule that evaluates to True produces no errors."""
        rules = [{"name": "positive", "expr": "value > 0", "error": "Must be positive"}]
        errors, warnings = validate_expression_rules({"value": 5}, rules, aeval, 1)
        assert errors == []
        assert warnings == []

    def test_failing_rule(self, aeval):
        """Rule that evaluates to False produces an error."""
        rules = [{"name": "positive", "expr": "value > 0", "error": "Must be positive, got {value}"}]
        errors, warnings = validate_expression_rules({"value": -1}, rules, aeval, 1)
        assert len(errors) == 1
        assert "Must be positive, got -1" in errors[0]["message"]

    def test_warning_level(self, aeval):
        """Rule with level='warning' produces a warning, not an error."""
        rules = [{
            "name": "soft_check",
            "expr": "value > 0",
            "error": "Value should be positive",
            "level": "warning",
        }]
        errors, warnings = validate_expression_rules({"value": -1}, rules, aeval, 1)
        assert errors == []
        assert len(warnings) == 1

    def test_when_clause_skips_rule(self, aeval):
        """Rule with when clause is skipped when condition is False."""
        rules = [{
            "name": "check_details",
            "expr": "len(details) > 0",
            "error": "Details required",
            "when": "has_details == True",
        }]
        errors, warnings = validate_expression_rules(
            {"has_details": False, "details": ""},
            rules, aeval, 1
        )
        assert errors == []

    def test_when_clause_runs_rule(self, aeval):
        """Rule with when clause runs when condition is True."""
        rules = [{
            "name": "check_details",
            "expr": "len(details) > 0",
            "error": "Details required",
            "when": "has_details == True",
        }]
        errors, warnings = validate_expression_rules(
            {"has_details": True, "details": ""},
            rules, aeval, 1
        )
        assert len(errors) == 1

    def test_falsy_numeric_result_fails_rule(self, aeval):
        """Falsy non-bool expression results (e.g., 0) must fail validation."""
        rules = [{
            "name": "nonzero_required",
            "expr": "value",
            "error": "Value must be truthy",
        }]
        errors, warnings = validate_expression_rules({"value": 0}, rules, aeval, 1)
        assert warnings == []
        assert len(errors) == 1
        assert errors[0]["rule"] == "nonzero_required"
        assert "Value must be truthy" in errors[0]["message"]

    def test_error_message_template_substitution(self, aeval):
        """Error template substitutes actual values from data."""
        rules = [{
            "name": "range_check",
            "expr": "score >= 0",
            "error": "Score {score} is invalid for player {name}",
        }]
        errors, _ = validate_expression_rules(
            {"score": -5, "name": "Bob"},
            rules, aeval, 1
        )
        assert "Score -5 is invalid for player Bob" in errors[0]["message"]

    def test_when_clause_evaluation_error_becomes_warning(self, aeval):
        """Invalid `when` expression should warn and skip the rule."""
        rules = [{
            "name": "guarded_rule",
            "expr": "value > 0",
            "error": "Must be positive",
            "when": "undefined_symbol > 0",
        }]
        errors, warnings = validate_expression_rules({"value": -1}, rules, aeval, 1)
        assert errors == []
        assert len(warnings) == 1
        assert "when clause failed to evaluate" in warnings[0]["message"]
        assert warnings[0]["rule"] == "guarded_rule"

    def test_expression_evaluation_error_includes_rule_message(self, aeval):
        """Rule expression errors should preserve user-facing context."""
        rules = [{
            "name": "score_rule",
            "expr": "score > threshold",
            "error": "Score check failed for {player}",
        }]
        errors, warnings = validate_expression_rules({"score": 10, "player": "Ada"}, rules, aeval, 1)
        assert warnings == []
        assert len(errors) == 1
        assert "Score check failed for Ada" in errors[0]["message"]
        assert "expression error" in errors[0]["message"]


# =============================================================================
# Full Line Validation (validate_line)
# =============================================================================

class TestValidateLine:
    """Tests for validate_line() which chains all validations."""

    def test_valid_data_passes(self, aeval, blackjack_validation_config):
        """Complete valid data passes all checks."""
        data = {
            "action_log": [{"action": "hit"}],
            "player_cards_used": ["A", "K"],
            "dealer_cards_used": ["10", "7"],
            "player_final_total": 21,
            "dealer_final_total": 17,
            "player_busted": False,
            "dealer_busted": False,
            "result": "player_wins",
            "first_action": "stand",
            "player_initial_total": 21,
        }
        is_valid, errors, warnings = validate_line(
            data, blackjack_validation_config, aeval, 1
        )
        assert is_valid
        assert errors == []

    def test_missing_required_stops_further_validation(self, aeval):
        """Missing required fields short-circuits further validation."""
        config = {
            "required": ["name"],
            "types": {"name": "string"},
            "rules": [{"name": "check", "expr": "len(name) > 0", "error": "Empty name"}],
        }
        is_valid, errors, _ = validate_line({}, config, aeval, 1)
        assert not is_valid
        assert len(errors) == 1  # Only the required error, no type/rule errors

    def test_type_error_still_runs_rules(self, aeval):
        """Type errors don't short-circuit rule validation."""
        config = {
            "required": ["score"],
            "types": {"score": "integer"},
            "rules": [{"name": "positive", "expr": "score > 0", "error": "Bad score"}],
        }
        data = {"score": "not_a_number"}
        is_valid, errors, _ = validate_line(data, config, aeval, 1)
        assert not is_valid
        rules = {e["rule"] for e in errors}
        assert "type_score" in rules
        assert "positive" in rules


# =============================================================================
# Process Line (JSON parsing + validation)
# =============================================================================

class TestProcessLine:
    """Tests for process_line() which handles raw JSON input."""

    def test_valid_json_line(self, aeval):
        """Valid JSON line is parsed and validated."""
        config = {"required": ["x"], "types": {"x": "integer"}}
        data, is_valid, warnings, errors = process_line(
            '{"x": 42}', config, aeval, 1
        )
        assert is_valid
        assert data == {"x": 42}

    def test_empty_line_skipped(self, aeval):
        """Empty lines are skipped (valid, no data)."""
        data, is_valid, warnings, errors = process_line("", {}, aeval, 1)
        assert is_valid
        assert data is None

    def test_whitespace_line_skipped(self, aeval):
        """Whitespace-only lines are skipped."""
        data, is_valid, warnings, errors = process_line("   \n", {}, aeval, 1)
        assert is_valid
        assert data is None

    def test_malformed_json(self, aeval):
        """Malformed JSON produces parse error."""
        data, is_valid, warnings, errors = process_line(
            "{not valid json}", {}, aeval, 1
        )
        assert not is_valid
        assert data is None
        assert len(errors) == 1
        assert errors[0]["rule"] == "json_parse"


# =============================================================================
# Config Helpers
# =============================================================================

class TestConfigHelpers:
    """Tests for get_step_validation_config()."""

    def test_get_existing_step(self):
        """Retrieves validation config for an existing step."""
        config = {
            "validation": {
                "play_hand": {"required": ["result"]},
            }
        }
        result = get_step_validation_config(config, "play_hand")
        assert result == {"required": ["result"]}

    def test_get_missing_step(self):
        """Returns empty dict for missing step."""
        config = {"validation": {}}
        result = get_step_validation_config(config, "nonexistent")
        assert result == {}

    def test_get_step_no_validation_section(self):
        """Returns empty dict when config has no validation section."""
        result = get_step_validation_config({}, "any_step")
        assert result == {}


# =============================================================================
# Format Error Message
# =============================================================================

class TestFormatErrorMessage:
    """Tests for format_error_message()."""

    def test_simple_substitution(self):
        """Substitutes simple variables."""
        msg = format_error_message("Got {value}", {"value": 42})
        assert msg == "Got 42"

    def test_multiple_substitutions(self):
        """Substitutes multiple variables."""
        msg = format_error_message("{a} + {b} = {c}", {"a": 1, "b": 2, "c": 3})
        assert msg == "1 + 2 = 3"

    def test_missing_variable_kept(self):
        """Unmatched variables are kept as-is."""
        msg = format_error_message("Got {unknown}", {"value": 42})
        assert msg == "Got {unknown}"

    def test_computed_variable(self):
        """Special _computed variable works."""
        msg = format_error_message("Computed: {_computed}", {}, computed=99)
        assert msg == "Computed: 99"
