#!/usr/bin/env python3
"""
realtime_provider.py - Synchronous API provider for real-time pipeline execution.

Provides an alternative to batch API processing that makes individual synchronous
calls to the LLM API via the provider abstraction. Useful for small runs where
immediate results are needed.

Cost warning: Real-time API calls cost approximately 2x the batch rate.
"""

import time
from typing import TYPE_CHECKING

# Import error types from provider base
from scripts.providers.base import ProviderError, RateLimitError

# Import shared utility
from scripts.octobatch_utils import parse_json_response

if TYPE_CHECKING:
    from scripts.providers.base import LLMProvider

# Backward compatibility aliases
RealtimeProviderError = ProviderError


class FatalProviderError(ProviderError):
    """Raised for auth/billing errors (400/401/403) that should abort the entire run."""
    pass


def run_realtime(
    prompts: list[dict],
    provider: "LLMProvider",
    delay_between_calls: float = 0.5,
    max_retries: int = 3,
    initial_backoff: float = 1.0,
    backoff_multiplier: float = 2.0,
    progress_callback: callable = None,
    trace_callback: callable = None
) -> list[dict]:
    """
    Run prompts synchronously using the provider abstraction and return results.

    Args:
        prompts: List of {"unit_id": ..., "prompt": ...}
        provider: LLMProvider instance (from get_provider())
        delay_between_calls: Seconds to wait between calls (default: 0.5)
        max_retries: Maximum retry attempts for transient errors (default: 3)
        initial_backoff: Initial backoff in seconds for retries (default: 1.0)
        backoff_multiplier: Exponential backoff multiplier (default: 2.0)
        progress_callback: Optional callback(unit_id, success, error_type, input_tokens, output_tokens, error_message) called after each unit
            - error_message is the full error string when success=False, None otherwise
        trace_callback: Optional callback(unit_id, duration_secs, status_str) for request-level telemetry

    Returns:
        List of {"unit_id": ..., "response": ..., "_metadata": {...}}
        On success, parsed JSON fields are merged into the result dict.
        On failure, includes "error" field with error message.
    """
    results = []

    for i, prompt_item in enumerate(prompts):
        unit_id = prompt_item.get("unit_id")
        prompt_text = prompt_item.get("prompt", "")

        # Add delay between calls (except for first call)
        if i > 0 and delay_between_calls > 0:
            time.sleep(delay_between_calls)

        # Retry loop with exponential backoff
        result = None
        last_error = None
        error_type = None
        backoff = initial_backoff
        call_start = time.time()

        for attempt in range(max_retries):
            try:
                call_start = time.time()
                result = _make_provider_call(provider, prompt_text, unit_id)
                break  # Success
            except RateLimitError as e:
                last_error = e
                error_type = "rate_limit"
                if attempt < max_retries - 1:
                    # Exponential backoff for rate limits
                    time.sleep(backoff)
                    backoff *= backoff_multiplier
            except ProviderError as e:
                # Check if it's a transient error that should be retried
                error_str = str(e).lower()
                if "503" in str(e) or "timeout" in error_str or "unavailable" in error_str:
                    last_error = e
                    error_type = "timeout"
                    if attempt < max_retries - 1:
                        time.sleep(backoff)
                        backoff *= backoff_multiplier
                else:
                    # Non-retryable error (auth/billing) — abort the entire run
                    error_str_check = str(e)
                    if any(code in error_str_check for code in ("400", "401", "403")):
                        raise FatalProviderError(f"Fatal provider error (auth/billing): {e}") from e
                    last_error = e
                    error_type = "api_error"
                    break

        call_duration = time.time() - call_start

        if result is None:
            # All retries failed - create failure result
            error_message = str(last_error) if last_error else "Unknown error"
            error_result = {
                "unit_id": unit_id,
                "response": None,
                "error": error_message,
                "_metadata": {
                    "input_tokens": 0,
                    "output_tokens": 0,
                    "model": provider.model,
                    "finish_reason": "ERROR"
                }
            }
            results.append(error_result)
            # Trace telemetry for failed call
            if trace_callback:
                _status = (error_type or "ERROR").upper()
                trace_callback(unit_id, call_duration, _status)
            # Report progress for failed unit (0 tokens for failed calls)
            if progress_callback:
                should_continue = progress_callback(unit_id, False, error_type or "api_error", 0, 0, error_message)
                if should_continue is False:
                    break
        else:
            results.append(result)
            # Extract token counts from result metadata
            metadata = result.get("_metadata", {})
            input_tokens = metadata.get("input_tokens", 0)
            output_tokens = metadata.get("output_tokens", 0)
            # Trace telemetry for successful call
            if trace_callback:
                trace_callback(unit_id, call_duration, "200")
            # Report progress for successful API call (validation happens later)
            if progress_callback:
                should_continue = progress_callback(unit_id, True, None, input_tokens, output_tokens, None)
                if should_continue is False:
                    break

    return results


def _make_provider_call(provider: "LLMProvider", prompt_text: str, unit_id: str) -> dict:
    """
    Make a single API call using the provider abstraction.

    Args:
        provider: LLMProvider instance
        prompt_text: The prompt to send
        unit_id: Unit identifier for the result

    Returns:
        Dict with parsed response fields merged in, plus unit_id and _metadata.
        If JSON parsing fails, includes raw 'response' field instead.

    Raises:
        RateLimitError: For rate limit errors (should retry)
        ProviderError: For other errors
    """
    # Call the provider's realtime API
    realtime_result = provider.generate_realtime(prompt_text)

    # Extract fields from RealtimeResult
    response_text = realtime_result.get("content", "")
    input_tokens = realtime_result.get("input_tokens", 0)
    output_tokens = realtime_result.get("output_tokens", 0)
    finish_reason = realtime_result.get("finish_reason", "STOP")

    # Build metadata
    metadata = {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "model": provider.model,
        "finish_reason": finish_reason
    }

    # Try to parse JSON from response and merge into result
    parsed = parse_json_response(response_text)
    if parsed is not None and isinstance(parsed, dict):
        # Merge parsed fields into result (alongside unit_id and _metadata)
        result = {"unit_id": unit_id}
        result.update(parsed)
        result["_raw_text"] = response_text  # Preserve original LLM text
        result["_metadata"] = metadata
        return result
    else:
        # JSON parsing failed - keep raw response for validation to report
        return {
            "unit_id": unit_id,
            "_raw_text": response_text,  # Preserve original LLM text
            "response": response_text,
            "_metadata": metadata
        }



# parse_json_response is imported from scripts.octobatch_utils
