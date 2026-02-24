"""
Data models and utilities for pipeline configuration.

Contains PipelineConfig class and YAML helper functions.
"""

import yaml
from pathlib import Path


def load_yaml(path: Path) -> dict:
    """Load a YAML file."""
    if not path.exists():
        return {}
    with open(path) as f:
        return yaml.safe_load(f) or {}


def save_yaml(path: Path, data: dict) -> None:
    """Save a YAML file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, 'w') as f:
        yaml.dump(data, f, default_flow_style=False, sort_keys=False)


class PipelineConfig:
    """Represents a pipeline configuration."""

    def __init__(self, name: str, base_dir: Path):
        self.name = name
        self.base_dir = base_dir
        self.config_path = base_dir / "config.yaml"
        self._config: dict = {}

    def load(self) -> None:
        """Load configuration from disk."""
        self._config = load_yaml(self.config_path)

    def save(self) -> None:
        """Save configuration to disk."""
        save_yaml(self.config_path, self._config)

    @property
    def config(self) -> dict:
        return self._config

    @property
    def steps(self) -> list[dict]:
        """Get pipeline steps."""
        return self._config.get("pipeline", {}).get("steps", [])

    @property
    def items_source(self) -> str:
        """Get items source file."""
        return self._config.get("processing", {}).get("items", {}).get("source", "")

    @property
    def step_count(self) -> int:
        """Get number of pipeline steps (excluding run-scope steps)."""
        return len([s for s in self.steps if s.get("scope") != "run"])


def discover_pipelines(pipelines_dir: Path) -> list[PipelineConfig]:
    """Discover all pipeline configurations in a directory."""
    configs = []
    if not pipelines_dir.exists():
        return configs

    for item in sorted(pipelines_dir.iterdir()):
        if item.is_dir():
            config_file = item / "config.yaml"
            if config_file.exists():
                config = PipelineConfig(item.name, item)
                config.load()
                configs.append(config)

    return configs
