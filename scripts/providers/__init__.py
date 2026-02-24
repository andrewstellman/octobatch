"""
providers - Unified LLM provider interface for batch and realtime APIs.

Provides a single interface for submitting prompts, polling status, and collecting
results from different LLM providers (Gemini, OpenAI, Anthropic).

Usage:
    from providers import get_provider

    provider = get_provider(config)

    # Realtime (synchronous)
    result = provider.generate_realtime("What is 2+2?")
    print(result["content"])

    # Batch
    batch_id = provider.create_batch(file_id)
    status = provider.get_batch_status(batch_id)
    if status["status"] == BatchStatus.COMPLETED:
        results, metadata = provider.download_batch_results(batch_id)
"""

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


def get_provider(config: dict) -> LLMProvider:
    """
    Factory function to get the appropriate provider based on config.

    Uses deferred imports so missing SDKs don't crash the framework.
    Each provider validates credentials in its __init__.

    Provider resolution order:
        1. config['api']['provider']
        2. Registry default_provider from models.yaml

    Args:
        config: Configuration dict (api.provider is optional if registry has default)

    Returns:
        LLMProvider instance for the specified provider

    Raises:
        ValueError: If provider is unknown or not specified anywhere
        ImportError: If provider SDK is not installed
    """
    api_config = config.get("api", {})
    provider_name = api_config.get("provider")

    if not provider_name:
        # Fallback to registry's default_provider
        try:
            registry = LLMProvider.load_model_registry()
            provider_name = registry.get("default_provider")
        except Exception:
            pass

    if not provider_name:
        raise ValueError(
            "No API provider specified in config and no default_provider in registry. "
            "Set api.provider to 'gemini', 'openai', or 'anthropic'."
        )

    provider_name = provider_name.lower()

    if provider_name == "gemini":
        from .gemini import GeminiProvider
        return GeminiProvider(config)

    elif provider_name == "openai":
        from .openai import OpenAIProvider
        return OpenAIProvider(config)

    elif provider_name == "anthropic":
        from .anthropic import AnthropicProvider
        return AnthropicProvider(config)

    else:
        raise ValueError(
            f"Unknown provider: '{provider_name}'. "
            f"Supported providers: gemini, openai, anthropic"
        )


def get_step_provider(config: dict, step_name: str, manifest: dict | None = None) -> LLMProvider:
    """
    Get a provider instance with per-step overrides applied.

    Resolution order: CLI flags > Step config > Global config.
    CLI flags are detected via manifest metadata (cli_provider_override / cli_model_override)
    set during init_run(). If CLI flags were used, step-level overrides are skipped.

    Args:
        config: Configuration dict (snapshotted, with CLI overrides already applied)
        step_name: Pipeline step name to look up overrides for
        manifest: Optional manifest dict; used to detect CLI override flags

    Returns:
        LLMProvider instance with the correct provider/model for this step
    """
    import copy

    # Look up step config
    step_provider = None
    step_model = None
    steps = config.get("pipeline", {}).get("steps", [])
    for s in steps:
        if s.get("name") == step_name:
            step_provider = s.get("provider")
            step_model = s.get("model")
            break

    # Check if CLI overrides were used (these take priority over step config)
    cli_provider = False
    cli_model = False
    if manifest:
        metadata = manifest.get("metadata", {})
        cli_provider = metadata.get("cli_provider_override", False)
        cli_model = metadata.get("cli_model_override", False)

    # If no step-level overrides, or CLI overrides both, use global config as-is
    if (not step_provider or cli_provider) and (not step_model or cli_model):
        return get_provider(config)

    # Build effective config with step-level overrides
    effective_config = copy.deepcopy(config)
    if "api" not in effective_config:
        effective_config["api"] = {}

    if step_provider and not cli_provider:
        effective_config["api"]["provider"] = step_provider
    if step_model and not cli_model:
        effective_config["api"]["model"] = step_model

    return get_provider(effective_config)


__all__ = [
    "LLMProvider",
    "BatchStatus",
    "BatchStatusInfo",
    "BatchResult",
    "BatchMetadata",
    "RealtimeResult",
    "ProviderError",
    "RateLimitError",
    "AuthenticationError",
    "get_provider",
    "get_step_provider",
]
