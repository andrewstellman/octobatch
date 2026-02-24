#!/usr/bin/env python3
"""
expression_evaluator.py - Runtime expression evaluation with seeded randomness.

Evaluates asteval expressions with a seeded random module for reproducible
Monte Carlo simulations. Each unit's _repetition_seed controls the random state.

Config structure:
    processing:
      expressions:
        next_card: "random.choice(['2','3','4','5','6','7','8','9','10','J','Q','K','A'])"
        dice_roll: "random.randint(1, 6)"
        deck: "random.sample(['2','3',...,'A'] * 4, k=5)"  # Deal 5 cards
"""

import random as random_module
from typing import Any

try:
    from asteval import Interpreter
    ASTEVAL_AVAILABLE = True
except ImportError:
    ASTEVAL_AVAILABLE = False


class SeededRandom:
    """
    A random module wrapper that uses a seeded RNG for reproducibility.
    Mimics the random module interface but uses a specific Random instance.
    """
    def __init__(self, seed: int):
        self._rng = random_module.Random(seed)

    def choice(self, seq):
        return self._rng.choice(seq)

    def randint(self, a, b):
        return self._rng.randint(a, b)

    def random(self):
        return self._rng.random()

    def uniform(self, a, b):
        return self._rng.uniform(a, b)

    def sample(self, population, k):
        return self._rng.sample(population, k)

    def shuffle(self, x):
        return self._rng.shuffle(x)

    def gauss(self, mu, sigma):
        return self._rng.gauss(mu, sigma)


class MockRandom:
    """
    A mock random module for validation purposes.
    Returns placeholder values but allows expression syntax checking.
    """
    def choice(self, seq):
        return seq[0] if seq else None

    def randint(self, a, b):
        return a

    def random(self):
        return 0.5

    def uniform(self, a, b):
        return a

    def sample(self, population, k):
        return list(population)[:k]

    def shuffle(self, x):
        pass

    def gauss(self, mu, sigma):
        return mu


def create_seeded_interpreter(seed_or_rng) -> 'Interpreter':
    """
    Create an asteval Interpreter with a seeded random module.

    Args:
        seed_or_rng: Integer seed for reproducible randomness, or an existing
                     SeededRandom instance to reuse (for persistent RNG state)

    Returns:
        Configured Interpreter with random module injected
    """
    if not ASTEVAL_AVAILABLE:
        raise ImportError("asteval is required for expressions feature. Install with: pip install asteval")

    interpreter = Interpreter()

    # Accept either a seed (int) or an existing SeededRandom instance
    if isinstance(seed_or_rng, SeededRandom):
        interpreter.symtable['random'] = seed_or_rng
    else:
        interpreter.symtable['random'] = SeededRandom(seed_or_rng)

    return interpreter


def create_validation_interpreter() -> 'Interpreter':
    """
    Create an asteval Interpreter for validation/syntax checking.
    Uses MockRandom so expressions with 'random' pass validation.

    Returns:
        Configured Interpreter with mock random module
    """
    if not ASTEVAL_AVAILABLE:
        raise ImportError("asteval is required for expressions feature. Install with: pip install asteval")

    interpreter = Interpreter()
    interpreter.symtable['random'] = MockRandom()

    return interpreter


def evaluate_expressions(
    expressions: dict[str, str],
    context: dict[str, Any],
    seed_or_rng
) -> dict[str, Any]:
    """
    Evaluate all expressions with the given seed/RNG and context.

    Args:
        expressions: Dict of {name: expression_string}
        context: Dict of variables available to expressions (e.g., unit data)
        seed_or_rng: Random seed (int) for reproducibility, or an existing
                     SeededRandom instance for persistent RNG state across calls

    Returns:
        Dict of {name: evaluated_result}

    Raises:
        ValueError: If an expression fails to evaluate
    """
    if not expressions:
        return {}

    interpreter = create_seeded_interpreter(seed_or_rng)

    # Add context variables to interpreter
    for key, value in context.items():
        if not key.startswith('_'):  # Skip private fields
            interpreter.symtable[key] = value

    results = {}
    for name, expr in expressions.items():
        try:
            result = interpreter(expr)
            if interpreter.error:
                errors = "; ".join(str(e.get_error()) for e in interpreter.error)
                interpreter.error = []  # Clear errors for next expression
                raise ValueError(f"Expression '{name}' failed: {errors}")
            results[name] = result
            # Add result to symtable so subsequent expressions can reference it
            interpreter.symtable[name] = result
        except ValueError:
            raise  # Re-raise ValueError as-is
        except Exception as e:
            raise ValueError(f"Expression '{name}' = '{expr}' failed: {e}")

    return results


def evaluate_condition(expr: str, context: dict, seed_or_rng=None) -> bool:
    """
    Evaluate a boolean expression against a context.

    Used for loop_until conditions in expression steps.

    Args:
        expr: Expression string that should evaluate to True/False
        context: Dict of variables available to the expression
        seed_or_rng: Optional seed (int) or SeededRandom instance for random operations

    Returns:
        Boolean result of the expression
    """
    if not ASTEVAL_AVAILABLE:
        raise ImportError("asteval is required for expressions feature")

    if seed_or_rng is not None:
        interpreter = create_seeded_interpreter(seed_or_rng)
    else:
        interpreter = Interpreter()

    # Add context variables
    for key, value in context.items():
        if not key.startswith('_'):  # Skip metadata
            interpreter.symtable[key] = value

    result = interpreter(expr)
    if interpreter.error:
        errors = "; ".join(str(e.get_error()) for e in interpreter.error)
        raise ValueError(f"Condition '{expr}' failed: {errors}")

    return bool(result)


def validate_expression(expr: str, context_keys: list[str] = None) -> tuple[bool, str]:
    """
    Validate an expression for syntax and safety.

    Args:
        expr: Expression string to validate
        context_keys: Optional list of variable names that will be available

    Returns:
        (is_valid, error_message) tuple
    """
    if not ASTEVAL_AVAILABLE:
        return True, "asteval not available, skipping validation"

    interpreter = create_validation_interpreter()

    # Add mock context variables
    if context_keys:
        for key in context_keys:
            interpreter.symtable[key] = "mock_value"

    try:
        interpreter(expr)
        if interpreter.error:
            errors = "; ".join(str(e.get_error()) for e in interpreter.error)
            interpreter.error = []
            return False, errors
        return True, ""
    except Exception as e:
        return False, str(e)


def get_expressions(config: dict) -> dict[str, str]:
    """Get expressions from config."""
    return config.get("processing", {}).get("expressions", {})
