"""
openai.py - OpenAI LLM provider implementation.

Implements the LLMProvider interface for OpenAI's API,
supporting both batch and realtime (synchronous) API modes.

Environment variables required:
    OPENAI_API_KEY: API key for OpenAI API access
"""

import json
import os
import sys
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


# OpenAI status code normalization
OPENAI_STATUS_MAP = {
    "validating": BatchStatus.RUNNING,
    "in_progress": BatchStatus.RUNNING,
    "finalizing": BatchStatus.RUNNING,
    "completed": BatchStatus.COMPLETED,
    "failed": BatchStatus.FAILED,
    "expired": BatchStatus.FAILED,
    "cancelling": BatchStatus.CANCELLED,
    "cancelled": BatchStatus.CANCELLED,
}

class OpenAIProvider(LLMProvider):
    """
    OpenAI LLM provider for both batch and realtime APIs.

    Uses OpenAI's API for LLM inference. Supports:
    - Realtime: Synchronous Chat Completions API
    - Batch: Asynchronous Batch API (50% cost savings)

    Config options (under api:):
        model: Model to use (default: "gpt-4o-mini")
    """

    def __init__(self, config: dict):
        """
        Initialize the OpenAI provider.

        Args:
            config: Configuration dict with api settings

        Raises:
            AuthenticationError: If API key not set
            ImportError: If openai package not installed
        """
        super().__init__(config)
        self._validate_sdk()
        self._validate_credentials()
        self._init_client()

        # Extract API config
        api_config = config.get("api", {})
        provider_info = LLMProvider.get_provider_info("openai")
        self.model = api_config.get("model", provider_info.get("default_model", "gpt-4o-mini"))

        # Look up model pricing from registry
        registry_models = LLMProvider.get_provider_models("openai")
        model_info = registry_models.get(self.model, {})
        registry_defaults = LLMProvider.get_default_pricing()

        default_input = model_info.get("input_per_million", registry_defaults.get("input_per_million", 1.00))
        default_output = model_info.get("output_per_million", registry_defaults.get("output_per_million", 2.00))
        default_multiplier = provider_info.get("realtime_multiplier", registry_defaults.get("realtime_multiplier", 2.0))

        # Pricing comes exclusively from registry
        self.input_rate = default_input
        self.output_rate = default_output
        self.realtime_multiplier = default_multiplier

    def _validate_sdk(self):
        """Check that openai SDK is installed."""
        try:
            import openai
            self._openai = openai
        except ImportError:
            raise ImportError(
                "openai package not installed. "
                "Install with: pip install openai>=1.0.0"
            )

    def _validate_credentials(self):
        """Check that required credentials are available."""
        api_key = os.environ.get("OPENAI_API_KEY")
        if not api_key:
            raise AuthenticationError(
                "OPENAI_API_KEY environment variable not set. "
                "Required for OpenAI API access. "
                "Get your API key from https://platform.openai.com/api-keys"
            )
        self._api_key = api_key

    def _init_client(self):
        """Initialize the OpenAI client."""
        from openai import OpenAI
        self._client = OpenAI(api_key=self._api_key)

    def get_api_key_env_var(self) -> str:
        """Get the environment variable name for OpenAI API key."""
        return "OPENAI_API_KEY"

    # === Realtime API ===

    def generate_realtime(
        self,
        prompt: str,
        schema: dict | None = None
    ) -> RealtimeResult:
        """
        Make a single synchronous API request to OpenAI.

        Args:
            prompt: The prompt text to send
            schema: Optional JSON schema (enables json_object response format)

        Returns:
            RealtimeResult with content, token counts, and finish reason

        Raises:
            RateLimitError: For 429 or quota errors
            ProviderError: For other API errors
        """
        try:
            # Build request parameters
            kwargs: dict[str, Any] = {
                "model": self.model,
                "messages": [{"role": "user", "content": prompt}],
            }

            # Enable JSON mode if schema is provided
            if schema:
                kwargs["response_format"] = {"type": "json_object"}

            response = self._client.chat.completions.create(**kwargs)

            # Extract content
            content = ""
            if response.choices and response.choices[0].message:
                content = response.choices[0].message.content or ""

            # Extract token metadata
            input_tokens = 0
            output_tokens = 0
            if response.usage:
                input_tokens = response.usage.prompt_tokens or 0
                output_tokens = response.usage.completion_tokens or 0

            # Get finish reason
            finish_reason = "stop"
            if response.choices and response.choices[0].finish_reason:
                finish_reason = response.choices[0].finish_reason

            return RealtimeResult(
                content=content,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                finish_reason=finish_reason.upper()
            )

        except self._openai.RateLimitError as e:
            raise RateLimitError(f"OpenAI rate limit exceeded: {e}")
        except self._openai.AuthenticationError as e:
            raise AuthenticationError(f"OpenAI authentication failed: {e}")
        except self._openai.APIError as e:
            raise ProviderError(f"OpenAI API error: {e}")
        except Exception as e:
            error_str = str(e).lower()
            if "429" in str(e) or "rate" in error_str or "quota" in error_str:
                raise RateLimitError(f"Rate limit exceeded: {e}")
            raise ProviderError(f"OpenAI API error: {e}")

    # === Batch API ===

    def format_batch_request(
        self,
        unit_id: str,
        prompt: str,
        schema: dict | None = None
    ) -> dict:
        """
        Format a single unit into OpenAI batch JSONL format.

        Args:
            unit_id: Unique identifier for this unit
            prompt: The prompt text
            schema: Optional JSON schema (enables json_object response format)

        Returns:
            Dict in OpenAI batch format
        """
        body: dict[str, Any] = {
            "model": self.model,
            "messages": [{"role": "user", "content": prompt}],
        }

        # Enable JSON mode if schema is provided
        if schema:
            body["response_format"] = {"type": "json_object"}

        return {
            "custom_id": unit_id,
            "method": "POST",
            "url": "/v1/chat/completions",
            "body": body
        }

    def upload_batch_file(self, file_path: Path) -> str:
        """
        Upload a JSONL file for batch processing.

        Args:
            file_path: Path to the JSONL file

        Returns:
            File identifier (OpenAI file ID)

        Raises:
            ProviderError: If upload fails
        """
        try:
            with open(file_path, "rb") as f:
                file_upload = self._client.files.create(
                    file=f,
                    purpose="batch"
                )
            return file_upload.id
        except self._openai.RateLimitError as e:
            raise RateLimitError(f"OpenAI rate limit during upload: {e}")
        except self._openai.APIError as e:
            raise ProviderError(f"Failed to upload batch file: {e}")
        except Exception as e:
            raise ProviderError(f"Failed to upload batch file: {e}")

    def create_batch(self, file_id: str) -> str:
        """
        Create a batch job from an uploaded file.

        Args:
            file_id: The file identifier from upload_batch_file()

        Returns:
            Batch job identifier (OpenAI batch ID)

        Raises:
            RateLimitError: For rate limit errors
            ProviderError: For other errors
        """
        try:
            batch = self._client.batches.create(
                input_file_id=file_id,
                endpoint="/v1/chat/completions",
                completion_window="24h"
            )
            return batch.id
        except self._openai.RateLimitError as e:
            raise RateLimitError(f"OpenAI rate limit during batch creation: {e}")
        except self._openai.APIError as e:
            raise ProviderError(f"Failed to create batch: {e}")
        except Exception as e:
            raise ProviderError(f"Failed to create batch: {e}")

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
            batch = self._client.batches.retrieve(batch_id)
        except self._openai.NotFoundError:
            raise ProviderError(f"Batch not found: {batch_id}")
        except self._openai.APIError as e:
            raise ProviderError(f"Failed to get batch status: {e}")
        except Exception as e:
            raise ProviderError(f"Failed to get batch status: {e}")

        # Normalize status
        openai_status = batch.status or "unknown"
        status = OPENAI_STATUS_MAP.get(openai_status, BatchStatus.RUNNING)

        # Calculate progress
        total = batch.request_counts.total if batch.request_counts else 0
        completed = batch.request_counts.completed if batch.request_counts else 0
        progress = f"{completed}/{total}" if total > 0 else None

        # Extract error if failed
        error = None
        if status == BatchStatus.FAILED:
            if batch.errors and batch.errors.data:
                error_messages = [e.message for e in batch.errors.data if e.message]
                error = "; ".join(error_messages) if error_messages else openai_status
            else:
                error = openai_status

        # Extract timestamps
        created_at = None
        updated_at = None
        if batch.created_at:
            from datetime import datetime, timezone
            created_at = datetime.fromtimestamp(batch.created_at, tz=timezone.utc).isoformat()
        if batch.completed_at:
            from datetime import datetime, timezone
            updated_at = datetime.fromtimestamp(batch.completed_at, tz=timezone.utc).isoformat()
        elif batch.in_progress_at:
            from datetime import datetime, timezone
            updated_at = datetime.fromtimestamp(batch.in_progress_at, tz=timezone.utc).isoformat()

        return BatchStatusInfo(
            status=status,
            progress=progress,
            error=error,
            provider_status=openai_status,
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
        # Get batch info for status and output file
        try:
            batch = self._client.batches.retrieve(batch_id)
        except self._openai.NotFoundError:
            raise ProviderError(f"Batch not found: {batch_id}")
        except self._openai.APIError as e:
            raise ProviderError(f"Failed to get batch info: {e}")

        status = OPENAI_STATUS_MAP.get(batch.status or "", BatchStatus.RUNNING)
        if status not in (BatchStatus.COMPLETED, BatchStatus.FAILED):
            raise ProviderError(
                f"Batch not completed. Current status: {batch.status}"
            )

        # Get output file ID
        output_file_id = batch.output_file_id
        if not output_file_id:
            # Check for error file if no output file
            if batch.error_file_id:
                raise ProviderError(
                    f"Batch failed with errors. Error file: {batch.error_file_id}"
                )
            raise ProviderError("No output file available for batch")

        # Download results
        try:
            file_content = self._client.files.content(output_file_id)
            content = file_content.text
        except self._openai.APIError as e:
            raise ProviderError(f"Failed to download results: {e}")
        except Exception as e:
            raise ProviderError(f"Failed to download results: {e}")

        # Parse results
        results: list[BatchResult] = []
        total_input_tokens = 0
        total_output_tokens = 0

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

        # Build metadata
        created_at = None
        completed_at = None
        if batch.created_at:
            from datetime import datetime, timezone
            created_at = datetime.fromtimestamp(batch.created_at, tz=timezone.utc).isoformat()
        if batch.completed_at:
            from datetime import datetime, timezone
            completed_at = datetime.fromtimestamp(batch.completed_at, tz=timezone.utc).isoformat()

        metadata = BatchMetadata(
            total_input_tokens=total_input_tokens,
            total_output_tokens=total_output_tokens,
            started_at=created_at,
            completed_at=completed_at,
            provider="openai",
            model=self.model
        )

        return results, metadata

    def _parse_batch_result(self, raw_result: dict) -> tuple[BatchResult, int, int]:
        """
        Parse a single OpenAI batch result.

        OpenAI batch result format:
        {
            "id": "...",
            "custom_id": "unit_id_here",
            "response": {
                "status_code": 200,
                "body": {
                    "choices": [{"message": {"content": "..."}, "finish_reason": "stop"}],
                    "usage": {"prompt_tokens": 10, "completion_tokens": 20}
                }
            },
            "error": null  # or {"code": "...", "message": "..."}
        }

        Returns:
            Tuple of (BatchResult, input_tokens, output_tokens)
        """
        unit_id = raw_result.get("custom_id", "unknown")
        input_tokens = 0
        output_tokens = 0
        content = None
        error = None

        # Check for row-level error
        row_error = raw_result.get("error")
        if row_error:
            error_code = row_error.get("code", "unknown")
            error_message = row_error.get("message", "Unknown error")
            error = f"{error_code}: {error_message}"
            return BatchResult(
                unit_id=unit_id,
                content=None,
                input_tokens=0,
                output_tokens=0,
                error=error
            ), 0, 0

        # Extract response
        response = raw_result.get("response", {})
        status_code = response.get("status_code", 0)

        if status_code != 200:
            error = f"HTTP {status_code}"
            return BatchResult(
                unit_id=unit_id,
                content=None,
                input_tokens=0,
                output_tokens=0,
                error=error
            ), 0, 0

        body = response.get("body", {})

        # Extract token usage
        usage = body.get("usage", {})
        input_tokens = usage.get("prompt_tokens", 0)
        output_tokens = usage.get("completion_tokens", 0)

        # Extract content
        try:
            choices = body.get("choices", [])
            if not choices:
                error = "no_choices"
            else:
                choice = choices[0]
                message = choice.get("message", {})
                content = message.get("content", "")

                # Check finish reason
                finish_reason = choice.get("finish_reason", "")
                if finish_reason and finish_reason not in ("stop", "length"):
                    # Content filter or other issue
                    if not content:
                        error = f"finish_reason: {finish_reason}"
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
        # Check current status first
        try:
            batch = self._client.batches.retrieve(batch_id)
            status = OPENAI_STATUS_MAP.get(batch.status or "", BatchStatus.RUNNING)

            if status in (BatchStatus.COMPLETED, BatchStatus.FAILED, BatchStatus.CANCELLED):
                return False

            self._client.batches.cancel(batch_id)
            return True
        except self._openai.NotFoundError:
            raise ProviderError(f"Batch not found: {batch_id}")
        except self._openai.APIError as e:
            error_str = str(e)
            if "already" in error_str.lower() or "completed" in error_str.lower():
                return False
            raise ProviderError(f"Failed to cancel batch: {e}")
        except Exception as e:
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

        OpenAI batch pricing is 50% of realtime pricing.

        Args:
            input_tokens: Number of input tokens
            output_tokens: Number of output tokens
            is_batch: True for batch pricing (1x), False for realtime (2x)

        Returns:
            Estimated cost in USD
        """
        # Batch pricing is already the discounted rate
        # Realtime is 2x batch (the full rate)
        multiplier = 1.0 if is_batch else self.realtime_multiplier
        cost = (
            (input_tokens / 1_000_000 * self.input_rate) +
            (output_tokens / 1_000_000 * self.output_rate)
        ) * multiplier
        return cost

    # === Helpers ===

    def _log_error(self, data: dict):
        """Log error to stderr in JSON format."""
        sys.stderr.write(json.dumps(data) + '\n')
