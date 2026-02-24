"""
gemini.py - Gemini LLM provider implementation.

Implements the LLMProvider interface for Google's Gemini API,
supporting both batch and realtime (synchronous) API modes.

Environment variables required:
    GOOGLE_API_KEY: API key for Gemini API access
"""

import json
import os
import re
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

from .base import (
    LLMProvider,
    BatchStatus,
    BatchStatusInfo,
    BatchResult,
    BatchMetadata,
    RealtimeResult,
    ProviderError,
    RateLimitError,
    AuthenticationError,
)


# Gemini status code normalization
GEMINI_STATUS_MAP = {
    "JOB_STATE_PENDING": BatchStatus.PENDING,
    "JOB_STATE_QUEUED": BatchStatus.PENDING,
    "JOB_STATE_RUNNING": BatchStatus.RUNNING,
    "JOB_STATE_SUCCEEDED": BatchStatus.COMPLETED,
    "JOB_STATE_FAILED": BatchStatus.FAILED,
    "JOB_STATE_CANCELLED": BatchStatus.CANCELLED,
    "JOB_STATE_CANCELLING": BatchStatus.RUNNING,
}


def _normalize_gemini_status(gemini_state: str) -> BatchStatus:
    """
    Normalize Gemini status code to BatchStatus enum.

    Args:
        gemini_state: Raw Gemini state string (e.g., "JOB_STATE_RUNNING")

    Returns:
        BatchStatus enum value
    """
    # Try exact match first
    if gemini_state in GEMINI_STATUS_MAP:
        return GEMINI_STATUS_MAP[gemini_state]

    # Try partial match for enum-style states
    for key, value in GEMINI_STATUS_MAP.items():
        if key in gemini_state:
            return value

    # Fallback heuristics
    state_upper = gemini_state.upper()
    if "SUCCEEDED" in state_upper or "COMPLETED" in state_upper:
        return BatchStatus.COMPLETED
    elif "FAILED" in state_upper:
        return BatchStatus.FAILED
    elif "CANCELLED" in state_upper:
        return BatchStatus.CANCELLED
    elif "PENDING" in state_upper or "QUEUED" in state_upper:
        return BatchStatus.PENDING
    elif "RUNNING" in state_upper or "PROCESSING" in state_upper:
        return BatchStatus.RUNNING

    # Unknown state, treat as running
    return BatchStatus.RUNNING


class GeminiProvider(LLMProvider):
    """
    Gemini LLM provider for both batch and realtime APIs.

    Uses Google's Gemini API for LLM inference. Supports:
    - Realtime: Synchronous individual API calls (2x cost)
    - Batch: Asynchronous batch processing (1x cost, higher latency)

    Config options (under api:):
        model: Model to use (default: "gemini-2.0-flash-001")
        max_inflight_batches: Max concurrent batches (default: 10)
        retry:
            max_attempts: Max retry attempts (default: 5)
            initial_delay_seconds: Initial retry delay (default: 30)
            backoff_multiplier: Exponential backoff factor (default: 2)
    """

    def __init__(self, config: dict):
        """
        Initialize the Gemini provider.

        Args:
            config: Configuration dict with api settings

        Raises:
            AuthenticationError: If API key not set
            ImportError: If google-genai package not installed
        """
        super().__init__(config)
        self._validate_sdk()
        self._validate_credentials()
        self._init_client()

        # Extract API config
        api_config = config.get("api", {})
        self.model = api_config.get("model", "gemini-2.0-flash-001")
        self.max_inflight = api_config.get("max_inflight_batches", 10)

        # Look up model pricing from registry
        registry_models = LLMProvider.get_provider_models("gemini")
        model_info = registry_models.get(self.model, {})
        registry_defaults = LLMProvider.get_default_pricing()

        # Default rates from registry (model-specific or global defaults)
        default_input = model_info.get("input_per_million", registry_defaults.get("input_per_million", 1.00))
        default_output = model_info.get("output_per_million", registry_defaults.get("output_per_million", 2.00))
        default_multiplier = LLMProvider.get_provider_info("gemini").get("realtime_multiplier", registry_defaults.get("realtime_multiplier", 2.0))

        # Pricing comes exclusively from registry
        self.input_rate = default_input
        self.output_rate = default_output
        self.realtime_multiplier = default_multiplier

        # Retry config
        retry_config = api_config.get("retry", {})
        self.retry_max_attempts = retry_config.get("max_attempts", 5)
        self.retry_initial_delay = retry_config.get("initial_delay_seconds", 30)
        self.retry_backoff = retry_config.get("backoff_multiplier", 2)

    def _validate_sdk(self):
        """Check that google-genai SDK is installed."""
        try:
            from google import genai
            self._genai = genai
        except ImportError:
            raise ImportError(
                "google-genai package not installed. "
                "Install with: pip install google-genai"
            )

    def _validate_credentials(self):
        """Check that required credentials are available."""
        api_key = os.environ.get("GOOGLE_API_KEY")
        if not api_key:
            raise AuthenticationError(
                "GOOGLE_API_KEY environment variable not set. "
                "Required for Gemini API access. "
                "Get your API key from https://aistudio.google.com/"
            )
        self._api_key = api_key

    def _init_client(self):
        """Initialize the Gemini client.

        Temporarily unsets GEMINI_API_KEY during client creation to suppress the
        google-genai SDK warning 'Both GOOGLE_API_KEY and GEMINI_API_KEY are set'.
        The SDK's Client.__init__ calls get_env_api_key() unconditionally, even
        when api_key is passed explicitly.
        """
        saved = os.environ.pop("GEMINI_API_KEY", None)
        try:
            # Add explicit timeout (120s) to prevent indefinite hangs
            from google.genai import types
            self._client = self._genai.Client(
                api_key=self._api_key,
                http_options=types.HttpOptions(timeout=120000)
            )
        finally:
            if saved is not None:
                os.environ["GEMINI_API_KEY"] = saved

    def get_api_key_env_var(self) -> str:
        """Get the environment variable name for Gemini API key."""
        return "GOOGLE_API_KEY"

    # === Realtime API ===

    def generate_realtime(
        self,
        prompt: str,
        schema: dict | None = None
    ) -> RealtimeResult:
        """
        Make a single synchronous API request to Gemini.

        Args:
            prompt: The prompt text to send
            schema: Optional JSON schema (not currently used for Gemini)

        Returns:
            RealtimeResult with content, token counts, and finish reason

        Raises:
            RateLimitError: For 429 or quota errors
            ProviderError: For other API errors
        """
        try:
            response = self._client.models.generate_content(
                model=self.model,
                contents=prompt
            )

            # Extract response text
            content = ""
            if response.text:
                content = response.text

            # Extract token metadata
            input_tokens = 0
            output_tokens = 0
            if hasattr(response, 'usage_metadata') and response.usage_metadata:
                usage = response.usage_metadata
                input_tokens = getattr(usage, 'prompt_token_count', 0) or 0
                output_tokens = getattr(usage, 'candidates_token_count', 0) or 0

            # Get finish reason
            finish_reason = "STOP"
            if response.candidates:
                candidate = response.candidates[0]
                if hasattr(candidate, 'finish_reason'):
                    fr = candidate.finish_reason
                    finish_reason = fr.name if hasattr(fr, 'name') else str(fr)

            return RealtimeResult(
                content=content,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                finish_reason=finish_reason
            )

        except Exception as e:
            error_str = str(e).lower()

            # Check for rate limit errors
            if "429" in str(e) or "rate" in error_str or "quota" in error_str:
                raise RateLimitError(f"Rate limit exceeded: {e}")

            # Check for other transient errors
            if "503" in str(e) or "timeout" in error_str or "unavailable" in error_str:
                raise RateLimitError(f"Transient error: {e}")

            # Non-retryable error
            raise ProviderError(f"Gemini API error: {e}")

    # === Batch API ===

    def format_batch_request(
        self,
        unit_id: str,
        prompt: str,
        schema: dict | None = None
    ) -> dict:
        """
        Format a single unit into Gemini batch JSONL format.

        Args:
            unit_id: Unique identifier for this unit
            prompt: The prompt text
            schema: Optional JSON schema (not used for Gemini)

        Returns:
            Dict in Gemini batch format
        """
        return {
            "key": unit_id,
            "request": {
                "contents": [{
                    "parts": [{"text": prompt}]
                }]
            }
        }

    def upload_batch_file(self, file_path: Path) -> str:
        """
        Upload a JSONL file for batch processing.

        Args:
            file_path: Path to the JSONL file

        Returns:
            File identifier (Gemini file name)

        Raises:
            ProviderError: If upload fails
        """
        from google.genai import types

        try:
            file_upload = self._client.files.upload(
                file=str(file_path),
                config=types.UploadFileConfig(mime_type='application/json')
            )
            return file_upload.name
        except Exception as e:
            raise ProviderError(f"Failed to upload batch file: {e}")

    def create_batch(self, file_id: str) -> str:
        """
        Create a batch job from an uploaded file.

        Args:
            file_id: The file identifier from upload_batch_file()

        Returns:
            Batch job identifier (Gemini operation name)

        Raises:
            RateLimitError: For rate limit errors
            ProviderError: For other errors
        """
        from google.genai.errors import ClientError

        for attempt in range(self.retry_max_attempts):
            try:
                batch_job = self._client.batches.create(
                    model=self.model,
                    src=file_id
                )
                return batch_job.name

            except ClientError as e:
                error_str = str(e)
                is_rate_limit = "429" in error_str or "RESOURCE_EXHAUSTED" in error_str

                if is_rate_limit and attempt < self.retry_max_attempts - 1:
                    wait_time = self.retry_initial_delay * (self.retry_backoff ** attempt)
                    self._log_error({
                        "event": "rate_limit_retry",
                        "attempt": attempt + 1,
                        "max_attempts": self.retry_max_attempts,
                        "wait_seconds": wait_time
                    })
                    time.sleep(wait_time)
                elif is_rate_limit:
                    raise RateLimitError(
                        f"Rate limit exceeded after {self.retry_max_attempts} attempts: {e}"
                    )
                else:
                    raise ProviderError(f"Batch creation failed: {e}")

        raise ProviderError("Batch creation failed after all retries")

    def get_batch_status(self, batch_id: str) -> BatchStatusInfo:
        """
        Check the status of a batch job.

        Args:
            batch_id: The batch identifier

        Returns:
            BatchStatusInfo with status and metadata

        Raises:
            ProviderError: If status check fails
        """
        try:
            batch = self._client.batches.get(name=batch_id)
        except Exception as e:
            error_str = str(e)
            if "404" in error_str or "NOT_FOUND" in error_str:
                raise ProviderError(f"Batch not found: {batch_id}")
            raise ProviderError(f"Failed to poll batch status: {e}")

        # Normalize status
        gemini_state = str(batch.state)
        status = _normalize_gemini_status(gemini_state)

        # Calculate progress
        total = getattr(batch, 'request_count', 0)
        completed = getattr(batch, 'completed_count', 0)
        progress = f"{completed}/{total}" if total > 0 else None

        # Extract error if failed
        error = None
        if status == BatchStatus.FAILED:
            error = getattr(batch, 'error_message', None) or gemini_state

        # Extract timestamps
        created_at = self._format_timestamp(
            getattr(batch, 'create_time', None) or getattr(batch, 'createTime', None)
        )
        updated_at = self._format_timestamp(
            getattr(batch, 'update_time', None) or getattr(batch, 'updateTime', None)
        )

        return BatchStatusInfo(
            status=status,
            progress=progress,
            error=error,
            provider_status=gemini_state,
            created_at=created_at,
            updated_at=updated_at
        )

    def download_batch_results(self, batch_id: str) -> tuple[list[BatchResult], BatchMetadata]:
        """
        Download and parse results from a completed batch.

        Args:
            batch_id: The batch identifier

        Returns:
            Tuple of (list of BatchResult, BatchMetadata)

        Raises:
            ProviderError: If download or parsing fails
        """
        # Get status first for timestamps
        status_info = self.get_batch_status(batch_id)
        if status_info["status"] not in (BatchStatus.COMPLETED, BatchStatus.FAILED):
            raise ProviderError(
                f"Batch not completed. Current status: {status_info['status'].value}"
            )

        # Get batch to access output file
        try:
            batch = self._client.batches.get(name=batch_id)
        except Exception as e:
            raise ProviderError(f"Failed to get batch info: {e}")

        if not batch.dest or not batch.dest.file_name:
            raise ProviderError("No output file available for batch")

        # Download results
        try:
            file_content = self._client.files.download(file=batch.dest.file_name)
        except Exception as e:
            raise ProviderError(f"Failed to download results: {e}")

        # Parse results
        results: list[BatchResult] = []
        total_input_tokens = 0
        total_output_tokens = 0

        content = file_content.decode('utf-8') if isinstance(file_content, bytes) else file_content

        for line in content.strip().split('\n'):
            if not line:
                continue

            try:
                raw_result = json.loads(line)
                batch_result, input_tokens, output_tokens = self._parse_batch_result(raw_result)
                results.append(batch_result)
                total_input_tokens += input_tokens
                total_output_tokens += output_tokens
            except json.JSONDecodeError as e:
                self._log_error({"event": "parse_error", "error": str(e)})
                continue

        metadata = BatchMetadata(
            total_input_tokens=total_input_tokens,
            total_output_tokens=total_output_tokens,
            started_at=status_info.get("created_at"),
            completed_at=status_info.get("updated_at"),
            provider="gemini",
            model=self.model
        )

        return results, metadata

    def _parse_batch_result(self, raw_result: dict) -> tuple[BatchResult, int, int]:
        """
        Parse a single Gemini batch result.

        Returns:
            Tuple of (BatchResult, input_tokens, output_tokens)
        """
        unit_id = raw_result.get("key", "unknown")
        input_tokens = 0
        output_tokens = 0
        content = None
        error = None

        response = raw_result.get("response", {})

        # Extract token usage
        usage = response.get("usageMetadata", {})
        if usage:
            input_tokens = usage.get("promptTokenCount", 0)
            output_tokens = usage.get("candidatesTokenCount", 0)

        # Check for error
        if "error" in raw_result:
            error = str(raw_result["error"])
            return BatchResult(
                unit_id=unit_id,
                content=None,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                error=error
            ), input_tokens, output_tokens

        # Extract content
        try:
            candidates = response.get("candidates", [])
            if not candidates:
                finish_reason = response.get("promptFeedback", {}).get("blockReason")
                error = f"safety_filter: {finish_reason}" if finish_reason else "no_response"
            else:
                candidate = candidates[0]
                finish_reason = candidate.get("finishReason", "")

                if finish_reason and finish_reason not in ("STOP", "MAX_TOKENS"):
                    error = f"finish_reason: {finish_reason}"
                else:
                    parts = candidate.get("content", {}).get("parts", [])
                    if parts:
                        content = parts[0].get("text", "")
                    if not content:
                        error = "empty_response"
        except Exception as e:
            error = f"parse_error: {e}"

        return BatchResult(
            unit_id=unit_id,
            content=content,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            error=error
        ), input_tokens, output_tokens

    def cancel_batch(self, batch_id: str) -> bool:
        """
        Cancel a running batch job.

        Args:
            batch_id: The batch identifier

        Returns:
            True if cancelled, False if already completed/failed

        Raises:
            ProviderError: If cancellation fails
        """
        status_info = self.get_batch_status(batch_id)
        if status_info["status"] in (BatchStatus.COMPLETED, BatchStatus.FAILED, BatchStatus.CANCELLED):
            return False

        try:
            self._client.batches.cancel(name=batch_id)
            return True
        except Exception as e:
            error_str = str(e)
            if "CANCELLED" in error_str or "COMPLETED" in error_str:
                return False
            raise ProviderError(f"Failed to cancel batch: {e}")

    # === Pricing ===

    def estimate_cost(
        self,
        input_tokens: int,
        output_tokens: int,
        is_batch: bool = True
    ) -> float:
        """
        Estimate cost in USD for token usage.

        Args:
            input_tokens: Number of input tokens
            output_tokens: Number of output tokens
            is_batch: True for batch pricing (1x), False for realtime (2x)

        Returns:
            Estimated cost in USD
        """
        multiplier = 1.0 if is_batch else self.realtime_multiplier
        cost = (
            (input_tokens / 1_000_000 * self.input_rate) +
            (output_tokens / 1_000_000 * self.output_rate)
        ) * multiplier
        return cost

    # === Helpers ===

    def _format_timestamp(self, timestamp) -> str | None:
        """Format a timestamp to ISO 8601 string."""
        if timestamp is None:
            return None
        try:
            if hasattr(timestamp, 'isoformat'):
                return timestamp.isoformat()
            if hasattr(timestamp, 'ToDatetime'):
                return timestamp.ToDatetime().isoformat() + "Z"
            return str(timestamp)
        except Exception:
            return str(timestamp)

    def _log_error(self, data: dict):
        """Log error to stderr in JSON format."""
        sys.stderr.write(json.dumps(data) + '\n')
