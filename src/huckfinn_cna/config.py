"""Load config/pipeline.toml and resolve its paths against the repository root."""

from __future__ import annotations

import tomllib
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG = REPO_ROOT / "config" / "pipeline.toml"


def load_config(path: Path = DEFAULT_CONFIG) -> dict:
    with path.open("rb") as fh:
        return tomllib.load(fh)


def repo_path(config: dict, key: str) -> Path:
    """Absolute path for config['paths'][key]."""
    return REPO_ROOT / config["paths"][key]
