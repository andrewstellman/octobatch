# NPC Dialog Walkthrough

[← Back to README](../../README.md)

An RPG dialog generation pipeline that demonstrates the cross_product strategy and the LLM-as-validator pattern. Five NPC personalities are combined with four player moods and three conversation topics to produce 60 unique dialog exchanges, each scored for personality consistency and mood responsiveness.

## Concept

You're building a dialog system for a fantasy RPG. Each NPC has a distinct personality — a Gruff Blacksmith speaks differently than a Nervous Merchant. When the player approaches with different moods (friendly, aggressive, desperate, suspicious) and different topics (directions, bartering, quest info), the NPC's response should adapt.

The pipeline generates all 60 combinations and then uses a second LLM step as a quality gate: dialogs that don't match the NPC's personality or don't respond to the player's mood get automatically retried.

## The Prompt

This prompt was used to generate the pipeline with Claude Code:

```
Read pipelines/TOOLKIT.md to understand Octobatch pipeline creation.

Create an NPC Dialog generation pipeline for an RPG game.

Concept: Generate dialog lines for non-player characters by combining different NPC
personalities with player interaction contexts. Every combination of personality,
mood, and topic produces a unique dialog exchange. A second step validates that the
dialog is consistent with the NPC's personality and responsive to the player's mood.

Data structure (items.yaml with three top-level keys):
- personalities (5 NPCs, each with id, name, description):
  Gruff Blacksmith, Wise Elder, Nervous Merchant, Cheerful Innkeeper,
  Mysterious Stranger
- moods (4 player moods, each with id, name, description):
  Friendly, Aggressive, Desperate, Suspicious
- topics (3 conversation topics, each with id, name, description):
  Asking for Directions, Bartering for Items, Seeking Quest Information

Pipeline steps:
1. Generate Dialog (LLM, name: generate_dialog) — Generate the dialog exchange for the
   given NPC, mood, and topic combination (from cross_product positions named "npc",
   "mood", "topic"). The output should include the NPC's greeting, a hint about how
   the player might respond, the overall tone (validated as an enum — one of: warm,
   cold, nervous, hostile, mysterious), and the full dialog exchange with NPC:/Player:
   prefixes.
2. Score Consistency (LLM, name: score_consistency) — Review the dialog and score two
   dimensions from 0.0 to 1.0: how well the dialog matches the NPC's personality, and
   how well it responds to the player's mood. Include personality-specific scoring
   criteria (e.g., Gruff Blacksmith should be blunt, Wise Elder should use proverbs).
   Echo back the NPC name, mood name, topic name, and tone for post-processing.
   Explain the reasoning for each score. Add validation rules that fail and retry
   any dialog where either score is below 0.6.

Processing: Use the cross-product strategy to generate all 60 combinations. The three
groups are NPC personalities, player moods, and conversation topics. Allow up to 3
validation retries.

After the pipeline completes, automatically generate reports showing:
- Tone distribution grouped by NPC name
- Personality consistency scores grouped by NPC name
- Mood responsiveness scores grouped by NPC name
- Show which NPC/mood combinations needed the most retries

Create in pipelines/NPCDialog/
```

## Pipeline Structure

```
+--------------------+    +------------------------+
| generate_dialog    |--->| score_consistency      |
| LLM step           |    | LLM step               |
|                    |    | validation gate:       |
|                    |    |   personality >= 0.6   |
|                    |    |   mood >= 0.6          |
+--------------------+    +------------------------+
```

**Step 1: generate_dialog** — Generates the dialog exchange. Outputs the NPC's greeting, a hint about how the player might respond, the overall tone (validated as an enum: warm/cold/nervous/hostile/mysterious), and the full dialog text.

**Step 2: score_consistency** — Scores the dialog on personality consistency and mood responsiveness, each from 0.0 to 1.0. Expression rules enforce minimum thresholds. Failed dialogs trigger automatic retries of the entire pipeline for that unit.

## Key Pattern: Cross Product

This pipeline demonstrates the `cross_product` strategy. Instead of listing all 60 combinations manually, you define three groups in `items.yaml` — 5 personalities, 4 moods, 3 topics — and Octobatch generates every combination automatically. Each unit receives three objects (`npc`, `mood`, `topic`) accessible in templates as `{{ npc.name }}`, `{{ mood.description }}`, etc.

The config maps each group to a position:

```yaml
processing:
  strategy: cross_product
  positions:
    - name: npc
      source_key: personalities
    - name: mood
      source_key: moods
    - name: topic
      source_key: topics
```

This produces 5 × 4 × 3 = 60 units with no manual enumeration.

## Quality Gate

The scoring step uses two expression rules as a quality gate:

```yaml
validation:
  score_consistency:
    rules:
      - name: personality_threshold
        expr: "personality_consistency >= 0.6"
        error: "Personality consistency {personality_consistency} is below threshold 0.6"
        level: error
      - name: mood_threshold
        expr: "mood_responsiveness >= 0.6"
        error: "Mood responsiveness {mood_responsiveness} is below threshold 0.6"
        level: error
```

When the scoring LLM gives a dialog a low score, the unit fails validation and Octobatch retries the entire pipeline for that unit — generating a new dialog and scoring it again.

## Expected Results

The Mysterious Stranger should almost always produce "mysterious" tones. The Cheerful Innkeeper tends toward "warm". The Gruff Blacksmith produces "cold" or "hostile" depending on the player's mood. Most dialogs score above 0.80 for personality consistency and 0.75 for mood responsiveness.

The hardest combinations tend to be Mysterious Stranger + Aggressive (balancing cryptic speech with responding to threats), Wise Elder + Desperate (measured personality can seem unresponsive to urgency), and Nervous Merchant + Suspicious (both parties anxious, making distinct voices difficult).

Post-processing generates tone distribution tables, consistency scores by NPC, retry analysis, and a CSV for further analysis.

See `pipelines/NPCDialog/config.yaml` for the full configuration, `items.yaml` for personality/mood/topic definitions, and the `templates/` directory for both LLM prompts.
