"""Environment/permissions config schema + loader.

Michael owns `load_config` (the parsing task). Malik's permission checks
consume the parsed `EnvironmentConfig` — never the raw YAML.
Canonical example: config/environment.example.yaml
"""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel, Field, ValidationError


class ConfigError(Exception):
    """Raised when the environment config is missing, malformed or wrong"""


class DepartmentConfig(BaseModel):
    name: str
    path: str  # data directory for this department
    allowed_roles: list[str] = Field(default_factory=list)  # roles that may read it
    file_globs: list[str] = Field(
        default_factory=lambda: ["**/*.md", "**/*.txt", "**/*.csv"]
    )


class GathererLimits(BaseModel):
    max_gatherers: int = 4  # cap on parallel gatherers per prompt
    max_files_per_gatherer: int = 10
    overgather_factor: float = 1.5  # multiply "obviously relevant" count by this


class ModelConfig(BaseModel):
    client: str = "claude-fable-5"
    state_manager: str = "gemini-3.5-flash"
    gatherer: str = "gemini-3.5-flash"
    veto: str = "claude-fable-5"  # should check this with malik if we want the veto model to be smart


class VetoConfig(BaseModel):
    max_retries: int = 2  # revise passes before we give up and ship with a warning


class EnvironmentConfig(BaseModel):
    departments: list[DepartmentConfig]
    gatherers: GathererLimits = GathererLimits()
    models: ModelConfig = ModelConfig()
    veto: VetoConfig = VetoConfig()

    def department(self, name: str) -> DepartmentConfig:
        for d in self.departments:
            if d.name == name:
                return d
        raise KeyError(f"unknown department: {name!r}")


def load_config(path: str | Path) -> EnvironmentConfig:
    """Parse and validate the environment YAML. (Michael)

    Requirements:
    - clear error if the file is missing or not valid YAML
    - surface pydantic ValidationError as a readable message (which field, why)
    - verify each department's `path` exists on disk; fail fast otherwise
    """
    # Check to make sure the file exists and the YAML is valid
    try:
        with open(path, "r") as f:
            raw = yaml.safe_load(f)
    except FileNotFoundError:
        raise ConfigError(f"Config file not found at: {path}") from None
    except yaml.YAMLError as e:
        raise ConfigError(f"Invalid YAML at {path}: {e}") from e

    # Validate the raw loaded config
    try:
        cfg = EnvironmentConfig.model_validate(raw)
    except ValidationError as e:
        lines = [
            f"{' -> '.join(str(p) for p in err['loc'])}: {err['msg']}"
            for err in e.errors()
        ]
        raise ConfigError(f"Invalid config in {path}:\n" + "\n".join(lines)) from e

    # Check if each department in the config has a path to an actual directory
    missing = [d.path for d in cfg.departments if not Path(d.path).is_dir()]
    if missing:
        raise ConfigError(
            f"Department directories not found (relative to {Path.cwd()}): {', '.join(missing)}"
        )

    return cfg
