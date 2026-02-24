# Drunken Sailor Walkthrough

[← Back to README](../../README.md)

A random walk simulation on a pier. A sailor leaves a bar and stumbles randomly toward either the ship (safety) or the water (falls in). This pipeline demonstrates looping expression steps, seeded randomness, and mixed expression + LLM pipelines.

## Concept

The sailor starts at position 5 on a pier that runs from position 0 (water) to position 10 (ship). Each step, he moves randomly left or right. The simulation runs until he reaches either end — falling in the water or making it to the ship.

With a symmetric starting position (5 out of 10), probability theory predicts a 50/50 split between outcomes. Running 100 trials lets us verify this empirically.

## The Prompt

This prompt was used to generate the pipeline with Claude Code:

```
Read pipelines/TOOLKIT.md to understand Octobatch pipeline creation.

Create a Drunken Sailor simulation pipeline.

Concept: A sailor leaves a bar and stumbles randomly toward either the ship (safety)
or the water (falls in). Each step he moves left or right. If he reaches position 0,
he falls in the water. If he reaches position 10, he makes it to the ship.

Pipeline steps:
1. Random Walk (Expression, name: random_walk) — Simulate the walk locally with no
   LLM. Start at the scenario's start_position, keep track of the full path and the
   number of steps taken. Each iteration, randomly move left or right. Loop until the
   sailor reaches 0 (water) or 10 (ship). Set max_iterations to 1000.
2. Analyze (LLM, name: analyze) — Receives the walk data and determines the outcome.
   Should report the final position, steps taken, the full path, and whether the
   sailor fell in the water or reached the ship.

Items: One scenario where the sailor starts at position 5.

Processing: Use the direct strategy with 100 repetitions and chunks of 100.

After the pipeline completes, automatically generate reports showing:
- Outcome distribution grouped by scenario
- Steps taken overall grouped by scenario
- Steps taken grouped by outcome

Create in pipelines/DrunkenSailor/
```

## Pipeline Structure

```
+---------------------+    +---------------+
| random_walk         |--->| analyze       |
| scope: expression   |    | LLM step      |
| loop_until: <=0 or  |    |               |
|   >=10              |    |               |
+---------------------+    +---------------+
```

**Step 1: random_walk** — An expression step with `loop_until`. Runs locally, no LLM call, no cost. The sailor starts at position 5 and takes random steps left or right until reaching 0 (water) or 10 (ship). A typical walk takes 15–40 steps. Each unit gets a deterministic seed, so results are fully reproducible.

**Step 2: analyze** — An LLM step that receives the walk data and determines the outcome. The LLM reads the path and final position, then reports the result as structured JSON.

## Key Pattern: Expression Steps

This pipeline demonstrates that not every step needs an LLM. The random walk simulation is pure computation — dealing with random numbers and loop conditions. Expression steps handle this locally with zero cost and instant execution, regardless of whether you're in batch or realtime mode. The LLM is only used where judgment is needed.

See `pipelines/DrunkenSailor/config.yaml` for the full configuration, `items.yaml` for the scenario definition, and `templates/analyze.jinja2` for the LLM prompt.

## Expected Results

With a symmetric start position (5 out of 10), you should see approximately 50% fell in water and 50% reached ship. The exact split varies by run due to randomness, but with 100 trials it should be close to 50/50 — matching the theoretical prediction for a symmetric random walk with absorbing barriers.

Average steps taken is typically around 25. The pipeline produces three post-processing outputs: an outcome distribution table, steps-by-outcome statistics, and a CSV for further analysis.
