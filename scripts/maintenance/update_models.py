#!/usr/bin/env python3
"""
update_models.py - Auto-update models.yaml with current model lists and pricing.

Fetches model lists from OpenAI, Anthropic, and Gemini APIs, scrapes their pricing
pages, uses an LLM to extract structured pricing, and updates models.yaml.

Dependencies (not in main requirements.txt - this is a maintainer tool):
  - requests
  - beautifulsoup4
  - PyYAML
  - google-genai OR openai OR anthropic (for LLM calls)

Usage:
  python scripts/maintenance/update_models.py [OPTIONS]

Options:
  --dry-run       Show what would change without writing to models.yaml
  --provider NAME Only update one provider (openai, anthropic, gemini)
  --verbose       Show raw LLM responses for debugging
  --llm MODEL     Override which LLM to use for parsing (e.g., "gpt-4o-mini")
"""

import argparse
import json
import os
import re
import sys
from pathlib import Path
from typing import Any, Optional

import requests
import yaml
from bs4 import BeautifulSoup


# ============================================================================
# Configuration
# ============================================================================

# Repo root is two levels up from this script (scripts/maintenance/update_models.py)
REPO_ROOT = Path(__file__).parent.parent.parent
MODELS_YAML_PATH = REPO_ROOT / "scripts" / "providers" / "models.yaml"
PRICING_OVERRIDES_DIR = REPO_ROOT / "scripts" / "maintenance" / "pricing_overrides"

# Pricing page URLs (tried in order until one succeeds)
PRICING_URLS = {
    "openai": [
        "https://platform.openai.com/docs/pricing",
        "https://platform.openai.com/docs/models/compare",
        "https://openai.com/api/pricing/",  # often blocked, last resort
    ],
    "anthropic": [
        "https://www.anthropic.com/pricing",
        "https://docs.anthropic.com/en/docs/about-claude/pricing",
    ],
    "gemini": [
        "https://ai.google.dev/gemini-api/docs/pricing",
        "https://ai.google.dev/pricing",
    ],
}

# Batch discount multipliers (standard price × multiplier = batch price)
BATCH_DISCOUNT = {
    "openai": 0.5,
    "anthropic": 0.5,
    "gemini": 0.5,
}

# Model ID prefixes to keep for OpenAI (filter out embeddings, audio, images, etc.)
OPENAI_CHAT_PREFIXES = ("gpt-", "o1-", "o3-", "o4-", "chatgpt-")

# OpenAI models to exclude (audio, image, realtime - these pass prefix filter but aren't text LLMs)
OPENAI_EXCLUDE_PATTERNS = ("audio", "realtime", "image", "tts", "whisper", "dall-e")

# Gemini models to exclude (TTS, robotics, vision-only, embeddings, aliases, etc.)
# A model is excluded if any of these patterns appear in its ID (case-insensitive)
# Note: "latest" excludes alias models like gemini-flash-latest
GEMINI_EXCLUDE_PATTERNS = [
    "tts", "robotics", "computer-use", "image",
    "embedding", "aqa", "latest",
]

# Browser user agent for web requests (realistic Chrome UA)
USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"


# ============================================================================
# Phase 1: Fetch Model Lists from APIs
# ============================================================================

def fetch_openai_models(api_key: str) -> list[dict]:
    """Fetch model list from OpenAI API."""
    resp = requests.get(
        "https://api.openai.com/v1/models",
        headers={"Authorization": f"Bearer {api_key}"},
        timeout=30,
    )
    resp.raise_for_status()
    data = resp.json()

    models = []
    for m in data.get("data", []):
        model_id = m.get("id", "")
        # Filter to chat/completion models only (by prefix)
        if not any(model_id.startswith(prefix) for prefix in OPENAI_CHAT_PREFIXES):
            continue

        # Exclude audio, image, realtime models (these pass prefix but aren't text LLMs)
        model_id_lower = model_id.lower()
        if any(pattern in model_id_lower for pattern in OPENAI_EXCLUDE_PATTERNS):
            continue

        models.append({
            "id": model_id,
            "display_name": model_id,  # OpenAI doesn't provide display names
        })

    return models


def fetch_anthropic_models(api_key: str) -> list[dict]:
    """Fetch model list from Anthropic API with pagination."""
    models = []
    cursor = None

    while True:
        params = {}
        if cursor:
            params["after_id"] = cursor

        resp = requests.get(
            "https://api.anthropic.com/v1/models",
            headers={
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01",
            },
            params=params,
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()

        for m in data.get("data", []):
            models.append({
                "id": m.get("id", ""),
                "display_name": m.get("display_name", m.get("id", "")),
            })

        if not data.get("has_more"):
            break
        cursor = data.get("last_id")

    return models


def fetch_gemini_models(api_key: str) -> list[dict]:
    """Fetch model list from Gemini API."""
    resp = requests.get(
        "https://generativelanguage.googleapis.com/v1beta/models",
        params={"key": api_key},
        timeout=30,
    )
    resp.raise_for_status()
    data = resp.json()

    models = []
    for m in data.get("models", []):
        # Filter to models that support generateContent
        methods = m.get("supportedGenerationMethods", [])
        if "generateContent" not in methods:
            continue

        # Strip "models/" prefix
        model_id = m.get("name", "").replace("models/", "")
        if not model_id:
            continue

        # Only include models with "gemini-" prefix (excludes nano-banana, deep-research, etc.)
        if not model_id.lower().startswith("gemini-"):
            continue

        # Exclude TTS, robotics, embeddings, alias models, and other non-text models
        model_id_lower = model_id.lower()
        if any(pattern in model_id_lower for pattern in GEMINI_EXCLUDE_PATTERNS):
            continue

        models.append({
            "id": model_id,
            "display_name": m.get("displayName", model_id),
        })

    return models


def fetch_all_models(verbose: bool = False) -> dict[str, list[dict]]:
    """Fetch models from all available providers."""
    results = {}

    # OpenAI
    openai_key = os.environ.get("OPENAI_API_KEY")
    if openai_key:
        try:
            models = fetch_openai_models(openai_key)
            results["openai"] = models
            if verbose:
                print(f"  OpenAI: fetched {len(models)} chat models")
        except Exception as e:
            print(f"  WARNING: Failed to fetch OpenAI models: {e}")
    else:
        print("  WARNING: OPENAI_API_KEY not set, skipping OpenAI")

    # Anthropic
    anthropic_key = os.environ.get("ANTHROPIC_API_KEY")
    if anthropic_key:
        try:
            models = fetch_anthropic_models(anthropic_key)
            results["anthropic"] = models
            if verbose:
                print(f"  Anthropic: fetched {len(models)} models")
        except Exception as e:
            print(f"  WARNING: Failed to fetch Anthropic models: {e}")
    else:
        print("  WARNING: ANTHROPIC_API_KEY not set, skipping Anthropic")

    # Gemini
    gemini_key = os.environ.get("GOOGLE_API_KEY")
    if gemini_key:
        try:
            models = fetch_gemini_models(gemini_key)
            results["gemini"] = models
            if verbose:
                print(f"  Gemini: fetched {len(models)} models")
        except Exception as e:
            print(f"  WARNING: Failed to fetch Gemini models: {e}")
    else:
        print("  WARNING: GOOGLE_API_KEY not set, skipping Gemini")

    return results


# ============================================================================
# Phase 2: Fetch Pricing Pages
# ============================================================================

def fetch_pricing_page(urls: list[str]) -> Optional[str]:
    """Fetch and extract text from a pricing page. Try URLs in order."""
    headers = {"User-Agent": USER_AGENT}

    for url in urls:
        try:
            resp = requests.get(url, headers=headers, timeout=30)
            resp.raise_for_status()

            soup = BeautifulSoup(resp.text, "html.parser")

            # Remove script and style elements
            for element in soup(["script", "style", "nav", "footer", "header"]):
                element.decompose()

            # Get text and clean up whitespace
            text = soup.get_text(separator="\n")
            lines = [line.strip() for line in text.splitlines() if line.strip()]
            return "\n".join(lines)

        except Exception as e:
            print(f"    Failed to fetch {url}: {e}")
            continue

    return None


def fetch_all_pricing_pages(providers: list[str], verbose: bool = False) -> dict[str, str]:
    """Fetch pricing pages for specified providers.

    Tries web fetch first, then falls back to local override files.
    """
    results = {}

    for provider in providers:
        urls = PRICING_URLS.get(provider, [])
        if not urls:
            continue

        if verbose:
            print(f"  Fetching {provider} pricing page...")

        text = fetch_pricing_page(urls)
        if text:
            results[provider] = text
            if verbose:
                print(f"    Got {len(text)} chars of text from web")
        else:
            # Try local override file
            override_file = PRICING_OVERRIDES_DIR / f"{provider}_pricing.txt"
            if override_file.exists():
                try:
                    text = override_file.read_text()
                    results[provider] = text
                    print(f"  Using local override: {override_file}")
                    if verbose:
                        print(f"    Got {len(text)} chars of text from file")
                except Exception as e:
                    print(f"  WARNING: Could not read override file: {e}")
            else:
                print(f"  WARNING: Could not fetch pricing page for {provider}")
                print(f"  TIP: {provider} likely uses Cloudflare protection that blocks automated fetches.")
                print(f"       To work around this: open the pricing page in your browser, Select All (Ctrl+A / Cmd+A),")
                print(f"       Copy (Ctrl+C), and paste into {override_file}")

    return results


# ============================================================================
# Phase 3: LLM Pricing Extraction
# ============================================================================

EXTRACTION_SYSTEM_PROMPT = """You are a data extraction assistant. You will be given a list of API model IDs
and the text content of a pricing page. Your job is to match each model ID to
its pricing information from the page.

Return ONLY a JSON object with this exact structure, no other text:
{
  "models": {
    "model-id-here": {
      "display_name": "Human Readable Name",
      "input_per_million": 2.50,
      "output_per_million": 10.00
    }
  }
}

Rules:
- You are a neutral data extraction assistant. Do not use your internal
  knowledge to validate or override prices. Only extract what is explicitly
  written in the provided pricing page text.
- Prices should be in USD per million tokens
- These are the STANDARD (non-cached, non-batch) prices from the page
- If a model ID has a date suffix (e.g., gpt-4o-2024-08-06) but the pricing
  page only lists the base name (e.g., GPT-4o), map it to the base name's price
- If you cannot find pricing for a model, omit it from the output
- Do NOT invent prices — only include models with clear pricing on the page
- For models with tiered pricing (e.g., different rates for >128K context),
  use the standard/lower-tier price"""


def get_available_llm() -> tuple[str, str]:
    """Determine which LLM to use based on available API keys.

    Returns: (provider, model_id)
    """
    # Prefer Gemini (cheapest)
    if os.environ.get("GOOGLE_API_KEY"):
        return ("gemini", "gemini-2.0-flash-001")

    # Then OpenAI
    if os.environ.get("OPENAI_API_KEY"):
        return ("openai", "gpt-4o-mini")

    # Then Anthropic
    if os.environ.get("ANTHROPIC_API_KEY"):
        return ("anthropic", "claude-3-5-haiku-20241022")

    raise RuntimeError("No LLM API key available (need GOOGLE_API_KEY, OPENAI_API_KEY, or ANTHROPIC_API_KEY)")


def call_llm_gemini(model: str, system: str, user: str) -> str:
    """Call Gemini API for LLM extraction."""
    try:
        from google import genai
        from google.genai import types
    except ImportError:
        raise ImportError("google-genai package required for Gemini LLM calls")

    api_key = os.environ.get("GOOGLE_API_KEY")
    client = genai.Client(api_key=api_key)

    response = client.models.generate_content(
        model=model,
        contents=user,
        config=types.GenerateContentConfig(
            system_instruction=system,
            temperature=0.0,
        ),
    )
    return response.text


def call_llm_openai(model: str, system: str, user: str) -> str:
    """Call OpenAI API for LLM extraction."""
    try:
        from openai import OpenAI
    except ImportError:
        raise ImportError("openai package required for OpenAI LLM calls")

    client = OpenAI()
    response = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        temperature=0.0,
    )
    return response.choices[0].message.content


def call_llm_anthropic(model: str, system: str, user: str) -> str:
    """Call Anthropic API for LLM extraction."""
    try:
        import anthropic
    except ImportError:
        raise ImportError("anthropic package required for Anthropic LLM calls")

    client = anthropic.Anthropic()
    response = client.messages.create(
        model=model,
        max_tokens=4096,
        system=system,
        messages=[
            {"role": "user", "content": user},
        ],
    )
    return response.content[0].text


def call_llm(provider: str, model: str, system: str, user: str) -> str:
    """Call the appropriate LLM API."""
    if provider == "gemini":
        return call_llm_gemini(model, system, user)
    elif provider == "openai":
        return call_llm_openai(model, system, user)
    elif provider == "anthropic":
        return call_llm_anthropic(model, system, user)
    else:
        raise ValueError(f"Unknown LLM provider: {provider}")


def parse_llm_response(response: str) -> dict:
    """Parse LLM response, handling markdown code fences."""
    text = response.strip()

    # Strip markdown code fences
    if text.startswith("```"):
        lines = text.split("\n")
        # Remove first line (```json or ```)
        lines = lines[1:]
        # Remove last line if it's just ```
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines)

    return json.loads(text)


def extract_pricing_with_llm(
    provider_name: str,
    model_ids: list[str],
    pricing_text: str,
    llm_provider: str,
    llm_model: str,
    verbose: bool = False,
) -> dict[str, dict]:
    """Use LLM to extract pricing for models from pricing page text."""
    user_prompt = f"""Provider: {provider_name}

API Model IDs:
{chr(10).join(model_ids)}

Pricing Page Content:
{pricing_text[:50000]}"""  # Limit to avoid context overflow

    for attempt in range(2):  # Retry once on parse failure
        try:
            response = call_llm(llm_provider, llm_model, EXTRACTION_SYSTEM_PROMPT, user_prompt)

            if verbose:
                print(f"\n--- Raw LLM response for {provider_name} ---")
                print(response[:2000])
                print("--- End response ---\n")

            data = parse_llm_response(response)
            return data.get("models", {})

        except json.JSONDecodeError as e:
            if attempt == 0:
                print(f"    WARNING: Failed to parse LLM response (attempt {attempt + 1}), retrying...")
            else:
                print(f"    ERROR: Failed to parse LLM response after retry: {e}")
                return {}
        except Exception as e:
            print(f"    ERROR: LLM call failed: {e}")
            return {}

    return {}


# ============================================================================
# Phase 4: Merge into models.yaml
# ============================================================================

def load_models_yaml() -> dict:
    """Load the existing models.yaml file."""
    with open(MODELS_YAML_PATH) as f:
        return yaml.safe_load(f)


def save_models_yaml(data: dict) -> None:
    """Save the updated models.yaml file."""
    # Custom representer to preserve formatting
    def str_representer(dumper, data):
        if "\n" in data:
            return dumper.represent_scalar("tag:yaml.org,2002:str", data, style="|")
        return dumper.represent_scalar("tag:yaml.org,2002:str", data)

    yaml.add_representer(str, str_representer)

    with open(MODELS_YAML_PATH, "w") as f:
        yaml.dump(data, f, default_flow_style=False, sort_keys=False, allow_unicode=True)


def merge_pricing(
    yaml_data: dict,
    provider_name: str,
    api_models: list[dict],
    llm_pricing: dict[str, dict],
    verbose: bool = False,
) -> dict[str, list[str]]:
    """Merge LLM-extracted pricing into YAML data.

    Returns dict with keys: updated, added, warnings, no_price
    """
    results = {
        "updated": [],
        "added": [],
        "warnings": [],
        "no_price": [],
    }

    provider_data = yaml_data.get("providers", {}).get(provider_name, {})
    existing_models = provider_data.get("models", {})
    batch_discount = BATCH_DISCOUNT.get(provider_name, 0.5)

    # Get set of API model IDs
    api_model_ids = {m["id"] for m in api_models}
    api_model_names = {m["id"]: m.get("display_name", m["id"]) for m in api_models}

    # Process LLM pricing results
    for model_id, pricing in llm_pricing.items():
        if model_id not in api_model_ids:
            # LLM returned a model not in the API list — skip
            continue

        # Apply batch discount
        input_price = pricing.get("input_per_million", 0) * batch_discount
        output_price = pricing.get("output_per_million", 0) * batch_discount
        display_name = pricing.get("display_name") or api_model_names.get(model_id, model_id)

        if model_id in existing_models:
            # Update existing model
            old = existing_models[model_id]
            old_input = old.get("input_per_million", 0)
            old_output = old.get("output_per_million", 0)

            if abs(old_input - input_price) > 0.001 or abs(old_output - output_price) > 0.001:
                existing_models[model_id]["input_per_million"] = round(input_price, 4)
                existing_models[model_id]["output_per_million"] = round(output_price, 4)
                if display_name:
                    existing_models[model_id]["display_name"] = display_name
                results["updated"].append(
                    f"{model_id}: input ${old_input:.2f}→${input_price:.2f}, "
                    f"output ${old_output:.2f}→${output_price:.2f}"
                )
        else:
            # Add new model
            existing_models[model_id] = {
                "display_name": display_name,
                "input_per_million": round(input_price, 4),
                "output_per_million": round(output_price, 4),
                "batch_support": True,
            }
            results["added"].append(
                f"{model_id}: input ${input_price:.2f}, output ${output_price:.2f}"
            )

    # Check for models in YAML but not in API
    for model_id in existing_models:
        if model_id not in api_model_ids:
            results["warnings"].append(f"{model_id} (in YAML but not in current API model list)")

    # Check for API models without pricing
    for model_id in api_model_ids:
        if model_id not in llm_pricing and model_id not in existing_models:
            results["no_price"].append(model_id)

    # Update the YAML data
    if "providers" not in yaml_data:
        yaml_data["providers"] = {}
    if provider_name not in yaml_data["providers"]:
        yaml_data["providers"][provider_name] = {}
    yaml_data["providers"][provider_name]["models"] = existing_models

    return results


# ============================================================================
# Main
# ============================================================================

def main():
    parser = argparse.ArgumentParser(
        description="Auto-update models.yaml with current model lists and pricing."
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would change without writing to models.yaml",
    )
    parser.add_argument(
        "--provider",
        choices=["openai", "anthropic", "gemini"],
        help="Only update one provider",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Show raw LLM responses for debugging",
    )
    parser.add_argument(
        "--llm",
        help="Override which LLM to use for parsing (e.g., 'gpt-4o-mini')",
    )
    args = parser.parse_args()

    # Load .env file so API keys don't need to be exported in the shell
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except ImportError:
        pass  # python-dotenv not installed; rely on environment variables

    print("=== Octobatch Model Registry Update ===\n")

    # Determine which LLM to use
    try:
        llm_provider, llm_model = get_available_llm()
        if args.llm:
            # Override model but keep same provider
            llm_model = args.llm
        print(f"Using LLM: {llm_provider}/{llm_model}\n")
    except RuntimeError as e:
        print(f"ERROR: {e}")
        sys.exit(1)

    # Phase 1: Fetch model lists
    print("Phase 1: Fetching model lists from APIs...")
    all_models = fetch_all_models(args.verbose)

    if not all_models:
        print("ERROR: No models fetched from any provider")
        sys.exit(1)

    # Filter to requested provider if specified
    if args.provider:
        if args.provider not in all_models:
            print(f"ERROR: No models fetched for provider '{args.provider}'")
            sys.exit(1)
        all_models = {args.provider: all_models[args.provider]}

    print()

    # Phase 2: Fetch pricing pages
    print("Phase 2: Fetching pricing pages...")
    pricing_pages = fetch_all_pricing_pages(list(all_models.keys()), args.verbose)
    print()

    # Phase 3: Extract pricing with LLM
    print("Phase 3: Extracting pricing with LLM...")
    llm_results = {}
    for provider_name, models in all_models.items():
        if provider_name not in pricing_pages:
            print(f"  Skipping {provider_name} (no pricing page)")
            continue

        print(f"  Processing {provider_name}...")
        model_ids = [m["id"] for m in models]
        pricing = extract_pricing_with_llm(
            provider_name,
            model_ids,
            pricing_pages[provider_name],
            llm_provider,
            llm_model,
            args.verbose,
        )
        llm_results[provider_name] = pricing
        print(f"    Extracted pricing for {len(pricing)} models")

    print()

    # Phase 4: Merge into models.yaml
    print("Phase 4: Merging into models.yaml...")
    yaml_data = load_models_yaml()

    total_updated = 0
    total_added = 0
    total_warnings = 0

    for provider_name in all_models:
        print(f"\n--- {provider_name.upper()} ---")

        api_models = all_models[provider_name]
        llm_pricing = llm_results.get(provider_name, {})

        print(f"API models found: {len(api_models)}")
        print(f"Pricing matched: {len(llm_pricing)}")

        if not llm_pricing:
            print("  No pricing extracted, skipping")
            continue

        results = merge_pricing(yaml_data, provider_name, api_models, llm_pricing, args.verbose)

        for item in results["updated"]:
            print(f"  UPDATED  {item}")
            total_updated += 1

        for item in results["added"]:
            print(f"  NEW      {item}")
            total_added += 1

        for item in results["warnings"]:
            print(f"  WARNING  {item}")
            total_warnings += 1

        for item in results["no_price"][:5]:  # Limit output
            print(f"  NO PRICE {item}")
        if len(results["no_price"]) > 5:
            print(f"  ... and {len(results['no_price']) - 5} more without pricing")

    # Summary
    print(f"\n=== Summary ===")
    print(f"Updated: {total_updated} models")
    print(f"Added: {total_added} models")
    print(f"Warnings: {total_warnings} models")

    if total_updated == 0 and total_added == 0:
        print("\nmodels.yaml is up to date")
        return

    if args.dry_run:
        print(f"\n--dry-run specified, models.yaml NOT modified")
    else:
        save_models_yaml(yaml_data)
        print(f"\nmodels.yaml saved to {MODELS_YAML_PATH}")


if __name__ == "__main__":
    main()
