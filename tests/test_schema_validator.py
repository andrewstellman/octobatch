"""
Tests for the JSON Schema validator (scripts/schema_validator.py).

Covers:
- load_schema: file loading and JSON parsing
- create_validator: Draft202012Validator creation and schema validation
- _resolve_schema_node: $ref resolution including circular ref guard
- _coerce_value: str->int, str->float, str->bool, float->int coercion + NaN guard
- coerce_data: recursive coercion including leaf, enum, object, string->array, array
- _unwrap_response: double-wrapped JSON with markdown fences, trailing commas, parse failures
- format_validation_error / format_all_errors: error formatting including nested context
- validate_line: empty line, valid JSON, invalid JSON, validation errors
- process_stream: stream processing in normal and strict modes
"""

import json
import math
import sys
from pathlib import Path

import pytest

# Add scripts directory to path so schema_validator module is importable
sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

from schema_validator import (
    load_schema,
    create_validator,
    _resolve_schema_node,
    _coerce_value,
    coerce_data,
    _unwrap_response,
    format_validation_error,
    format_all_errors,
    validate_line,
    process_stream,
)


# =============================================================================
# Fixtures
# =============================================================================

@pytest.fixture
def simple_schema():
    """A simple JSON Schema for testing."""
    return {
        "type": "object",
        "required": ["name", "age"],
        "properties": {
            "name": {"type": "string"},
            "age": {"type": "integer"},
        },
    }


@pytest.fixture
def schema_with_defs():
    """Schema with $defs for testing $ref resolution."""
    return {
        "type": "object",
        "required": ["score"],
        "properties": {
            "score": {"$ref": "#/$defs/scoreType"},
        },
        "$defs": {
            "scoreType": {
                "type": "integer",
                "minimum": 0,
                "maximum": 100,
            },
        },
    }


@pytest.fixture
def enum_schema():
    """Schema with enum values for testing enum normalization."""
    return {
        "type": "object",
        "required": ["status"],
        "properties": {
            "status": {
                "type": "string",
                "enum": ["active", "inactive", "pending"],
            },
        },
    }


@pytest.fixture
def array_schema():
    """Schema with array type for testing string->array coercion."""
    return {
        "type": "object",
        "properties": {
            "tags": {
                "type": "array",
                "items": {"type": "string"},
            },
        },
    }


@pytest.fixture
def simple_validator(simple_schema):
    """A validator created from simple_schema."""
    return create_validator(simple_schema)


# =============================================================================
# load_schema / create_validator
# =============================================================================

class TestLoadSchema:
    def test_load_valid_schema(self, tmp_path):
        schema_file = tmp_path / "schema.json"
        schema_file.write_text(json.dumps({"type": "object"}))
        schema = load_schema(schema_file)
        assert schema["type"] == "object"

    def test_load_missing_schema(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            load_schema(tmp_path / "nope.json")

    def test_create_validator_valid(self, simple_schema):
        v = create_validator(simple_schema)
        assert v is not None

    def test_create_validator_invalid_schema(self):
        from jsonschema.exceptions import SchemaError
        with pytest.raises(SchemaError):
            create_validator({"type": "bogus"})


# =============================================================================
# _resolve_schema_node
# =============================================================================

class TestResolveSchemaNode:
    def test_no_ref(self):
        node = {"type": "integer"}
        assert _resolve_schema_node(node, {}) == node

    def test_resolve_ref(self):
        defs = {"score": {"type": "integer"}}
        node = {"$ref": "#/$defs/score"}
        assert _resolve_schema_node(node, defs) == {"type": "integer"}

    def test_missing_def(self):
        node = {"$ref": "#/$defs/missing"}
        assert _resolve_schema_node(node, {}) == node

    def test_circular_ref(self):
        defs = {"a": {"$ref": "#/$defs/a"}}
        node = {"$ref": "#/$defs/a"}
        result = _resolve_schema_node(node, defs)
        assert "$ref" in result

    def test_non_local_ref(self):
        node = {"$ref": "http://example.com/schema.json"}
        assert _resolve_schema_node(node, {}) == node

    def test_other_local_ref(self):
        node = {"$ref": "#/definitions/foo"}
        assert _resolve_schema_node(node, {}) == node


# =============================================================================
# _coerce_value
# =============================================================================

class TestCoerceValue:
    def test_str_to_int(self):
        val, ok = _coerce_value("42", "integer", "$.x")
        assert val == 42 and ok

    def test_str_to_int_fail(self):
        val, ok = _coerce_value("abc", "integer", "$.x")
        assert val == "abc" and not ok

    def test_float_to_int(self):
        val, ok = _coerce_value(3.0, "integer", "$.x")
        assert val == 3 and ok

    def test_float_nan_not_coerced(self):
        """NaN is left unchanged so schema validation can report structured errors."""
        val, ok = _coerce_value(float("nan"), "integer", "$.x")
        assert isinstance(val, float)
        assert math.isnan(val)
        assert ok is False

    def test_float_inf_not_coerced(self):
        """Infinity is left unchanged so schema validation can report structured errors."""
        val, ok = _coerce_value(float("inf"), "integer", "$.x")
        assert val == float("inf")
        assert ok is False

    def test_float_neg_inf_not_coerced(self):
        """-Infinity with integer type returns unchanged, no exception."""
        val, ok = _coerce_value(float("-inf"), "integer", "$.x")
        assert val == float("-inf")
        assert ok is False

    def test_float_nan_number_not_coerced(self):
        """NaN with number type returns unchanged — NaN is not valid JSON."""
        val, ok = _coerce_value(float("nan"), "number", "$.x")
        assert isinstance(val, float)
        assert math.isnan(val)
        assert ok is False

    def test_float_inf_number_not_coerced(self):
        """Infinity with number type returns unchanged — not valid JSON."""
        val, ok = _coerce_value(float("inf"), "number", "$.x")
        assert val == float("inf")
        assert ok is False

    def test_float_neg_inf_number_not_coerced(self):
        """-Infinity with number type returns unchanged — not valid JSON."""
        val, ok = _coerce_value(float("-inf"), "number", "$.x")
        assert val == float("-inf")
        assert ok is False

    def test_str_to_float(self):
        val, ok = _coerce_value("3.14", "number", "$.x")
        assert val == 3.14 and ok

    def test_str_to_float_fail(self):
        val, ok = _coerce_value("xyz", "number", "$.x")
        assert val == "xyz" and not ok

    def test_str_to_bool_true(self):
        val, ok = _coerce_value("True", "boolean", "$.x")
        assert val is True and ok

    def test_str_to_bool_false(self):
        val, ok = _coerce_value("false", "boolean", "$.x")
        assert val is False and ok

    def test_str_to_bool_not_bool(self):
        val, ok = _coerce_value("yes", "boolean", "$.x")
        assert val == "yes" and not ok

    def test_unknown_type(self):
        val, ok = _coerce_value("x", "string", "$.x")
        assert val == "x" and not ok

    def test_int_not_coerced_to_int(self):
        val, ok = _coerce_value(42, "integer", "$.x")
        assert val == 42 and not ok

    def test_bool_not_coerced_to_bool(self):
        val, ok = _coerce_value(True, "boolean", "$.x")
        assert val is True and not ok

    def test_float_with_fraction_not_coerced_to_int(self):
        val, ok = _coerce_value(3.5, "integer", "$.x")
        assert val == 3.5 and not ok


# =============================================================================
# coerce_data
# =============================================================================

class TestCoerceData:
    def test_leaf_integer(self):
        assert coerce_data("5", {"type": "integer"}, {}) == 5

    def test_leaf_number(self):
        assert coerce_data("2.5", {"type": "number"}, {}) == 2.5

    def test_leaf_boolean(self):
        assert coerce_data("true", {"type": "boolean"}, {}) is True

    def test_enum_normalization_shadow_prefix(self):
        schema = {"enum": ["integration", "fragmentation"]}
        assert coerce_data("shadow integration", schema, {}) == "integration"

    def test_enum_normalization_pipe_delimiter(self):
        schema = {"enum": ["active", "inactive"]}
        assert coerce_data("active | extra", schema, {}) == "active"

    def test_enum_normalization_case_insensitive(self):
        schema = {"enum": ["ACTIVE", "INACTIVE"]}
        assert coerce_data("active", schema, {}) == "ACTIVE"

    def test_enum_no_match(self):
        schema = {"enum": ["a", "b"]}
        assert coerce_data("zzz", schema, {}) == "zzz"

    def test_enum_already_valid(self):
        schema = {"enum": ["active"]}
        assert coerce_data("active", schema, {}) == "active"

    def test_object_traversal(self):
        schema = {
            "type": "object",
            "properties": {"score": {"type": "integer"}},
        }
        result = coerce_data({"score": "10"}, schema, {})
        assert result["score"] == 10

    def test_object_additional_properties(self):
        schema = {
            "type": "object",
            "properties": {},
            "additionalProperties": {"type": "integer"},
        }
        result = coerce_data({"x": "5"}, schema, {})
        assert result["x"] == 5

    def test_object_additional_properties_not_dict(self):
        schema = {
            "type": "object",
            "properties": {"name": {"type": "string"}},
            "additionalProperties": True,
        }
        result = coerce_data({"name": "a", "extra": "z"}, schema, {})
        assert result["extra"] == "z"

    def test_string_to_array_json_parse(self):
        schema = {"type": "array", "items": {"type": "integer"}}
        result = coerce_data('[1, 2, 3]', schema, {})
        assert result == [1, 2, 3]

    def test_string_to_array_json_parse_with_coercion(self):
        schema = {"type": "array", "items": {"type": "integer"}}
        result = coerce_data('["10", "20"]', schema, {})
        assert result == [10, 20]

    def test_string_to_array_json_parse_no_items_schema(self):
        schema = {"type": "array"}
        result = coerce_data('[1, 2]', schema, {})
        assert result == [1, 2]

    def test_string_to_array_tag_wrap(self):
        schema = {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"tag": {"type": "string"}},
            },
        }
        result = coerce_data("SURRENDER", schema, {})
        assert result == [{"tag": "SURRENDER"}]

    def test_string_to_array_generic_fallback(self):
        schema = {"type": "array", "items": {"type": "string"}}
        result = coerce_data("hello", schema, {})
        assert result == ["hello"]

    def test_string_to_array_non_list_json(self):
        # json.loads succeeds but result is not a list -> falls through to generic wrap
        # The original data string (with quotes) gets wrapped, not the parsed result
        schema = {"type": "array", "items": {"type": "string"}}
        result = coerce_data('"just a string"', schema, {})
        assert result == ['"just a string"']

    def test_string_to_array_items_not_dict(self):
        schema = {"type": "array", "items": True}
        result = coerce_data("hello", schema, {})
        assert result == ["hello"]

    def test_string_to_array_object_items_no_tag(self):
        schema = {
            "type": "array",
            "items": {"type": "object", "properties": {"value": {"type": "integer"}}},
        }
        result = coerce_data("test", schema, {})
        assert result == ["test"]

    def test_array_traversal(self):
        schema = {
            "type": "array",
            "items": {"type": "object", "properties": {"n": {"type": "integer"}}},
        }
        data = [{"n": "1"}, {"n": "2"}]
        result = coerce_data(data, schema, {})
        assert result[0]["n"] == 1

    def test_no_type_dict_with_properties(self):
        schema = {"properties": {"x": {"type": "integer"}}}
        result = coerce_data({"x": "5"}, schema, {})
        assert result["x"] == 5

    def test_no_type_dict_with_additional_properties(self):
        schema = {
            "properties": {"a": {"type": "string"}},
            "additionalProperties": {"type": "integer"},
        }
        result = coerce_data({"a": "hi", "b": "99"}, schema, {})
        assert result["b"] == 99

    def test_no_type_dict_no_properties(self):
        schema = {"description": "anything"}
        data = {"x": "y"}
        result = coerce_data(data, schema, {})
        assert result == {"x": "y"}

    def test_no_type_non_dict(self):
        schema = {}
        assert coerce_data(42, schema, {}) == 42

    def test_ref_resolution_in_coercion(self):
        defs = {"scoreType": {"type": "integer"}}
        schema = {
            "type": "object",
            "properties": {"score": {"$ref": "#/$defs/scoreType"}},
        }
        result = coerce_data({"score": "10"}, schema, defs)
        assert result["score"] == 10

    def test_enum_not_string_data(self):
        schema = {"enum": ["a", "b"]}
        assert coerce_data(42, schema, {}) == 42


# =============================================================================
# _unwrap_response
# =============================================================================

class TestUnwrapResponse:
    def test_not_a_dict(self):
        assert _unwrap_response([1, 2], {}) == [1, 2]

    def test_no_response_key(self):
        data = {"name": "Alice"}
        assert _unwrap_response(data, {"required": ["name"]}) == data

    def test_response_not_string(self):
        data = {"response": 42}
        assert _unwrap_response(data, {"required": ["missing"]}) == data

    def test_no_required(self):
        data = {"response": '{"a": 1}'}
        assert _unwrap_response(data, {}) == data

    def test_all_required_present(self):
        data = {"response": "x", "name": "Alice"}
        assert _unwrap_response(data, {"required": ["name"]}) == data

    def test_unwrap_plain_json(self):
        inner = json.dumps({"scores": [1, 2]})
        data = {"response": inner}
        schema = {"required": ["scores"]}
        result = _unwrap_response(data, schema)
        assert result["scores"] == [1, 2]
        assert "response" not in result

    def test_unwrap_markdown_fences(self):
        inner = json.dumps({"scores": [1]})
        data = {"response": f"```json\n{inner}\n```"}
        schema = {"required": ["scores"]}
        result = _unwrap_response(data, schema)
        assert result["scores"] == [1]

    def test_unwrap_markdown_no_newline(self):
        data = {"response": "```{broken```"}
        schema = {"required": ["missing"]}
        result = _unwrap_response(data, schema)
        assert result == data

    def test_trailing_comma_cleaned(self, capsys):
        inner = '{"scores": [1, 2,]}'
        data = {"response": inner}
        schema = {"required": ["scores"]}
        result = _unwrap_response(data, schema)
        assert result["scores"] == [1, 2]
        captured = capsys.readouterr()
        assert "trailing commas" in captured.err

    def test_parse_failure(self, capsys):
        data = {"response": "not json at all"}
        schema = {"required": ["missing"]}
        result = _unwrap_response(data, schema)
        assert result == data
        captured = capsys.readouterr()
        assert "Unwrap parse failed" in captured.err

    def test_inner_not_dict(self):
        data = {"response": "[1, 2, 3]"}
        schema = {"required": ["missing"]}
        result = _unwrap_response(data, schema)
        assert result == data

    def test_no_resolved_keys(self):
        inner = json.dumps({"other": 1})
        data = {"response": inner}
        schema = {"required": ["missing"]}
        result = _unwrap_response(data, schema)
        assert result == data

    def test_response_key_preserved_if_in_inner(self):
        inner = json.dumps({"scores": [1], "response": "kept"})
        data = {"response": json.dumps({"scores": [1], "response": "kept"})}
        schema = {"required": ["scores"]}
        result = _unwrap_response(data, schema)
        assert result["scores"] == [1]
        assert result["response"] == "kept"

    def test_existing_keys_not_overwritten(self):
        inner = json.dumps({"scores": [1], "name": "inner"})
        data = {"response": inner, "name": "outer"}
        schema = {"required": ["scores"]}
        result = _unwrap_response(data, schema)
        assert result["name"] == "outer"
        assert result["scores"] == [1]

    def test_empty_string_response(self):
        data = {"response": ""}
        schema = {"required": ["missing"]}
        result = _unwrap_response(data, schema)
        assert result == data

    def test_unwrap_ending_backticks(self):
        # Test where inner_str ends with ``` but doesn't start with ```
        inner = json.dumps({"scores": [1]})
        data = {"response": f"{inner}```"}
        schema = {"required": ["scores"]}
        result = _unwrap_response(data, schema)
        assert result["scores"] == [1]


# =============================================================================
# format_validation_error / format_all_errors
# =============================================================================

class TestFormatErrors:
    def test_format_validation_error(self):
        from jsonschema import ValidationError
        err = ValidationError(
            message="too small",
            validator="minimum",
            validator_value=0,
            instance=-1,
            path=["score"],
            schema_path=[],
        )
        result = format_validation_error(err)
        assert result["path"] == ["score"]
        assert result["rule"] == "schema_minimum"
        assert result["message"] == "too small"
        assert result["instance_type"] == "int"

    def test_format_validation_error_empty_path(self):
        from jsonschema import ValidationError
        err = ValidationError("bad", validator="type", instance=None, path=[], schema_path=[])
        result = format_validation_error(err)
        assert result["path"] == ["$"]
        assert result["instance_type"] == "null"

    def test_format_validation_error_no_validator(self):
        from jsonschema import ValidationError
        err = ValidationError("msg", validator=None, instance="x", path=[], schema_path=[])
        result = format_validation_error(err)
        assert result["rule"] == "schema"

    def test_format_all_errors_with_context(self):
        from jsonschema import ValidationError
        parent = ValidationError("parent", validator="anyOf", instance={}, path=["data"], schema_path=[])
        child = ValidationError("child", validator="type", instance=42, path=["value"], schema_path=[])
        parent.context = [child]
        result = format_all_errors([parent])
        assert len(result) == 2
        assert "parent_path" in result[1]

    def test_format_all_errors_no_context(self):
        from jsonschema import ValidationError
        err = ValidationError("msg", validator="type", instance=1, path=[], schema_path=[])
        result = format_all_errors([err])
        assert len(result) == 1


# =============================================================================
# validate_line
# =============================================================================

class TestValidateLine:
    def test_empty_line(self, simple_validator, simple_schema):
        data, errors = validate_line("", simple_validator, simple_schema, 1)
        assert data is None and errors is None

    def test_whitespace_line(self, simple_validator, simple_schema):
        data, errors = validate_line("   \t  ", simple_validator, simple_schema, 1)
        assert data is None and errors is None

    def test_valid_json(self, simple_validator, simple_schema):
        line = json.dumps({"name": "Alice", "age": 30})
        data, errors = validate_line(line, simple_validator, simple_schema, 1)
        assert data == {"name": "Alice", "age": 30} and errors is None

    def test_invalid_json(self, simple_validator, simple_schema):
        data, errors = validate_line("{not valid}", simple_validator, simple_schema, 1)
        assert data is None
        assert errors[0]["rule"] == "schema_json_parse"
        assert "line_position" in errors[0]

    def test_validation_errors(self, simple_validator, simple_schema):
        line = json.dumps({"name": "Alice"})
        data, errors = validate_line(line, simple_validator, simple_schema, 1)
        assert data == {"name": "Alice"}
        assert errors is not None
        assert errors[0]["validator"] == "required"
        assert "age" in errors[0]["message"]

    def test_non_finite_nan_reports_structured_validation_error(self):
        schema = {
            "type": "object",
            "required": ["count"],
            "properties": {"count": {"type": "integer"}},
        }
        v = create_validator(schema)
        line = '{"count": NaN}'
        data, errors = validate_line(line, v, schema, 1)
        assert data is not None
        assert errors is not None
        assert any(err["rule"] == "schema_non_finite_number" for err in errors)
        assert any(err.get("validator") == "type" for err in errors)

    def test_non_finite_inf_reports_structured_validation_error(self):
        schema = {
            "type": "object",
            "required": ["count"],
            "properties": {"count": {"type": "integer"}},
        }
        v = create_validator(schema)
        line = '{"count": Infinity}'
        data, errors = validate_line(line, v, schema, 1)
        assert data is not None
        assert errors is not None
        assert any(err["rule"] == "schema_non_finite_number" for err in errors)
        assert any(err.get("validator") == "type" for err in errors)

    def test_string_nan_not_coerced_to_number(self):
        schema = {
            "type": "object",
            "required": ["score"],
            "properties": {"score": {"type": "number"}},
        }
        v = create_validator(schema)
        data, errors = validate_line(json.dumps({"score": "NaN"}), v, schema, 1)
        assert data == {"score": "NaN"}
        assert errors is not None
        assert errors[0]["validator"] == "type"
        assert errors[0]["path"] == ["score"]

    def test_string_infinity_not_coerced_to_number(self):
        schema = {
            "type": "object",
            "required": ["score"],
            "properties": {"score": {"type": "number"}},
        }
        v = create_validator(schema)
        data, errors = validate_line(json.dumps({"score": "Infinity"}), v, schema, 1)
        assert data == {"score": "Infinity"}
        assert errors is not None
        assert errors[0]["validator"] == "type"
        assert errors[0]["path"] == ["score"]

    def test_bare_infinity_number_rejected(self):
        schema = {
            "type": "object",
            "required": ["score"],
            "properties": {"score": {"type": "number"}},
        }
        v = create_validator(schema)
        data, errors = validate_line('{"score": Infinity}', v, schema, 1)
        assert data is not None
        assert errors is not None
        assert any(err["rule"] == "schema_non_finite_number" for err in errors)
        assert any("$.score" in err["path"][0] for err in errors if err["rule"] == "schema_non_finite_number")

    def test_coercion_during_validation(self, capsys):
        schema = {"type": "object", "required": ["count"], "properties": {"count": {"type": "integer"}}}
        v = create_validator(schema)
        data, errors = validate_line(json.dumps({"count": "42"}), v, schema, 1)
        assert data == {"count": 42} and errors is None

    def test_unwrap_during_validation(self):
        schema = {"type": "object", "required": ["name", "score"],
                  "properties": {"name": {"type": "string"}, "score": {"type": "integer"}}}
        v = create_validator(schema)
        inner = json.dumps({"name": "Alice", "score": 95})
        line = json.dumps({"response": inner})
        data, errors = validate_line(line, v, schema, 1)
        assert data["name"] == "Alice" and errors is None

    def test_non_dict_data(self):
        schema = {"type": "array", "items": {"type": "integer"}}
        v = create_validator(schema)
        data, errors = validate_line(json.dumps([1, 2, 3]), v, schema, 1)
        assert data == [1, 2, 3] and errors is None

    def test_defs_used_in_coercion(self):
        schema = {
            "type": "object", "required": ["score"],
            "properties": {"score": {"$ref": "#/$defs/scoreType"}},
            "$defs": {"scoreType": {"type": "integer"}},
        }
        v = create_validator(schema)
        data, errors = validate_line(json.dumps({"score": "42"}), v, schema, 1)
        assert data["score"] == 42 and errors is None


# =============================================================================
# process_stream
# =============================================================================

class TestProcessStream:
    def test_normal_mode_valid(self, simple_validator, simple_schema, capsys):
        lines = [json.dumps({"name": "Alice", "age": 30}) + "\n",
                 json.dumps({"name": "Bob", "age": 25}) + "\n"]
        valid, errors, collected = process_stream(iter(lines), simple_validator, simple_schema)
        assert valid == 2 and errors == 0 and collected == []
        captured = capsys.readouterr()
        assert len([line for line in captured.out.strip().split("\n") if line]) == 2

    def test_normal_mode_invalid(self, simple_validator, simple_schema, capsys):
        lines = [json.dumps({"name": "Alice", "age": 30}) + "\n",
                 json.dumps({"name": "Bob"}) + "\n",
                 json.dumps({"name": "Carol", "age": 22}) + "\n"]
        valid, errors, collected = process_stream(iter(lines), simple_validator, simple_schema)
        assert valid == 2 and errors == 1
        captured = capsys.readouterr()
        assert "failure_stage" in captured.err

    def test_normal_mode_json_parse_error(self, simple_validator, simple_schema, capsys):
        lines = ["{bad json}\n", json.dumps({"name": "Alice", "age": 30}) + "\n"]
        valid, errors, _ = process_stream(iter(lines), simple_validator, simple_schema)
        assert valid == 1 and errors == 1
        captured = capsys.readouterr()
        assert "raw_response" in captured.err

    def test_strict_mode_all_valid(self, simple_validator, simple_schema, capsys):
        lines = [json.dumps({"name": "A", "age": 1}) + "\n",
                 json.dumps({"name": "B", "age": 2}) + "\n"]
        valid, errors, collected = process_stream(iter(lines), simple_validator, simple_schema, strict=True)
        assert valid == 2 and errors == 0 and len(collected) == 2
        assert capsys.readouterr().out == ""

    def test_strict_mode_stops_on_first_error(self, simple_validator, simple_schema):
        lines = [json.dumps({"name": "A", "age": 1}) + "\n",
                 json.dumps({"name": "B"}) + "\n",
                 json.dumps({"name": "C", "age": 3}) + "\n"]
        valid, errors, collected = process_stream(iter(lines), simple_validator, simple_schema, strict=True)
        assert valid == 1 and errors == 1 and collected == []

    def test_empty_stream(self, simple_validator, simple_schema):
        valid, errors, _ = process_stream(iter([]), simple_validator, simple_schema)
        assert valid == 0 and errors == 0

    def test_blank_lines_skipped(self, simple_validator, simple_schema):
        lines = ["\n", "  \n", json.dumps({"name": "A", "age": 1}) + "\n", "\n"]
        valid, errors, _ = process_stream(iter(lines), simple_validator, simple_schema)
        assert valid == 1 and errors == 0

    def test_failure_record_has_unit_id(self, capsys):
        schema = {"type": "object", "required": ["name", "score"],
                  "properties": {"name": {"type": "string"}, "score": {"type": "integer"}}}
        v = create_validator(schema)
        lines = [json.dumps({"unit_id": "test_123", "name": "Alice"}) + "\n"]
        process_stream(iter(lines), v, schema)
        captured = capsys.readouterr()
        stderr_data = json.loads(captured.err.strip())
        assert stderr_data["unit_id"] == "test_123"
        assert stderr_data["failure_stage"] == "schema_validation"

    def test_failure_record_has_retry_count(self, capsys):
        schema = {"type": "object", "required": ["name"],
                  "properties": {"name": {"type": "string"}}}
        v = create_validator(schema)
        lines = [json.dumps({"retry_count": 2, "unit_id": "u1"}) + "\n"]
        process_stream(iter(lines), v, schema)
        captured = capsys.readouterr()
        stderr_data = json.loads(captured.err.strip())
        assert stderr_data["retry_count"] == 2

    def test_json_parse_error_failure_record(self, capsys):
        schema = {"type": "object", "required": ["x"], "properties": {"x": {"type": "integer"}}}
        v = create_validator(schema)
        process_stream(iter(["{broken json}\n"]), v, schema)
        captured = capsys.readouterr()
        stderr_data = json.loads(captured.err.strip())
        assert stderr_data["unit_id"] is None
        assert stderr_data["input"] is None
        assert stderr_data["raw_response"] == "{broken json}"
        assert stderr_data["retry_count"] == 0

    def test_strict_mode_json_parse_error_stops(self):
        schema = {"type": "object", "required": ["x"], "properties": {"x": {"type": "integer"}}}
        v = create_validator(schema)
        valid, errors, collected = process_stream(
            iter(["{broken}\n", json.dumps({"x": 1}) + "\n"]), v, schema, strict=True)
        assert valid == 0 and errors == 1 and collected == []


# =============================================================================
# Integration
# =============================================================================

class TestIntegration:
    def test_full_pipeline_with_coercion(self, tmp_path):
        schema_data = {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "type": "object", "required": ["name", "score", "active"],
            "properties": {"name": {"type": "string"}, "score": {"type": "integer"},
                           "active": {"type": "boolean"}},
        }
        f = tmp_path / "schema.json"
        f.write_text(json.dumps(schema_data))
        schema = load_schema(f)
        v = create_validator(schema)
        data, errors = validate_line(json.dumps({"name": "A", "score": "95", "active": "true"}), v, schema, 1)
        assert errors is None and data["score"] == 95 and data["active"] is True

    def test_full_pipeline_with_unwrap(self):
        schema = {"type": "object", "required": ["items", "count"],
                  "properties": {"items": {"type": "array", "items": {"type": "string"}},
                                 "count": {"type": "integer"}}}
        v = create_validator(schema)
        inner = json.dumps({"items": ["a", "b"], "count": "2"})
        line = json.dumps({"response": f"```json\n{inner}\n```"})
        data, errors = validate_line(line, v, schema, 1)
        assert errors is None and data["items"] == ["a", "b"] and data["count"] == 2

    def test_stream_with_mixed_validity(self, capsys):
        schema = {"type": "object", "required": ["x"], "properties": {"x": {"type": "integer"}}}
        v = create_validator(schema)
        lines = [json.dumps({"x": 1}) + "\n", "\n", json.dumps({"x": "2"}) + "\n",
                 "{bad}\n", json.dumps({"y": 1}) + "\n", json.dumps({"x": 3}) + "\n"]
        valid, errors, _ = process_stream(iter(lines), v, schema)
        assert valid == 3 and errors == 2

    def test_array_items_with_refs_coercion(self):
        schema = {
            "type": "object", "required": ["scores"],
            "properties": {"scores": {"type": "array", "items": {"$ref": "#/$defs/scoreItem"}}},
            "$defs": {"scoreItem": {"type": "object", "properties": {"value": {"type": "integer"}}}},
        }
        v = create_validator(schema)
        data, errors = validate_line(
            json.dumps({"scores": [{"value": "10"}, {"value": "20"}]}), v, schema, 1)
        assert errors is None and data["scores"][0]["value"] == 10

    def test_enum_normalization_in_pipeline(self):
        schema = {"type": "object", "required": ["status"],
                  "properties": {"status": {"type": "string", "enum": ["active", "inactive", "pending"]}}}
        v = create_validator(schema)
        data, errors = validate_line(json.dumps({"status": "Shadow Active | extra info"}), v, schema, 1)
        assert errors is None and data["status"] == "active"
