"""
Pipeline Configuration Editor package for Octobatch TUI.

Provides screens and modals for managing pipeline configurations.
"""

from .models import (
    PipelineConfig,
    discover_pipelines,
    load_yaml,
    save_yaml,
)

from .list_screen import ConfigListScreen

from .edit_screen import EditConfigScreen

from .modals import (
    NewConfigModal,
    RenameConfigModal,
    DeleteConfigModal,
    EditStepModal,
)

__all__ = [
    # Models
    'PipelineConfig',
    'discover_pipelines',
    'load_yaml',
    'save_yaml',
    # Screens
    'ConfigListScreen',
    'EditConfigScreen',
    # Modals
    'NewConfigModal',
    'RenameConfigModal',
    'DeleteConfigModal',
    'EditStepModal',
]
