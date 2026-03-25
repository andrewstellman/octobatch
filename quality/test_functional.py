import json
import math
import sys
from pathlib import Path

import pytest
import yaml

# Match the project's existing import pattern for script modules.
sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

import providers as provider_module
from config_validator import get_item_source_path, validate_config, validate_config_run
from expression_evaluator import evaluate_expressions
from generate_units import (
    generate_cross_product_units,
    generate_direct_units,
    generate_permutation_units,
    generate_units,
    get_positions,
)
from octobatch_utils import load_manifest, save_manifest
from orchestrate import retry_validation_failures
from schema_validator import (
    _coerce_value,
    _resolve_schema_node,
    _unwrap_response,
    create_validator,
    process_stream,
    validate_line,
)


# =============================================================================
# Helpers
# =============================================================================


def write_yaml(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(data, sort_keys=False))


def write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2))


def make_llm_pipeline(
    tmp_path: Path,
    *,
    include_prompt_link: bool = True,
    include_schema_link: bool = True,
    include_validation_link: bool = True,
    include_template_file: bool = True,
    include_schema_file: bool = True,
) -> Path:
    """Create a minimal LLM pipeline on disk for validate_config_run()."""
    config = {
        "pipeline": {"steps": [{"name": "generate"}]},
        "processing": {
            "strategy": "direct",
            "chunk_size": 10,
            "items": {"source": "items.yaml", "key": "items", "name_field": "id"},
        },
        "prompts": {"template_dir": "templates", "templates": {}},
        "schemas": {"schema_dir": "schemas", "files": {}},
        "api": {"retry": {"max_attempts": 3}},
        "validation": {},
    }

    if include_prompt_link:
        config["prompts"]["templates"]["generate"] = "generate.jinja2"
    if include_schema_link:
        config["schemas"]["files"]["generate"] = "generate.json"
    if include_validation_link:
        config["validation"]["generate"] = {
            "required": ["text"],
            "types": {"text": "string"},
        }

    config_path = tmp_path / "Pipeline" / "config.yaml"
    write_yaml(config_path, config)
    write_yaml(config_path.parent / "items.yaml", {"items": [{"id": "item_1", "topic": "alpha"}]})

    if include_template_file:
        template_path = config_path.parent / "templates" / "generate.jinja2"
        template_path.parent.mkdir(parents=True, exist_ok=True)
        template_path.write_text('{"text": "{{ topic }}"}')

    if include_schema_file:
        write_json(
            config_path.parent / "schemas" / "generate.json",
            {
                "$schema": "https://json-schema.org/draft/2020-12/schema",
                "type": "object",
                "required": ["text"],
                "properties": {"text": {"type": "string"}},
            },
        )

    return config_path


@pytest.fixture
def drift_schema():
    return {
        "type": "object",
        "required": ["count", "tags", "enabled", "status"],
        "properties": {
            "count": {"type": "integer"},
            "tags": {"type": "array", "items": {"type": "string"}},
            "enabled": {"type": "boolean"},
            "status": {"type": "string", "enum": ["warm", "cold"]},
        },
    }


# =============================================================================
# Spec Requirements
# =============================================================================


class TestSpecRequirements:
    def test_spec_llm_steps_require_four_links(self, tmp_path):
        """[Req: formal — specs/VALIDATION.md § The 4-Point Link Rule] incomplete LLM steps are rejected before runtime."""
        config_path = make_llm_pipeline(tmp_path, include_validation_link=False)

        result = validate_config_run(config_path)

        assert result["valid"] is False
        assert any("4-Point Link Rule violation" in error for error in result["errors"])
        assert any("missing validation rules" in error for error in result["errors"])

    def test_spec_expression_steps_are_exempt_from_four_links(self):
        """[Req: formal — specs/EXPRESSION.md § What Expression Steps Are] expression-only pipelines do not need prompts, schemas, or api sections."""
        config = {
            "pipeline": {"steps": [{"name": "random_walk", "scope": "expression", "expressions": {"x": "1 + 2"}}]},
            "processing": {
                "strategy": "direct",
                "chunk_size": 5,
                "items": {"source": "items.yaml", "key": "items"},
            },
        }

        assert validate_config(config) == []

    def test_spec_cli_provider_override_beats_step_override(self, monkeypatch):
        """[Req: formal — specs/PROVIDERS.md § Provider/Model Resolution] CLI override metadata outranks step overrides."""
        config = {
            "api": {"provider": "anthropic", "model": "claude-sonnet-4-20250514"},
            "pipeline": {"steps": [{"name": "score", "provider": "openai", "model": "gpt-4o-mini"}]},
        }
        manifest = {"metadata": {"cli_provider_override": True, "cli_model_override": True}}

        monkeypatch.setattr(provider_module, "get_provider", lambda cfg: cfg["api"].copy())

        resolved = provider_module.get_step_provider(config, "score", manifest)

        assert resolved == {"provider": "anthropic", "model": "claude-sonnet-4-20250514"}

    def test_spec_step_override_applies_when_no_cli_override(self, monkeypatch):
        """[Req: formal — specs/PROVIDERS.md § Per-Step Overrides] step-level provider settings apply when no CLI override was used."""
        config = {
            "api": {"provider": "gemini", "model": "gemini-2.0-flash-001"},
            "pipeline": {"steps": [{"name": "score", "provider": "openai", "model": "gpt-4o-mini"}]},
        }

        monkeypatch.setattr(provider_module, "get_provider", lambda cfg: cfg["api"].copy())

        resolved = provider_module.get_step_provider(config, "score", manifest=None)

        assert resolved == {"provider": "openai", "model": "gpt-4o-mini"}

    @pytest.mark.parametrize(
        ("label", "config", "items_data", "expected_count", "expected_first_id"),
        [
            (
                "permutation",
                {
                    "processing": {
                        "strategy": "permutation",
                        "positions": [{"name": "past"}, {"name": "present"}],
                        "items": {"key": "cards", "name_field": "name"},
                    }
                },
                {"cards": [{"name": "Fool"}, {"name": "Magician"}, {"name": "Priestess"}]},
                6,
                "Fool-Magician",
            ),
            (
                "cross_product",
                {
                    "processing": {
                        "strategy": "cross_product",
                        "positions": [{"name": "npc", "source_key": "npcs"}, {"name": "mood", "source_key": "moods"}],
                        "items": {"name_field": "id"},
                    }
                },
                {"npcs": [{"id": "smith"}, {"id": "elder"}], "moods": [{"id": "friendly"}, {"id": "aggressive"}]},
                4,
                "smith-friendly",
            ),
            (
                "direct",
                {
                    "processing": {
                        "strategy": "direct",
                        "items": {"key": "hands", "name_field": "id"},
                    }
                },
                {"hands": [{"id": "hand_1", "value": 17}, {"id": "hand_2", "value": 19}]},
                2,
                "hand_1",
            ),
        ],
    )
    def test_spec_generation_strategies_produce_expected_units(self, label, config, items_data, expected_count, expected_first_id):
        """[Req: formal — specs/ORCHESTRATOR.md § Initialization Process] configured generation strategies must produce stable unit counts and IDs."""
        units = generate_units(config, items_data)

        assert len(units) == expected_count, label
        assert units[0]["unit_id"] == expected_first_id

    def test_spec_expression_evaluation_order(self):
        """[Req: formal — specs/EXPRESSION.md § Evaluation Order] later expressions can reference earlier results."""
        results = evaluate_expressions(
            {"total": "base + 2", "category": "'high' if total >= 5 else 'low'"},
            {"base": 3},
            11,
        )

        assert results == {"total": 5, "category": "high"}

    def test_spec_schema_validator_coerces_common_llm_types(self, drift_schema):
        """[Req: formal — specs/VALIDATION.md § Automatic Type Coercion] recoverable LLM drift is rescued before strict failure."""
        validator = create_validator(drift_schema)
        line = json.dumps(
            {
                "count": "5",
                "tags": "solo",
                "enabled": "FALSE",
                "status": "Shadow warm | backup",
            }
        )

        data, errors = validate_line(line, validator, drift_schema, 1)

        assert errors is None
        assert data["count"] == 5
        assert data["tags"] == ["solo"]
        assert data["enabled"] is False
        assert data["status"] == "warm"

    def test_spec_manifest_is_source_of_truth_and_writes_summary(self, tmp_path):
        """[Req: formal — specs/ORCHESTRATOR.md § The Manifest] saving a manifest writes canonical state plus a lightweight summary cache."""
        run_dir = tmp_path / "run"
        run_dir.mkdir()
        manifest = {
            "status": "running",
            "pipeline": ["generate"],
            "chunks": {"chunk_000": {"state": "generate_PENDING", "items": 2}},
            "metadata": {"mode": "realtime", "pipeline_name": "Demo"},
        }

        save_manifest(run_dir, manifest)

        saved = load_manifest(run_dir)
        summary = json.loads((run_dir / ".manifest_summary.json").read_text())

        assert saved["status"] == "running"
        assert saved["chunks"]["chunk_000"]["state"] == "generate_PENDING"
        assert summary["status"] in {"active", "running"}
        assert summary["pipeline_name"] == "Demo"


# =============================================================================
# Fitness Scenarios
# =============================================================================


class TestFitnessScenarios:
    def test_scenario_1_manifest_integrity_after_crash(self, tmp_path):
        """[Req: inferred — from octobatch_utils.save_manifest() behavior] corrupted summary cache does not replace canonical manifest state."""
        run_dir = tmp_path / "run"
        run_dir.mkdir()
        manifest = {
            "status": "paused",
            "pipeline": ["generate"],
            "chunks": {"chunk_000": {"state": "FAILED", "items": 1}},
            "metadata": {"mode": "batch", "pipeline_name": "Integrity"},
        }

        save_manifest(run_dir, manifest)
        (run_dir / ".manifest_summary.json").write_text('{"status": "failed", "pipeline_name": "WRONG"}')

        canonical = load_manifest(run_dir)

        assert canonical["status"] == "paused"
        assert canonical["metadata"]["pipeline_name"] == "Integrity"
        assert canonical["chunks"]["chunk_000"]["state"] == "FAILED"

    def test_scenario_2_broken_pipeline_stopped_before_spend(self, tmp_path):
        """[Req: formal — specs/VALIDATION.md § The 4-Point Link Rule] missing link entries are caught before any provider execution could begin."""
        config_path = make_llm_pipeline(tmp_path, include_schema_link=False)

        result = validate_config_run(config_path)

        assert result["valid"] is False
        assert any("4-Point Link Rule violation" in error for error in result["errors"])
        assert any("missing schema" in error for error in result["errors"])

    def test_scenario_3_provider_override_drift_prevented(self, monkeypatch):
        """[Req: formal — specs/PROVIDERS.md § CLI Override Tracking] forced CLI provider/model settings must win over step config."""
        config = {
            "api": {"provider": "openai", "model": "gpt-4o-mini"},
            "pipeline": {"steps": [{"name": "analyze", "provider": "anthropic", "model": "claude-sonnet-4-20250514"}]},
        }
        manifest = {"metadata": {"cli_provider_override": True, "cli_model_override": True}}

        monkeypatch.setattr(provider_module, "get_provider", lambda cfg: cfg["api"].copy())

        resolved = provider_module.get_step_provider(config, "analyze", manifest)

        assert resolved["provider"] == "openai"
        assert resolved["model"] == "gpt-4o-mini"

    def test_scenario_4_statistical_seed_bias_defense(self):
        """[Req: formal — specs/EXPRESSION.md § Seeded Randomness] repeat-mode units are deterministic, interleaved, and uniquely seeded."""
        config = {
            "processing": {
                "strategy": "direct",
                "repeat": 3,
                "items": {"key": "items", "name_field": "id"},
            }
        }
        items_data = {"items": [{"id": "a"}, {"id": "b"}, {"id": "c"}]}

        first = generate_units(config, items_data)
        second = generate_units(config, items_data)

        assert [unit["unit_id"] for unit in first[:6]] == [
            "a__rep0000",
            "b__rep0000",
            "c__rep0000",
            "a__rep0001",
            "b__rep0001",
            "c__rep0001",
        ]
        assert first == second
        assert first[0]["_repetition_seed"] != first[3]["_repetition_seed"]
        assert len({unit["_repetition_seed"] for unit in first}) == len(first)

    def test_scenario_5_markdown_wrapped_json_rescued(self):
        """[Req: formal — specs/VALIDATION.md § Markdown Block Extraction] wrapped JSON with trailing commas is recovered into structured fields."""
        schema = {
            "type": "object",
            "required": ["scenario_name", "final_position", "steps_taken", "path", "outcome"],
            "properties": {
                "scenario_name": {"type": "string"},
                "final_position": {"type": "integer"},
                "steps_taken": {"type": "integer"},
                "path": {"type": "array", "items": {"type": "integer"}},
                "outcome": {"type": "string", "enum": ["fell_in_water", "reached_ship"]},
            },
        }
        validator = create_validator(schema)
        line = json.dumps(
            {
                "unit_id": "walk_1",
                "response": "```json\n{\"scenario_name\": \"Start at Position 5\", \"final_position\": 0, \"steps_taken\": 5, \"path\": [5,4,3,2,1,0], \"outcome\": \"fell_in_water\",}\n```",
            }
        )

        data, errors = validate_line(line, validator, schema, 1)

        assert errors is None
        assert data["scenario_name"] == "Start at Position 5"
        assert data["final_position"] == 0
        assert data["path"] == [5, 4, 3, 2, 1, 0]
        assert data["outcome"] == "fell_in_water"
        assert "response" not in data

    def test_scenario_6_retry_isolation_preserves_good_work(self, tmp_path):
        """[Req: inferred — from orchestrate.retry_validation_failures() behavior] only retryable validation failures are moved into a retry chunk."""
        run_dir = tmp_path / "run"
        chunk_dir = run_dir / "chunks" / "chunk_000"
        chunk_dir.mkdir(parents=True)

        manifest = {
            "config": "config/config.yaml",
            "status": "failed",
            "pipeline": ["generate"],
            "chunks": {
                "chunk_000": {
                    "state": "FAILED",
                    "items": 2,
                    "retries": 0,
                    "failed": 2,
                }
            },
            "metadata": {"mode": "realtime"},
        }
        save_manifest(run_dir, manifest)

        units_path = chunk_dir / "units.jsonl"
        units_path.write_text(
            "\n".join(
                [
                    json.dumps({"unit_id": "u1", "payload": "retry-me"}),
                    json.dumps({"unit_id": "u2", "payload": "keep-hard-failure"}),
                ]
            )
            + "\n"
        )

        failures = [
            {"unit_id": "u1", "failure_stage": "validation", "retry_count": 0, "errors": [{"message": "bad score"}]},
            {"unit_id": "u2", "failure_stage": "pipeline_internal", "retry_count": 0, "errors": [{"message": "missing downstream file"}]},
        ]
        (chunk_dir / "generate_failures.jsonl").write_text("\n".join(json.dumps(row) for row in failures) + "\n")

        archived = retry_validation_failures(run_dir, manifest, run_dir / "RUN_LOG.txt", max_retries=3)

        retry_dirs = sorted((run_dir / "chunks").glob("retry_*"))
        assert len(retry_dirs) == 1
        retry_name = retry_dirs[0].name
        retry_units = [json.loads(line) for line in (retry_dirs[0] / "units.jsonl").read_text().splitlines() if line.strip()]
        remaining_failures = [json.loads(line) for line in (chunk_dir / "generate_failures.jsonl").read_text().splitlines() if line.strip()]

        assert archived == 1
        assert retry_units == [{"unit_id": "u1", "payload": "retry-me", "retry_count": 1}]
        assert remaining_failures == [failures[1]]
        assert (chunk_dir / "generate_failures.jsonl.bak").exists()
        assert manifest["chunks"][retry_name]["state"] == "generate_PENDING"
        assert manifest["chunks"]["chunk_000"]["failed"] == 1
        assert manifest["status"] == "running"

    def test_scenario_7_expression_order_and_determinism_are_contract(self):
        """[Req: formal — specs/EXPRESSION.md § Evaluation Order] seeded expression evaluation is reproducible and compositional."""
        expressions = {
            "draw": "random.randint(1, 1000000)",
            "augmented": "draw + offset",
        }
        context = {"offset": 5}

        first = evaluate_expressions(expressions, context, 7)
        second = evaluate_expressions(expressions, context, 7)
        third = evaluate_expressions(expressions, context, 8)

        assert first == second
        assert first["augmented"] == first["draw"] + 5
        assert first["draw"] != third["draw"]

    def test_scenario_8_non_finite_numbers_never_sneak_through(self):
        """[Req: formal — specs/VALIDATION.md § What Schema Validation Catches] NaN remains a structured validation failure, not accepted output."""
        schema = {
            "type": "object",
            "required": ["score"],
            "properties": {"score": {"type": "number"}},
        }
        validator = create_validator(schema)
        line = json.dumps({"unit_id": "u1", "score": float("nan")})

        data, errors = validate_line(line, validator, schema, 1)

        assert data["unit_id"] == "u1"
        assert errors is not None
        assert any(error["rule"] == "schema_non_finite_number" for error in errors)


# =============================================================================
# Boundaries and Edge Cases
# =============================================================================


class TestBoundariesAndEdgeCases:
    def test_boundary_invalid_provider_rejected(self):
        """[Req: inferred — from config_validator.validate_config() guard] invalid provider values are rejected at config-lint time."""
        config = {
            "pipeline": {"steps": [{"name": "generate", "provider": "azure"}]},
            "processing": {
                "strategy": "direct",
                "chunk_size": 5,
                "items": {"source": "items.yaml", "key": "items"},
            },
        }

        errors = validate_config(config)

        assert any("invalid provider" in error.lower() for error in errors)

    def test_boundary_get_positions_rejects_invalid_format(self):
        """[Req: inferred — from generate_units.get_positions() guard] malformed position entries fail loudly."""
        config = {"processing": {"positions": [123]}}

        with pytest.raises(ValueError, match="Invalid position format"):
            get_positions(config)

    def test_boundary_permutation_requires_enough_items(self):
        """[Req: inferred — from generate_permutation_units() guard] permutation strategy rejects underfilled position sets."""
        config = {
            "processing": {
                "strategy": "permutation",
                "positions": [{"name": "left"}, {"name": "right"}, {"name": "center"}],
                "items": {"key": "cards", "name_field": "name"},
            }
        }
        items_data = {"cards": [{"name": "A"}, {"name": "B"}]}

        with pytest.raises(ValueError, match="Not enough items"):
            generate_permutation_units(config, items_data)

    def test_boundary_cross_product_requires_existing_source_keys(self):
        """[Req: inferred — from generate_cross_product_units() guard] missing group keys stop cross-product generation immediately."""
        config = {
            "processing": {
                "strategy": "cross_product",
                "positions": [{"name": "npc", "source_key": "npcs"}, {"name": "topic", "source_key": "topics"}],
                "items": {"name_field": "id"},
            }
        }
        items_data = {"npcs": [{"id": "smith"}]}

        with pytest.raises(ValueError, match="Missing 'topics'"):
            generate_cross_product_units(config, items_data)

    def test_boundary_direct_strategy_rejects_duplicate_unit_ids(self):
        """[Req: inferred — from generate_direct_units() guard] duplicate direct-unit IDs are rejected before chunking."""
        config = {"processing": {"strategy": "direct", "items": {"key": "hands", "name_field": "id"}}}
        items_data = {"hands": [{"id": "dup"}, {"id": "dup"}]}

        with pytest.raises(ValueError, match="Duplicate unit_id detected"):
            generate_direct_units(config, items_data)

    def test_boundary_schema_validator_preserves_json_parse_failures_with_raw_response(self, capsys):
        """[Req: inferred — from schema_validator.process_stream() guard] raw malformed lines are preserved for debugging."""
        schema = {"type": "object", "properties": {"value": {"type": "number"}}}
        validator = create_validator(schema)

        valid_count, error_count, _ = process_stream(['{"value": ]\n'], validator, schema)
        failure_record = json.loads(capsys.readouterr().err.strip().splitlines()[-1])

        assert valid_count == 0
        assert error_count == 1
        assert failure_record["failure_stage"] == "schema_validation"
        assert failure_record["raw_response"] == '{"value": ]'

    def test_boundary_nan_is_not_coerced_into_a_number(self):
        """[Req: inferred — from schema_validator._coerce_value() guard] non-finite floats remain unchanged for structured failure reporting."""
        coerced, changed = _coerce_value(float("nan"), "number", "$.score")

        assert isinstance(coerced, float)
        assert math.isnan(coerced)
        assert changed is False

    def test_boundary_get_item_source_path_handles_self_referential_configs(self, tmp_path):
        """[Req: inferred — from config_validator.get_item_source_path() behavior] self-referential item configs do not fabricate a source path."""
        config = {"processing": {"items": {"key": "items"}}}
        config_path = tmp_path / "config.yaml"

        assert get_item_source_path(config, config_path) is None

    def test_boundary_ref_resolution_stops_on_circular_defs(self):
        """[Req: inferred — from schema_validator._resolve_schema_node() guard] circular $ref chains do not recurse forever."""
        defs = {"loop": {"$ref": "#/$defs/loop"}}

        resolved = _resolve_schema_node({"$ref": "#/$defs/loop"}, defs)

        assert resolved == {"$ref": "#/$defs/loop"}

    def test_boundary_unwrap_response_leaves_valid_top_level_payloads_alone(self):
        """[Req: inferred — from schema_validator._unwrap_response() guard] already-valid payloads are not mutated just because a response key exists."""
        schema = {
            "type": "object",
            "required": ["status"],
            "properties": {"status": {"type": "string"}},
        }
        data = {"status": "warm", "response": "```json\n{\"status\": \"cold\"}\n```"}

        unwrapped = _unwrap_response(data.copy(), schema)

        assert unwrapped["status"] == "warm"
        assert unwrapped["response"].startswith("```")
