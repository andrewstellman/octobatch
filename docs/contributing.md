# Contributing

[← Back to README](../README.md)

Octobatch is under active development. This page covers development setup, the context file system, and how to make contributions.

## Development Setup

```bash
git clone https://github.com/andrewstellman/octobatch.git
cd octobatch
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

You'll need at least one API key for testing pipelines:

```bash
export GOOGLE_API_KEY="your-key"
export OPENAI_API_KEY="your-key"     # optional
export ANTHROPIC_API_KEY="your-key"  # optional
```

## The Context File System

Octobatch uses a system of `CONTEXT.md` files to maintain continuity across development sessions. These files are written for AI coding assistants (primarily Claude Code) but are useful for human contributors too.

### Why Context Files Exist

Octobatch is developed using an "AI-assisted" workflow. The developer describes what to build, Claude Code generates the implementation, and context files keep the AI oriented across sessions. Without them, each new session would start from scratch — re-reading code, re-discovering design decisions, repeating mistakes.

Context files solve this by documenting:
- What was just built and why
- Active bugs with root cause analysis
- Technical learnings (especially non-obvious decisions)
- Architecture and patterns to follow

### Context File Inventory

| File | Purpose | Update Frequency |
|------|---------|-----------------|
| `ai_context/DEVELOPMENT_CONTEXT.md` | Session state, recent work, active bugs, technical learnings | Every session |
| `ai_context/PROJECT_CONTEXT.md` | Stable architecture, design patterns, folder structure | When architecture changes |
| `ai_context/QUALITY.md` | Quality constitution — coverage targets, fitness scenarios, review protocol | When quality standards evolve |
| `scripts/CONTEXT.md` | Orchestrator and CLI tools | When orchestrator changes |
| `scripts/tui/CONTEXT.md` | TUI application structure | When TUI changes |
| `scripts/tui/utils/CONTEXT.md` | Utility functions | When utils change |
| `pipelines/CONTEXT.md` | Pipeline configuration format | When config format changes |
| `pipelines/TOOLKIT.md` | AI-facing pipeline creation reference | When pipeline features change |

### DEVELOPMENT_CONTEXT.md — Session State

This is the most frequently updated file. It tracks:

- **Current Focus**: What's being worked on right now
- **Recently Completed**: What was finished in recent sessions
- **Active Bugs**: Known issues with symptoms, root cause analysis, and notes
- **Key Technical Learnings**: Non-obvious decisions and their reasoning

The most important rule: **always include the WHY**. When documenting a technical learning, explain *why* the decision was made, not just *what* was decided. This prevents future sessions from "refactoring" a deliberate choice.

Example of a good learning entry:

```markdown
### Recursive `set_timer()` for Auto-Refresh on Pushed Screens
- **What**: Use recursive `set_timer()` instead of `set_interval()`
- **Why**: Textual's `set_interval()` callbacks are not reliably serviced on
  pushed screens. The timer is created but the callback never fires. Recursive
  `set_timer()` works because each one-shot timer schedules the next.
- **Discovered**: Jan 30, 2026
- **Location**: `scripts/tui/screens/main_screen.py`
```

### PROJECT_CONTEXT.md — Architecture

Updated less frequently. Documents stable architecture: the orchestrator, TUI screens, data flow, design decisions, and folder structure. Update this when making architectural changes — new screens, new execution modes, new file structures.

### Component CONTEXT.md Files

Each major directory has its own CONTEXT.md documenting that component's API, key functions, and patterns. These are updated when the component's interface or structure changes significantly.

### TOOLKIT.md — The AI Pipeline Reference

This file is special. It's written *for Claude Code*, not for humans. It's the complete reference that enables Claude Code to generate pipeline configurations. It includes:

- Pipeline structure and directory layout
- Complete config.yaml reference
- Template syntax and available variables
- Schema format and common patterns
- Expression step documentation
- Unit generation strategies
- Testing procedures

When pipeline features change, TOOLKIT.md must be updated to reflect the new capabilities.

## The Bootstrap Prompt

To start a development session, give Claude Code this prompt:

```
Read ai_context/DEVELOPMENT_CONTEXT.md and bootstrap yourself to continue development.
```

Claude Code reads DEVELOPMENT_CONTEXT.md, which contains bootstrap instructions pointing to the other context files. After loading the full context chain, it understands the current state and can pick up where the last session left off.

For pipeline work specifically, use:

```
Read pipelines/TOOLKIT.md and use it as your guide for creating Octobatch pipelines.
```

### The Full Bootstrap Prompt for Claude Code Planning Sessions

For sessions where Claude Code doesn't have direct file access (e.g., planning in Claude.ai), a more detailed bootstrap is used. Here's the prompt:

```
I've attached the following context files to this chat (representing the full
documentation state of the project):
- ai_context/DEVELOPMENT_CONTEXT.md - Session state and recent work
- ai_context/PROJECT_CONTEXT.md - System architecture and design patterns
- pipelines/CONTEXT.md - Pipeline configuration format
- pipelines/TOOLKIT.md - The "User Manual" and standard patterns
- scripts/CONTEXT.md - Orchestrator and CLI tools
- scripts/tui/CONTEXT.md - TUI application structure
- scripts/tui/utils/CONTEXT.md - TUI utilities

Please read all attached files to bootstrap yourself, starting with
DEVELOPMENT_CONTEXT.md, then PROJECT_CONTEXT.md, then the others.

## How We Work
This is a planning and coordination chat. You do NOT have direct access to the
source code. When investigating code:
- Give me shell commands to run locally
- I'll paste the output back to you
- You analyze and suggest next steps

When making code changes:
- Generate prompts for Claude Code (our code editing tool)
- I'll paste the prompt into Claude Code and report results

Confirm when you have processed the context and are ready for a task.
```

## The Update Discipline

When making changes, update the relevant context files so the next session has continuity:

1. **After significant progress**: Update `DEVELOPMENT_CONTEXT.md` — move items to "Recently Completed," update "Current Focus"
2. **After architectural changes**: Update `PROJECT_CONTEXT.md` — new screens, new features, new patterns
3. **After component changes**: Update the relevant `CONTEXT.md` in that directory
4. **After pipeline feature changes**: Update `pipelines/TOOLKIT.md`

This discipline is what makes the AI-assisted workflow sustainable across many sessions.

## Code Style and Patterns

### Follow Existing Patterns

Each component has established patterns. Before making changes, read the relevant CONTEXT.md and look at existing code to understand:

- How state is managed (manifest as source of truth)
- How UI updates work (reactive properties, watchers)
- How errors are handled (try/except with status management)
- How subprocesses are managed (Popen with detachment)

### Key Patterns to Follow

- **Atomic manifest updates**: Write to temp file, then rename
- **Recursive `set_timer()`**: For auto-refresh on Textual pushed screens (not `set_interval()`)
- **Guard before modify**: Check `get_process_health()` before modifying manifest or spawning orchestrator
- **Full refresh after state changes**: Call `action_refresh()`, not just `_load_data()`
- **Transition states for UI**: Track items transitioning between states to prevent flicker

## Testing & Quality

For quick checks during development, use config validation and small test runs:

```bash
# Validate pipeline config
python scripts/orchestrate.py --validate-config --config pipelines/MyPipeline/config.yaml

# Small realtime test (5 units, minimal cost)
python scripts/orchestrate.py --init --pipeline MyPipeline \
    --run-dir runs/test --realtime --max-units 5 --provider gemini --yes
```

For comprehensive quality assurance, see **[tests/README.md](../tests/README.md)** which covers the full quality infrastructure: code reviews, integration tests, and the Council of Three regression methodology. The three levels, from fastest to most thorough:

1. **Code review** (~10 min) — have 2+ AI tools independently review recent changes
2. **Integration tests** (~25 min) — run all pipelines across all providers
3. **Regression tests** (~2 hours) — full codebase audit against intent specs

### Debug Logging

```bash
python scripts/tui.py  # Check tui_debug.log for detailed operation logs
```

## Submitting PRs

1. **Test your changes**: Run `--validate-config` on any modified pipelines. Do a small realtime test run. Check the TUI if your changes affect the UI.
2. **Update context files**: If your change is significant, update the relevant CONTEXT.md files.
3. **Keep commits focused**: One logical change per commit.
4. **Describe the why**: PR descriptions should explain *why* a change was made, not just *what* changed.
