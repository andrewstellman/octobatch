"""
Tests for expression step evaluation (scripts/expression_evaluator.py).

Covers:
- Basic expression evaluation (arithmetic, string operations)
- Seeded randomness via _repetition_seed produces deterministic output
- Expression steps WITH validation rules — failing condition rejects the unit
- Expression steps WITH validation rules — passing condition accepts the unit
- Expression steps WITHOUT validation rules — output written directly (legacy behavior)
- Card dealing expressions (the Blackjack pattern)
"""

import json
import sys
import tempfile
from pathlib import Path

import pytest

# Add scripts directory to path
sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

from expression_evaluator import (
    SeededRandom,
    evaluate_expressions,
    evaluate_condition,
    validate_expression,
    get_expressions,
    create_seeded_interpreter,
    create_validation_interpreter,
)


# =============================================================================
# Basic Expression Evaluation
# =============================================================================

class TestBasicExpressions:
    """Tests for evaluate_expressions()."""

    def test_arithmetic_expression(self):
        """Arithmetic expressions evaluate correctly."""
        expressions = {"total": "2 + 3 * 4"}
        results = evaluate_expressions(expressions, {}, seed_or_rng=42)
        assert results["total"] == 14

    def test_string_operations(self):
        """String operations work in expressions."""
        expressions = {"greeting": "'hello ' + name"}
        results = evaluate_expressions(expressions, {"name": "world"}, seed_or_rng=42)
        assert results["greeting"] == "hello world"

    def test_list_comprehension(self):
        """List comprehensions work in expressions."""
        expressions = {"doubled": "[x * 2 for x in items]"}
        results = evaluate_expressions(expressions, {"items": [1, 2, 3]}, seed_or_rng=42)
        assert results["doubled"] == [2, 4, 6]

    def test_context_variables_accessible(self):
        """Context variables are accessible in expressions."""
        expressions = {"result": "x + y"}
        results = evaluate_expressions(expressions, {"x": 10, "y": 20}, seed_or_rng=42)
        assert results["result"] == 30

    def test_empty_expressions_returns_empty(self):
        """Empty expression dict returns empty result."""
        results = evaluate_expressions({}, {}, seed_or_rng=42)
        assert results == {}

    def test_expression_chaining(self):
        """Later expressions can reference results of earlier ones."""
        expressions = {
            "a": "10",
            "b": "a + 5",
            "c": "a + b",
        }
        results = evaluate_expressions(expressions, {}, seed_or_rng=42)
        assert results["a"] == 10
        assert results["b"] == 15
        assert results["c"] == 25

    def test_invalid_expression_raises(self):
        """Invalid expression raises ValueError."""
        expressions = {"bad": "undefined_var + 1"}
        with pytest.raises(ValueError, match="failed"):
            evaluate_expressions(expressions, {}, seed_or_rng=42)


# =============================================================================
# Seeded Randomness
# =============================================================================

class TestSeededRandomness:
    """Tests for SeededRandom and deterministic output."""

    def test_same_seed_produces_same_results(self):
        """Same seed produces identical results across runs."""
        expressions = {"roll": "random.randint(1, 100)"}
        result1 = evaluate_expressions(expressions, {}, seed_or_rng=12345)
        result2 = evaluate_expressions(expressions, {}, seed_or_rng=12345)
        assert result1["roll"] == result2["roll"]

    def test_different_seeds_produce_different_results(self):
        """Different seeds produce different results (with very high probability)."""
        expressions = {"roll": "random.randint(1, 1000000)"}
        result1 = evaluate_expressions(expressions, {}, seed_or_rng=1)
        result2 = evaluate_expressions(expressions, {}, seed_or_rng=2)
        assert result1["roll"] != result2["roll"]

    def test_seeded_random_choice(self):
        """SeededRandom.choice is deterministic."""
        rng1 = SeededRandom(42)
        rng2 = SeededRandom(42)
        seq = ["A", "B", "C", "D", "E"]
        assert rng1.choice(seq) == rng2.choice(seq)

    def test_seeded_random_sample(self):
        """SeededRandom.sample is deterministic."""
        rng1 = SeededRandom(99)
        rng2 = SeededRandom(99)
        population = list(range(52))
        assert rng1.sample(population, 5) == rng2.sample(population, 5)

    def test_seeded_random_uniform(self):
        """SeededRandom.uniform is deterministic."""
        rng1 = SeededRandom(7)
        rng2 = SeededRandom(7)
        assert rng1.uniform(0.0, 1.0) == rng2.uniform(0.0, 1.0)

    def test_seeded_random_gauss(self):
        """SeededRandom.gauss is deterministic."""
        rng1 = SeededRandom(7)
        rng2 = SeededRandom(7)
        assert rng1.gauss(0, 1) == rng2.gauss(0, 1)

    def test_persistent_rng_advances_state(self):
        """Passing an existing SeededRandom advances its state."""
        rng = SeededRandom(42)
        expressions = {"r1": "random.randint(1, 100)"}
        r1 = evaluate_expressions(expressions, {}, seed_or_rng=rng)
        expressions2 = {"r2": "random.randint(1, 100)"}
        r2 = evaluate_expressions(expressions2, {}, seed_or_rng=rng)
        # r1 and r2 use advancing state, not the same draw
        # They COULD collide by chance, but the point is the RNG advances
        # Verify determinism by replaying with a fresh RNG
        rng_replay = SeededRandom(42)
        r1_replay = evaluate_expressions({"r1": "random.randint(1, 100)"}, {}, seed_or_rng=rng_replay)
        r2_replay = evaluate_expressions({"r2": "random.randint(1, 100)"}, {}, seed_or_rng=rng_replay)
        assert r1["r1"] == r1_replay["r1"]
        assert r2["r2"] == r2_replay["r2"]


# =============================================================================
# Card Dealing Expressions (Blackjack Pattern)
# =============================================================================

class TestCardDealingExpressions:
    """Tests for card dealing expressions matching the Blackjack pipeline."""

    def test_shoe_creation(self):
        """6-deck shoe expression creates correct number of cards."""
        expressions = {
            "_shoe_cards": "['A','2','3','4','5','6','7','8','9','10','J','Q','K'] * 24",
        }
        results = evaluate_expressions(expressions, {}, seed_or_rng=42)
        assert len(results["_shoe_cards"]) == 13 * 24  # 312 cards in 6-deck shoe

    def test_deal_from_shoe(self):
        """Dealing cards from shoe produces correct count and valid cards."""
        expressions = {
            "_shoe_cards": "['A','2','3','4','5','6','7','8','9','10','J','Q','K'] * 24",
            "_dealt": "random.sample(_shoe_cards, 14)",
            "player_card_1": "_dealt[0]",
            "player_card_2": "_dealt[1]",
            "dealer_up_card": "_dealt[2]",
        }
        results = evaluate_expressions(expressions, {}, seed_or_rng=42)
        assert len(results["_dealt"]) == 14
        valid_cards = {'A', '2', '3', '4', '5', '6', '7', '8', '9', '10', 'J', 'Q', 'K'}
        assert results["player_card_1"] in valid_cards
        assert results["player_card_2"] in valid_cards
        assert results["dealer_up_card"] in valid_cards

    def test_deal_deterministic_with_seed(self):
        """Same seed deals the same cards."""
        expressions = {
            "_shoe_cards": "['A','2','3','4','5','6','7','8','9','10','J','Q','K'] * 24",
            "_dealt": "random.sample(_shoe_cards, 14)",
            "player_card_1": "_dealt[0]",
            "player_card_2": "_dealt[1]",
        }
        r1 = evaluate_expressions(expressions, {}, seed_or_rng=777)
        r2 = evaluate_expressions(expressions, {}, seed_or_rng=777)
        assert r1["player_card_1"] == r2["player_card_1"]
        assert r1["player_card_2"] == r2["player_card_2"]
        assert r1["_dealt"] == r2["_dealt"]

    def test_extra_card_pools(self):
        """Player and dealer extra card pools are separate slices."""
        expressions = {
            "_shoe_cards": "['A','2','3','4','5','6','7','8','9','10','J','Q','K'] * 24",
            "_dealt": "random.sample(_shoe_cards, 14)",
            "player_extra_cards": "_dealt[4:9]",
            "dealer_extra_cards": "_dealt[9:14]",
        }
        results = evaluate_expressions(expressions, {}, seed_or_rng=42)
        assert len(results["player_extra_cards"]) == 5
        assert len(results["dealer_extra_cards"]) == 5


# =============================================================================
# Evaluate Condition
# =============================================================================

class TestEvaluateCondition:
    """Tests for evaluate_condition()."""

    def test_true_condition(self):
        """True condition returns True."""
        assert evaluate_condition("x > 5", {"x": 10}) is True

    def test_false_condition(self):
        """False condition returns False."""
        assert evaluate_condition("x > 5", {"x": 3}) is False

    def test_invalid_condition_raises(self):
        """Invalid condition raises ValueError."""
        with pytest.raises(ValueError):
            evaluate_condition("undefined_var > 5", {})


# =============================================================================
# Expression Steps WITH Validation Rules
# =============================================================================

class TestExpressionStepValidation:
    """Tests for expression steps that have associated validation rules."""

    def test_expression_step_with_passing_validation(self):
        """Expression step whose output passes validation is accepted."""
        from validator import validate_line
        from octobatch_utils import create_interpreter

        # Simulate expression step output that passes validation
        expr_output = {
            "verification_passed": True,
            "verification_details": "All checks passed",
        }
        validation_config = {
            "required": ["verification_passed"],
            "types": {"verification_passed": "boolean"},
            "rules": [{
                "name": "hand_verification",
                "expr": "verification_passed == True",
                "error": "Hand verification failed: {verification_details}",
                "level": "error",
            }],
        }
        aeval = create_interpreter()
        is_valid, errors, warnings = validate_line(expr_output, validation_config, aeval, 1)
        assert is_valid
        assert errors == []

    def test_expression_step_with_failing_validation(self):
        """Expression step whose output fails validation is rejected."""
        from validator import validate_line
        from octobatch_utils import create_interpreter

        expr_output = {
            "verification_passed": False,
            "verification_details": "Totals mismatch: calc=19, reported=21",
        }
        validation_config = {
            "required": ["verification_passed"],
            "types": {"verification_passed": "boolean"},
            "rules": [{
                "name": "hand_verification",
                "expr": "verification_passed == True",
                "error": "Hand verification failed: {verification_details}",
                "level": "error",
            }],
        }
        aeval = create_interpreter()
        is_valid, errors, warnings = validate_line(expr_output, validation_config, aeval, 1)
        assert not is_valid
        assert len(errors) == 1
        assert "Hand verification failed" in errors[0]["message"]

    def test_blackjack_verify_pattern_with_meaningful_error(self):
        """Blackjack-style verify step should fail with detailed mismatch context."""
        from validator import validate_line
        from octobatch_utils import create_interpreter

        # Simulate post-play context where reported total is incorrect.
        context = {
            "player_cards_used": ["10", "8"],
            "dealer_cards_used": ["9", "7"],
            "player_final_total": 19,  # wrong on purpose (actual should be 18)
            "dealer_final_total": 16,
            "player_busted": False,
            "dealer_busted": False,
        }
        expressions = {
            "_player_calc": "sum([10 if c in ['J','Q','K'] else 11 if c == 'A' else int(c) for c in player_cards_used])",
            "_dealer_calc": "sum([10 if c in ['J','Q','K'] else 11 if c == 'A' else int(c) for c in dealer_cards_used])",
            "_totals_ok": "player_final_total == _player_calc and dealer_final_total == _dealer_calc",
            "_bust_ok": "player_busted == (_player_calc > 21) and dealer_busted == (_dealer_calc > 21)",
            "verification_passed": "_totals_ok and _bust_ok",
            "verification_details": "f'Player: calc={_player_calc} reported={player_final_total}, Dealer: calc={_dealer_calc} reported={dealer_final_total}'",
        }
        expr_output = {**context, **evaluate_expressions(expressions, context, seed_or_rng=42)}

        validation_config = {
            "required": ["verification_passed"],
            "types": {"verification_passed": "boolean"},
            "rules": [{
                "name": "hand_verification",
                "expr": "verification_passed == True",
                "error": "Hand verification failed: {verification_details}",
                "level": "error",
            }],
        }
        aeval = create_interpreter()
        is_valid, errors, _warnings = validate_line(expr_output, validation_config, aeval, 1)
        assert not is_valid
        assert len(errors) == 1
        assert "Hand verification failed: Player: calc=18 reported=19" in errors[0]["message"]


# =============================================================================
# Expression Steps WITHOUT Validation Rules (legacy behavior)
# =============================================================================

class TestExpressionStepLegacy:
    """Tests for expression steps without validation — output written directly."""

    def test_expression_output_written_directly(self):
        """Without validation rules, expression output goes directly to validated file."""
        # This tests the pattern: expression steps without validation config
        # produce output that flows directly to the next step
        expressions = {
            "player_card_1": "'A'",
            "player_card_2": "'K'",
        }
        context = {"unit_id": "test_unit", "strategy_name": "The Pro"}
        results = evaluate_expressions(expressions, context, seed_or_rng=42)

        # Results should be available without any validation
        assert results["player_card_1"] == "A"
        assert results["player_card_2"] == "K"

    def test_expression_results_merge_with_context(self):
        """Expression results overlay on existing context (simulating unit merge)."""
        context = {"unit_id": "test_unit", "original_field": "kept"}
        expressions = {"new_field": "'added'"}
        results = evaluate_expressions(expressions, context, seed_or_rng=42)

        # Merge (as orchestrator does): context + results
        merged = {**context, **results}
        assert merged["original_field"] == "kept"
        assert merged["new_field"] == "added"
        assert merged["unit_id"] == "test_unit"


# =============================================================================
# Validate Expression (syntax checking)
# =============================================================================

class TestValidateExpression:
    """Tests for validate_expression() syntax checking."""

    def test_valid_expression(self):
        """Valid expression passes syntax check."""
        is_valid, error = validate_expression("2 + 3")
        assert is_valid

    def test_invalid_expression(self):
        """Invalid expression fails syntax check."""
        is_valid, error = validate_expression("def foo():")
        assert not is_valid

    def test_expression_with_random(self):
        """Expression using random module passes (MockRandom available)."""
        is_valid, error = validate_expression("random.randint(1, 6)")
        assert is_valid


# =============================================================================
# Get Expressions from Config
# =============================================================================

class TestGetExpressions:
    """Tests for get_expressions() config helper."""

    def test_extracts_expressions(self):
        """Extracts expressions from config processing section."""
        config = {
            "processing": {
                "expressions": {
                    "dice": "random.randint(1, 6)",
                    "coin": "random.choice(['heads', 'tails'])",
                }
            }
        }
        expressions = get_expressions(config)
        assert "dice" in expressions
        assert "coin" in expressions

    def test_missing_expressions(self):
        """Returns empty dict when no expressions configured."""
        assert get_expressions({}) == {}
        assert get_expressions({"processing": {}}) == {}


# =============================================================================
# v1.0.3 Bug 4: Init variable scoping in loops
# =============================================================================

class TestInitVariableScoping:
    """Tests for v1.0.3 Bug 4: init expressions referencing upstream context
    must be available in the expressions block."""

    def test_init_referencing_upstream_context(self):
        """Init expression referencing upstream context variable resolves correctly."""
        init = {"_hand": "[card1, card2]"}
        context = {"card1": "5", "card2": "3"}

        init_results = evaluate_expressions(init, context, seed_or_rng=42)
        assert init_results["_hand"] == ["5", "3"]

    def test_init_expressions_sequential(self):
        """Init expressions evaluated sequentially (later init can reference earlier init)."""
        init = {
            "_hand": "[card1, card2]",
            "_total": "sum([int(c) for c in _hand])",
        }
        context = {"card1": "5", "card2": "3"}

        init_results = evaluate_expressions(init, context, seed_or_rng=42)
        assert init_results["_hand"] == ["5", "3"]
        assert init_results["_total"] == 8

    def test_init_variables_available_in_expressions(self):
        """Init variables available in expressions block on first loop iteration."""
        init = {"_hand": "[card1, card2]"}
        context = {"card1": "5", "card2": "3"}

        # Evaluate init
        init_results = evaluate_expressions(init, context, seed_or_rng=42)
        context.update(init_results)

        # Evaluate expressions (simulates what run_expression_step does)
        expressions = {"_new_total": "sum([int(c) for c in _hand]) + 1"}
        expr_results = evaluate_expressions(expressions, context, seed_or_rng=42)
        assert expr_results["_new_total"] == 9

    def test_init_complex_types_from_upstream(self):
        """Init variables with complex types (lists, dicts built from upstream fields) work."""
        init = {
            "_cards": "[card1, card2, card3]",
            "_values": "[int(c) if c.isdigit() else 10 for c in _cards]",
        }
        context = {"card1": "5", "card2": "K", "card3": "3"}

        results = evaluate_expressions(init, context, seed_or_rng=42)
        assert results["_cards"] == ["5", "K", "3"]
        assert results["_values"] == [5, 10, 3]

    def test_drunken_sailor_init_unchanged(self):
        """Existing Drunken Sailor init behavior unchanged (regression test)."""
        # Drunken Sailor uses: init: { position: "start_position" }
        init = {"position": "start_position"}
        context = {"start_position": 5}

        results = evaluate_expressions(init, context, seed_or_rng=42)
        assert results["position"] == 5

    def test_system_fields_not_leaked_to_expressions(self):
        """System fields (_metadata, _raw_text) are not available in expressions."""
        expressions = {"result": "1 + 1"}
        context = {
            "card1": "5",
            "_metadata": {"some": "data"},
            "_raw_text": "raw response",
            "_repetition_seed": 12345,
        }

        # Should not fail even though system fields are in context
        results = evaluate_expressions(expressions, context, seed_or_rng=42)
        assert results["result"] == 2
