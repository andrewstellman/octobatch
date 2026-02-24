"""
anthropic.py - Anthropic Claude LLM provider implementation.

Implements the LLMProvider interface for Anthropic's Claude API,
supporting both batch and realtime (synchronous) API modes.

Environment variables required:
    ANTHROPIC_API_KEY: API key for Anthropic API access
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


# Anthropic status code normalization
# Note: Anthropic uses "in_progress" and "ended" (with processing_status for details)
ANTHROPIC_STATUS_MAP = {
    "in_progress": BatchStatus.RUNNING,
    "canceling": BatchStatus.CANCELLED,
    "ended": BatchStatus.COMPLETED,  # Need to check processing_status for actual result
}

class AnthropicProvider(LLMProvider):
    """
    Anthropic Claude LLM provider for both batch and realtime APIs.

    Uses Anthropic's Claude API for LLM inference. Supports:
    - Realtime: Synchronous Messages API
    - Batch: Message Batches API (50% cost savings)

    Config options (under api:):
        model: Model to use (default: "claude-sonnet-4-20250514")
        max_tokens: Maximum tokens to generate (default: 4096)
    """

    DEFAULT_MAX_TOKENS = 4096

    def __init__(self, config: dict):
        """
        Initialize the Anthropic provider.

        Args:
            config: Configuration dict with api settings

        Raises:
            AuthenticationError: If API key not set
            ImportError: If anthropic package not installed
        """
        super().__init__(config)
        self._validate_sdk()
        self._validate_credentials()
        self._init_client()

        # Extract API config
        api_config = config.get("api", {})
        provider_info = LLMProvider.get_provider_info("anthropic")
        self.model = api_config.get("model", provider_info.get("default_model", "claude-sonnet-4-20250514"))
        self.max_tokens = api_config.get("max_tokens", self.DEFAULT_MAX_TOKENS)

        # Look up model pricing from registry
        registry_models = LLMProvider.get_provider_models("anthropic")
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
        """Check that anthropic SDK is installed."""
        try:
            import anthropic
            self._anthropic = anthropic
        except ImportError:
            raise ImportError(
                "anthropic package not installed. "
                "Install with: pip install anthropic>=0.30.0"
            )

    def _validate_credentials(self):
        """Check that required credentials are available."""
        api_key = os.environ.get("ANTHROPIC_API_KEY")
        if not api_key:
            raise AuthenticationError(
                "ANTHROPIC_API_KEY environment variable not set. "
                "Required for Anthropic API access. "
                "Get your API key from https://console.anthropic.com/settings/keys"
            )
        self._api_key = api_key

    def _init_client(self):
        """Initialize the Anthropic client."""
        from anthropic import Anthropic
        self._client = Anthropic(api_key=self._api_key)

    def get_api_key_env_var(self) -> str:
        """Get the environment variable name for Anthropic API key."""
        return "ANTHROPIC_API_KEY"

    # === Realtime API ===

    def generate_realtime(
        self,
        prompt: str,
        schema: dict | None = None
    ) -> RealtimeResult:
        """
        Make a single synchronous API request to Anthropic Claude.

        Args:
            prompt: The prompt text to send
            schema: Optional JSON schema (included in prompt for Claude)

        Returns:
            RealtimeResult with content, token counts, and finish reason

        Raises:
            RateLimitError: For 429 or quota errors
            ProviderError: For other API errors
        """
        try:
            # Build the message content
            # If schema is provided, we can add instructions to return JSON
            message_content = prompt
            if schema:
                # Claude doesn't have a built-in JSON mode, but we can instruct it
                message_content = f"{prompt}\n\nRespond with valid JSON matching this schema: {json.dumps(schema)}"

            response = self._client.messages.create(
                model=self.model,
                max_tokens=self.max_tokens,
                messages=[{"role": "user", "content": message_content}]
            )

            # Extract content
            content = ""
            if response.content and len(response.content) > 0:
                # Content blocks can be text or other types
                first_block = response.content[0]
                if hasattr(first_block, 'text'):
                    content = first_block.text

            # Extract token metadata
            input_tokens = 0
            output_tokens = 0
            if response.usage:
                input_tokens = response.usage.input_tokens or 0
                output_tokens = response.usage.output_tokens or 0

            # Get stop reason (Anthropic calls it stop_reason)
            finish_reason = response.stop_reason or "end_turn"

            return RealtimeResult(
                content=content,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                finish_reason=finish_reason.upper()
            )

        except self._anthropic.RateLimitError as e:
            raise RateLimitError(f"Anthropic rate limit exceeded: {e}")
        except self._anthropic.AuthenticationError as e:
            raise AuthenticationError(f"Anthropic authentication failed: {e}")
        except self._anthropic.APIError as e:
            raise ProviderError(f"Anthropic API error: {e}")
        except Exception as e:
            error_str = str(e).lower()
            if "429" in str(e) or "rate" in error_str or "quota" in error_str:
                raise RateLimitError(f"Rate limit exceeded: {e}")
            raise ProviderError(f"Anthropic API error: {e}")

    # === Batch API ===

    def format_batch_request(
        self,
        unit_id: str,
        prompt: str,
        schema: dict | None = None
    ) -> dict:
        """
        Format a single unit into Anthropic batch request format.

        Args:
            unit_id: Unique identifier for this unit
            prompt: The prompt text
            schema: Optional JSON schema (included in prompt)

        Returns:
            Dict in Anthropic batch format
        """
        # Build message content
        message_content = prompt
        if schema:
            message_content = f"{prompt}\n\nRespond with valid JSON matching this schema: {json.dumps(schema)}"

        return {
            "custom_id": unit_id,
            "params": {
                "model": self.model,
                "max_tokens": self.max_tokens,
                "messages": [{"role": "user", "content": message_content}]
            }
        }

    def upload_batch_file(self, file_path: Path) -> str:
        """
        Upload a JSONL file for batch processing.

        NOTE: Anthropic's Message Batches API does NOT use server-side file uploads.
        This is a NO-OP that returns the file path as the "file_id".
        The actual file reading happens in create_batch().

        Args:
            file_path: Path to the JSONL file

        Returns:
            The file path as a string (used as file_id for create_batch)
        """
        # Verify file exists
        file_path = Path(file_path)
        if not file_path.exists():
            raise ProviderError(f"Batch file not found: {file_path}")
        return str(file_path)

    def create_batch(self, file_id: str) -> str:
        """
        Create a batch job from requests in a JSONL file.

        Anthropic's batch API accepts requests directly (not file uploads),
        so we read the JSONL file and pass the requests to the API.

        Args:
            file_id: Path to the JSONL file (from upload_batch_file)

        Returns:
            Batch job identifier

        Raises:
            RateLimitError: For rate limit errors
            ProviderError: For other errors
        """
        # Read and parse the JSONL file
        file_path = Path(file_id)
        if not file_path.exists():
            raise ProviderError(f"Batch file not found: {file_path}")

        requests = []
        with open(file_path) as f:
            for line_num, line in enumerate(f, 1):
                line = line.strip()
                if not line:
                    continue
                try:
                    request = json.loads(line)
                    requests.append(request)
                except json.JSONDecodeError as e:
                    raise ProviderError(f"Invalid JSON on line {line_num}: {e}")

        if not requests:
            raise ProviderError(f"No valid requests found in {file_path}")

        try:
            # Use the BETA namespace for Message Batches
            batch = self._client.beta.messages.batches.create(
                requests=requests
            )
            return batch.id
        except self._anthropic.RateLimitError as e:
            raise RateLimitError(f"Anthropic rate limit during batch creation: {e}")
        except self._anthropic.APIError as e:
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
            batch = self._client.beta.messages.batches.retrieve(batch_id)
        except self._anthropic.NotFoundError:
            raise ProviderError(f"Batch not found: {batch_id}")
        except self._anthropic.APIError as e:
            raise ProviderError(f"Failed to get batch status: {e}")
        except Exception as e:
            raise ProviderError(f"Failed to get batch status: {e}")

        # Normalize status
        anthropic_status = batch.processing_status or "unknown"

        # Map Anthropic status to our BatchStatus
        if anthropic_status == "in_progress":
            status = BatchStatus.RUNNING
        elif anthropic_status == "canceling":
            status = BatchStatus.CANCELLED
        elif anthropic_status == "ended":
            # Check request counts to determine if it was successful
            succeeded = batch.request_counts.succeeded if batch.request_counts else 0
            errored = batch.request_counts.errored if batch.request_counts else 0
            canceled = batch.request_counts.canceled if batch.request_counts else 0

            if errored > 0 and succeeded == 0:
                status = BatchStatus.FAILED
            elif canceled > 0 and succeeded == 0:
                status = BatchStatus.CANCELLED
            else:
                status = BatchStatus.COMPLETED
        else:
            status = BatchStatus.RUNNING

        # Calculate progress
        total = 0
        completed = 0
        if batch.request_counts:
            succeeded = batch.request_counts.succeeded or 0
            errored = batch.request_counts.errored or 0
            expired = batch.request_counts.expired or 0
            canceled = batch.request_counts.canceled or 0
            processing = batch.request_counts.processing or 0

            total = succeeded + errored + expired + canceled + processing
            completed = succeeded + errored + expired + canceled

        progress = f"{completed}/{total}" if total > 0 else None

        # Extract error info
        error = None
        if status == BatchStatus.FAILED:
            errored_count = batch.request_counts.errored if batch.request_counts else 0
            error = f"{errored_count} requests failed"

        # Extract timestamps
        created_at = None
        updated_at = None
        if batch.created_at:
            created_at = batch.created_at.isoformat() if hasattr(batch.created_at, 'isoformat') else str(batch.created_at)
        if batch.ended_at:
            updated_at = batch.ended_at.isoformat() if hasattr(batch.ended_at, 'isoformat') else str(batch.ended_at)

        return BatchStatusInfo(
            status=status,
            progress=progress,
            error=error,
            provider_status=anthropic_status,
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
        # Get batch info first
        try:
            batch = self._client.beta.messages.batches.retrieve(batch_id)
        except self._anthropic.NotFoundError:
            raise ProviderError(f"Batch not found: {batch_id}")
        except self._anthropic.APIError as e:
            raise ProviderError(f"Failed to get batch info: {e}")

        if batch.processing_status not in ("ended",):
            raise ProviderError(
                f"Batch not completed. Current status: {batch.processing_status}"
            )

        # Download results using the results iterator
        results: list[BatchResult] = []
        total_input_tokens = 0
        total_output_tokens = 0

        try:
            for result in self._client.beta.messages.batches.results(batch_id):
                batch_result, input_tokens, output_tokens = self._parse_batch_result(result)
                results.append(batch_result)
                total_input_tokens += input_tokens
                total_output_tokens += output_tokens
        except self._anthropic.APIError as e:
            raise ProviderError(f"Failed to download results: {e}")
        except Exception as e:
            raise ProviderError(f"Failed to download results: {e}")

        # Build metadata
        created_at = None
        completed_at = None
        if batch.created_at:
            created_at = batch.created_at.isoformat() if hasattr(batch.created_at, 'isoformat') else str(batch.created_at)
        if batch.ended_at:
            completed_at = batch.ended_at.isoformat() if hasattr(batch.ended_at, 'isoformat') else str(batch.ended_at)

        metadata = BatchMetadata(
            total_input_tokens=total_input_tokens,
            total_output_tokens=total_output_tokens,
            started_at=created_at,
            completed_at=completed_at,
            provider="anthropic",
            model=self.model
        )

        return results, metadata

    def _parse_batch_result(self, result: Any) -> tuple[BatchResult, int, int]:
        """
        Parse a single Anthropic batch result.

        Anthropic batch result structure:
        - result.custom_id: The unit ID
        - result.result.type: "succeeded" or "errored" or "expired" or "canceled"
        - result.result.message: The Message object (if succeeded)
        - result.result.error: The error info (if errored)

        Returns:
            Tuple of (BatchResult, input_tokens, output_tokens)
        """
        custom_id = result.custom_id or "unknown"
        input_tokens = 0
        output_tokens = 0
        content = None
        error = None

        result_obj = result.result

        # Check result type
        if result_obj.type == "succeeded":
            # Extract content from the message
            message = result_obj.message
            if message.content and len(message.content) > 0:
                first_block = message.content[0]
                if hasattr(first_block, 'text'):
                    content = first_block.text

            # Extract token usage
            if message.usage:
                input_tokens = message.usage.input_tokens or 0
                output_tokens = message.usage.output_tokens or 0

        elif result_obj.type == "errored":
            # Extract error info
            if hasattr(result_obj, 'error') and result_obj.error:
                error_type = getattr(result_obj.error, 'type', 'unknown')
                error_message = getattr(result_obj.error, 'message', 'Unknown error')
                error = f"{error_type}: {error_message}"
            else:
                error = "Unknown error"

        elif result_obj.type == "expired":
            error = "Request expired"

        elif result_obj.type == "canceled":
            error = "Request canceled"

        else:
            error = f"Unknown result type: {result_obj.type}"

        return BatchResult(
            unit_id=custom_id,
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
        try:
            batch = self._client.beta.messages.batches.retrieve(batch_id)

            # Check if already in a terminal state
            if batch.processing_status in ("ended",):
                return False

            # Cancel the batch
            self._client.beta.messages.batches.cancel(batch_id)
            return True

        except self._anthropic.NotFoundError:
            raise ProviderError(f"Batch not found: {batch_id}")
        except self._anthropic.APIError as e:
            error_str = str(e)
            if "already" in error_str.lower() or "ended" in error_str.lower():
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

    def _log_error(self, data: dict):
        """Log error to stderr in JSON format."""
        sys.stderr.write(json.dumps(data) + '\n')
