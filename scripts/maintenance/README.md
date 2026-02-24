# Octobatch Maintenance Scripts

This directory contains maintenance utilities for Octobatch. These scripts are meant to be run manually by maintainers, not as part of normal Octobatch operation.

## update_models.py

Auto-updates `scripts/providers/models.yaml` with current model lists and pricing from OpenAI, Anthropic, and Gemini.

### How It Works

1. **Fetches model lists** from each provider's API
2. **Scrapes pricing pages** to get current pricing information
3. **Uses an LLM** to extract structured pricing from the page text
4. **Merges changes** into models.yaml, preserving existing structure

### Dependencies

These packages are required but NOT in the main requirements.txt:

```bash
pip install requests beautifulsoup4 PyYAML
pip install google-genai  # or: pip install openai anthropic
```

### Environment Variables

Set at least one provider API key:

- `OPENAI_API_KEY` - for fetching OpenAI models and optionally for LLM calls
- `ANTHROPIC_API_KEY` - for fetching Anthropic models and optionally for LLM calls
- `GOOGLE_API_KEY` - for fetching Gemini models and optionally for LLM calls

The script will use whichever LLM is available (preferring Gemini for cost).

### Usage

```bash
# Preview changes without modifying models.yaml
python scripts/maintenance/update_models.py --dry-run

# Update all providers
python scripts/maintenance/update_models.py

# Update only one provider
python scripts/maintenance/update_models.py --provider openai

# Show verbose output including raw LLM responses
python scripts/maintenance/update_models.py --verbose

# Use a specific LLM model for extraction
python scripts/maintenance/update_models.py --llm gpt-4o
```

### Output

The script prints a summary of changes:

```
=== Octobatch Model Registry Update ===

--- OPENAI ---
API models found: 12
Pricing matched: 8
  UPDATED  gpt-4o: input $1.25→$1.00, output $5.00→$4.00
  NEW      gpt-4.5-preview: input $1.875, output $7.50
  WARNING  gpt-4-turbo (in YAML but not in current API model list)

=== Summary ===
Updated: 1 models
Added: 1 models
Warnings: 1 models
models.yaml saved to scripts/providers/models.yaml
```

### Notes

- **Batch pricing**: The script converts standard/realtime prices to batch prices (50% discount for all providers)
- **Safe updates**: The script never removes models from models.yaml — it only warns about models that are no longer in the API
- **Idempotent**: Running the script twice with no pricing changes will report "models.yaml is up to date"
