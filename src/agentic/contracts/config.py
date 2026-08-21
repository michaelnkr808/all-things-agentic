"""Environment/permissions config schema + loader.

Michael owns `load_config` (the parsing task). Malik's permission checks
consume the parsed `EnvironmentConfig` — never the raw YAML.
Canonical example: config/environment.example.yaml
"""

from __future__ import annotations

from pathlib import Path

from enum import Enum

from typing import Literal

import yaml
from pydantic import BaseModel, Field, ValidationError, model_validator


class ConfigError(Exception):
    """Raised when the environment config is missing, malformed or wrong"""


class StorageConfig(BaseModel):
    """Optional cloud backend for a department's data.

    A department with `storage` set reads its files from the cloud instead
    of the local filesystem. Object keys / Drive paths resolve as relative
    paths under the department root, so permissions containment and the
    [[wikilinks]] emitter treat them exactly like local files.
    """

    provider: Literal["gcs", "drive"]
    bucket: str | None = None  # gcs: bucket name
    prefix: str = ""  # gcs: logical containment root for object keys
    folder_id: str | None = None  # drive: department root folder
    max_bytes: int = 5_000_000  # per-file download cap

    @model_validator(mode="after")
    def _requires_its_backend(self) -> "StorageConfig":
        if self.provider == "gcs" and not self.bucket:
            raise ValueError("gcs storage requires 'bucket'")
        if self.provider == "drive" and not self.folder_id:
            raise ValueError("drive storage requires 'folder_id'")
        return self


class DepartmentConfig(BaseModel):
    name: str
    path: str  # data directory for this department
    allowed_roles: list[str] = Field(default_factory=list)  # roles that may read it
    file_globs: list[str] = Field(
        default_factory=lambda: ["**/*.md", "**/*.txt", "**/*.csv"]
    )
    storage: StorageConfig | None = None  # cloud backend, if any


class GathererLimits(BaseModel):
    max_gatherers: int = 4  # cap on parallel gatherers per prompt
    max_files_per_gatherer: int = 10
    overgather_factor: float = 1.5  # multiply "obviously relevant" count by this


class ModelConfig(BaseModel):
    client: str = "claude-fable-5"
    state_manager: str = "gemini-3.5-flash"
    gatherer: str = "gemini-3.5-flash"
    # TEMPORARY: Opus 5 is $5/$25 per MTok vs Fable 5's $10/$50 — halves the cost
    # of every veto check while iterating. Fable 5 is the more capable model and
    # this is the adversarial reviewer, so switch back before the demo.
    veto: str = "claude-opus-5"


class VetoConfig(BaseModel):
    max_retries: int = 2  # revise passes before we give up and ship with a warning


class AgentType(str, Enum):
    FILE_GATHERER = "file_gatherer"


class AgentTypeConfig(BaseModel):
    model: str
    max_files: int


class EnvironmentConfig(BaseModel):
    departments: list[DepartmentConfig]
    gatherers: GathererLimits = GathererLimits()
    models: ModelConfig = ModelConfig()
    agent_types: dict[AgentType, AgentTypeConfig]
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
