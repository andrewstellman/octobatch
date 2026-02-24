# Config Editor Directory Context
> **File:** `scripts/tui/config_editor/CONTEXT.md`

## 1. Purpose

This directory contains the Pipeline Configuration Editor - a TUI-based editor for creating, viewing, and modifying pipeline configurations. It follows a "Docs + Claude Code" strategy where users can copy configuration context to clipboard for AI-assisted prompt engineering.

## 2. Key Components

### models.py (Data Models & YAML Utilities)
**PipelineConfig Class:**
```python
class PipelineConfig:
    name: str           # Configuration name
    base_dir: Path      # Root directory of the pipeline
    config_path: Path   # Points to base_dir/config.yaml
    _config: dict       # In-memory configuration cache

    def load() -> None      # Loads YAML from disk
    def save() -> None      # Saves _config to YAML
    @property config        # Returns cached _config
    @property steps         # Extracts pipeline.steps array
    @property items_source  # Gets processing.items.source
    @property step_count    # Counts non-run-scope steps
```

**YAML Utilities:**
- `load_yaml(path)`: Safe YAML load, returns `{}` if file missing
- `save_yaml(path, data)`: Creates directories, writes human-readable YAML
- `discover_pipelines(dir)`: Scans for directories with config.yaml

### list_screen.py (ConfigListScreen - Pipeline List)
**Purpose:** Modal screen displaying all discovered pipeline configurations.

**Layout:**
```
┌─────────────────────────────────────────────────┐
│ Pipeline Configurations                  [header]│
├─────────────────────────────────────────────────┤
│ │ Name        │ Steps │ Items Source │         │
│ │ Example     │     3 │ items.yaml   │         │
│ │ Another     │     1 │ data.yaml    │         │
├─────────────────────────────────────────────────┤
│ N:new  E:edit  R:rename  D:delete      [footer] │
└─────────────────────────────────────────────────┘
```

**Key Bindings:**
| Key | Action | Description |
|-----|--------|-------------|
| N | new_config | Create new pipeline |
| E | edit_config | Open EditConfigScreen |
| R | rename_config | Rename selected pipeline |
| D | delete_config | Delete with confirmation |
| Q/q | quit_app | Exit application |
| Escape | close | Return to previous screen |

### edit_screen.py (EditConfigScreen - Step Editor)
**Purpose:** Split-panel editor for viewing and editing pipeline steps.

**Layout:**
```
┌─────────────────────────────────────────────────┐
│ Edit Configuration: Example              [header]│
├─────────────────────────────────────────────────┤
│ Pipeline Visualization (top panel)              │
│ ┌──────────┐   ┌──────────┐   ┌──────────┐    │
│ │ GENERATE │──▶│ COHERENCE│──▶│  WOUNDS  │    │
│ └──────────┘   └──────────┘   └──────────┘    │
├─────────────────────────────────────────────────┤
│ Step Details (bottom panel)                     │
│ > Step Details: generate                        │
│   Name: generate                     [editable] │
│   Template: story_generation_prompt  [editable] │
│   Description: Generate stories      [editable] │
│   Schema: output (string)           [read-only] │
│   Schema: stories (array)           [read-only] │
├─────────────────────────────────────────────────┤
│ ←→:step  ↑↓:nav  Enter:edit  C:copy  V:template│
└─────────────────────────────────────────────────┘
```

**Key Bindings:**
| Key | Action | Description |
|-----|--------|-------------|
| ←/→ | prev/next_step | Navigate pipeline steps |
| ↑/↓ | panel navigation | Switch panels or navigate items |
| Enter | edit_property | Edit selected property (if editable) |
| C | copy_context | Copy step context to clipboard |
| V | view_template | Open template in ViewTemplateModal |
| A | add_step | Add new step |
| Q/q | quit_app | Exit application |
| Escape | close | Return to list screen |

**Reactive Properties:**
- `selected_step_index`: Currently selected step (0-indexed)
- `focus_panel`: "top" or "bottom" panel focus

**Expression Steps:**
Steps with `scope: expression` are displayed in the pipeline visualization but have different editing behavior:
- No template file to view (V key shows "No template for expression steps")
- No schema or validation entries required
- The `expressions:` block is shown in step properties

### modals.py (CRUD Modals)
**NewConfigModal:** Create new pipeline directory with skeleton config
**RenameConfigModal:** Rename pipeline directory
**DeleteConfigModal:** Confirm deletion with Y/N
**EditStepModal:** Add or edit pipeline steps

**Skeleton Config Created by NewConfigModal:**
```yaml
pipeline:
  steps:
    - name: generate
      description: Generate content
      prompt_template: generate.jinja2
api:
  provider: gemini
  model: gemini-2.0-flash-001
processing:
  chunk_size: 100
  items:
    source: items.yaml
    key: items
prompts:
  template_dir: templates
schemas:
  schema_dir: schemas
```

## 3. Data Flow

```
ConfigListScreen
    │
    ├── discover_pipelines() → list[PipelineConfig]
    │
    ├── N → NewConfigModal
    │   └── Creates: pipelines/{name}/
    │       ├── config.yaml (skeleton)
    │       ├── templates/generate.jinja2
    │       └── schemas/
    │
    ├── E → EditConfigScreen(config)
    │   │
    │   ├── _non_run_steps (filtered steps)
    │   │
    │   ├── C → Copy Context
    │   │   └── Clipboard: YAML + Template content
    │   │
    │   ├── Enter → EditPropertyModal
    │   │   └── Updates config.save()
    │   │
    │   └── V → ViewTemplateModal
    │       └── Read-only template display
    │
    ├── R → RenameConfigModal
    │   └── shutil.move(old_dir, new_dir)
    │
    └── D → DeleteConfigModal
        └── shutil.rmtree(config_dir)
```

## 4. Architectural Decisions

### "Docs + Claude Code" Strategy
The Copy Context feature (C key) bundles step configuration and template content for AI-assisted editing:
```
[Context for Step: generate]

YAML Config:
name: generate
description: Generate 4 stories per card triple
prompt_template: story_generation_prompt.jinja2
...

Template (templates/story_generation_prompt.jinja2):
You are a creative writing assistant...
```

Users paste this into Claude Code to get AI assistance with prompt engineering.

### Non-Run-Scope Step Filtering
Run-scope steps are hidden from the editor:
```python
_non_run_steps = [s for s in config.steps if s.get("scope") != "run"]
```

### Template Path Resolution
Searches multiple locations for template files:
1. Direct path: `config_dir / template_name`
2. Templates subdirectory: `config_dir / "templates" / template_name`
3. With .j2 extension: `config_dir / template_name.j2`
4. Combined: `config_dir / "templates" / template_name.j2`

### Editable vs Read-Only Properties
- **Editable** (cyan): Name, Template, Description
- **Read-only** (dim): Schema fields, Validation rules

Read-only properties are extracted from nested config but not editable through the simple property editor.

### Expression Step Handling
Expression steps don't follow the 4-Point Link rule. The editor recognizes `scope: expression` and:
- Skips template resolution
- Doesn't warn about missing schema/validation entries
- Displays the `expressions:` configuration instead

## 5. Key Patterns & Conventions

### Panel Focus Indication
```python
# Top panel title with focus indicator
if self.focus_panel == "top":
    title = "[bold cyan]> Pipeline Steps[/]"
else:
    title = "[dim]Pipeline Steps[/]"
```

### Step Box Rendering
```python
# Selected step highlighted
if selected:
    if has_focus:
        style = "[bold cyan reverse]"
    else:
        style = "[cyan]"
else:
    style = ""
```

### YAML Preservation
`save_yaml()` uses `sort_keys=False` to preserve key order, and `default_flow_style=False` for human-readable output.

### Clipboard Integration
Uses `pyperclip` library with graceful fallback:
```python
try:
    import pyperclip
    pyperclip.copy(context)
    self.app.notify("Copied to clipboard")
except ImportError:
    self.app.notify("pyperclip not installed", severity="warning")
```

## 6. Recent Changes

### Split-Panel Layout
Replaced simple list with two-panel design:
- Top: Pipeline flow visualization with connected boxes
- Bottom: OptionList with step properties

### Copy Context (C Key)
Added clipboard integration for Claude Code workflow:
- Serializes step config as YAML
- Includes template file contents
- Formatted for easy pasting

### Template Viewing (V Key)
ViewTemplateModal shows template content read-only with escape to close.

### Focus Management
Arrow keys now switch between panels:
- Down at bottom of top panel → focus bottom
- Up at top of bottom panel → focus top

## 7. Current State & Known Issues

### Working Features
- Pipeline list with CRUD operations
- Split-panel step editor
- Copy context to clipboard
- Template viewing
- Property editing for basic fields

### Known Limitations
- No inline template editing (read-only view)
- Schema and validation rules not editable
- No undo/redo for edits

### Technical Debt
- EditConfigScreen is large (~770 lines), could be split
- Some CSS could be extracted to shared styles

### Planned Improvements
- Inline template editing
- Schema field editing
- Validation rule editor
- Preview prompt output

## 8. Testing

### Manual Testing
```bash
# Open pipeline editor from HomeScreen
# Press P to open ConfigListScreen

# Test New Pipeline
# Press N, enter name, verify directory created

# Test Edit
# Select pipeline, press E
# Navigate with arrows, press C to copy context
# Verify clipboard contains YAML + template

# Test Template View
# Press V on a step with template
# Verify template content displays

# Test Property Edit
# Navigate to Name/Template/Description
# Press Enter, modify, save
# Verify config.yaml updated
```

### Key Test Scenarios
1. **Pipeline Creation**: Verify skeleton config and directories created
2. **Copy Context**: Paste clipboard content, verify format
3. **Panel Navigation**: Arrows switch panels correctly
4. **Property Editing**: Changes persist to config.yaml
5. **Template Resolution**: Finds templates in various locations
