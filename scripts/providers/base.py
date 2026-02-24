"""
base.py - Abstract base class for LLM providers.

Defines a unified interface for LLM providers that supports both batch and realtime APIs.
The orchestrator uses this interface rather than provider-specific APIs.

All providers must implement:
- Realtime API: generate_realtime() for synchronous single-request calls
- Batch API: format_batch_request(), upload_batch_file(), create_batch(),
             get_batch_status(), download_batch_results(), cancel_batch()
- Pricing: estimate_cost() for cost estimation
"""

from abc import ABC, abstractmethod
from enum import Enum
from pathlib import Path
from typing import Any, TypedDict, NotRequired

import yaml


class BatchStatus(Enum):
    """Status of a batch job."""
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class BatchStatusInfo(TypedDict):
    """Detailed status information for a batch job."""
    status: BatchStatus
    progress: str | None  # e.g., "45/100"
    error: str | None
    provider_status: NotRequired[str]  # Raw provider status code
    created_at: NotRequired[str | None]
    updated_at: NotRequired[str | None]


class BatchResult(TypedDict):
    """Result from a batch operation."""
    unit_id: str
    content: str | None
    input_tokens: int
    output_tokens: int
    error: str | None


class BatchMetadata(TypedDict, total=False):
    """Batch-level metadata summary."""
    total_input_tokens: int
    total_output_tokens: int
    started_at: str | None
    completed_at: str | None
    provider: str
    model: str


class RealtimeResult(TypedDict):
    """Result from a realtime API call."""
    content: str
    input_tokens: int
    output_tokens: int
    finish_reason: str


class LLMProvider(ABC):
    """
    Abstract base class for LLM providers (batch and realtime).

    Provides a unified interface for interacting with different LLM providers
    (Gemini, OpenAI, Anthropic) through both batch and realtime APIs.

    Usage:
        from providers import get_provider

        provider = get_provider(config)

        # Realtime (synchronous)
        result = provider.generate_realtime("What is 2+2?")

        # Batch
        batch_id = provider.create_batch(file_id)
        status = provider.get_batch_status(batch_id)
    """

    def __init__(self, config: dict):
        """
        Initialize the provider.

        Args:
            config: Configuration dict with api settings
        """
        self.config = config
        api_config = config.get('api', {})
        self.model = api_config.get('model')

    # === Realtime API ===

    @abstractmethod
    def generate_realtime(
        self,
        prompt: str,
        schema: dict | None = None
    ) -> RealtimeResult:
        """
        Make a single synchronous API request.

        Args:
            prompt: The prompt text to send
            schema: Optional JSON schema for structured output

        Returns:
            RealtimeResult with content, token counts, and finish reason

        Raises:
            ProviderError: If the API call fails
        """
        pass

    # === Batch API ===

    @abstractmethod
    def format_batch_request(
        self,
        unit_id: str,
        prompt: str,
        schema: dict | None = None
    ) -> dict:
        """
        Format a single unit into provider-specific JSONL line format.

        This method converts our standard prompt format into the format
        expected by the provider's batch API.

        Args:
            unit_id: Unique identifier for this unit
            prompt: The prompt text
            schema: Optional JSON schema for structured output

        Returns:
            Dict ready to be JSON-serialized as a JSONL line
        """
        pass

    @abstractmethod
    def upload_batch_file(self, file_path: Path) -> str:
        """
        Upload a JSONL file for batch processing.

        Args:
            file_path: Path to the JSONL file with batch requests

        Returns:
            file_id: Provider-specific file identifier

        Raises:
            ProviderError: If upload fails
        """
        pass

    @abstractmethod
    def create_batch(self, file_id: str) -> str:
        """
        Create a batch job from an uploaded file.

        Args:
            file_id: The file identifier from upload_batch_file()

        Returns:
            batch_id: Provider-specific batch job identifier

        Raises:
            ProviderError: If batch creation fails
        """
        pass

    @abstractmethod
    def get_batch_status(self, batch_id: str) -> BatchStatusInfo:
        """
        Check the status of a batch job.

        Args:
            batch_id: The batch identifier from create_batch()

        Returns:
            BatchStatusInfo with status, progress, and metadata

        Raises:
            ProviderError: If status check fails
        """
        pass

    @abstractmethod
    def download_batch_results(self, batch_id: str) -> tuple[list[BatchResult], BatchMetadata]:
        """
        Download and parse results from a completed batch.

        Args:
            batch_id: The batch identifier from create_batch()

        Returns:
            Tuple of (list of BatchResult, BatchMetadata)

        Raises:
            ProviderError: If download or parsing fails
        """
        pass

    @abstractmethod
    def cancel_batch(self, batch_id: str) -> bool:
        """
        Cancel a running batch job.

        Args:
            batch_id: The batch identifier from create_batch()

        Returns:
            True if cancelled successfully, False if already completed/failed

        Raises:
            ProviderError: If cancellation fails
        """
        pass

    # === Pricing ===

    @abstractmethod
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
            is_batch: True for batch API pricing, False for realtime

        Returns:
            Estimated cost in USD
        """
        pass

    # === Helper Methods ===

    def get_api_key_env_var(self) -> str:
        """
        Get the environment variable name for this provider's API key.

        Returns:
            Environment variable name (e.g., "GOOGLE_API_KEY")
        """
        return "API_KEY"  # Override in subclasses

    # === Model Registry Methods ===

    @staticmethod
    def load_model_registry() -> dict:
        """Load the centralized model registry from models.yaml."""
        registry_path = Path(__file__).parent / "models.yaml"
        with open(registry_path) as f:
            return yaml.safe_load(f)

    @staticmethod
    def get_provider_models(provider_name: str) -> dict:
        """Get the models dict for a given provider from the registry."""
        registry = LLMProvider.load_model_registry()
        provider_data = registry.get("providers", {}).get(provider_name, {})
        return provider_data.get("models", {})

    @staticmethod
    def get_provider_info(provider_name: str) -> dict:
        """Get provider-level info (env_var, sdk, default_model, realtime_multiplier)."""
        registry = LLMProvider.load_model_registry()
        return registry.get("providers", {}).get(provider_name, {})

    @staticmethod
    def get_all_providers() -> dict:
        """Get the full providers section of the registry."""
        registry = LLMProvider.load_model_registry()
        return registry.get("providers", {})

    @staticmethod
    def get_default_pricing() -> dict:
        """Get default pricing for unknown models."""
        registry = LLMProvider.load_model_registry()
        return registry.get("defaults", {"input_per_million": 1.00, "output_per_million": 2.00, "realtime_multiplier": 2.0})


class ProviderError(Exception):
    """Base exception for provider errors."""
    pass


class RateLimitError(ProviderError):
    """Rate limit (429) error - should be retried."""
    pass


class AuthenticationError(ProviderError):
    """Authentication/authorization error."""
    pass
