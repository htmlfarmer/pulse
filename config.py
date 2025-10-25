"""Small YAML config loader for pulse.
Falls back to defaults in the absence of a config file.
"""
from pathlib import Path
from typing import Dict, Any
import yaml


def load_config(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {}
    with path.open('rt', encoding='utf-8') as fh:
        return yaml.safe_load(fh) or {}


def default_config() -> Dict[str, Any]:
    return {
        'feeds': [],
        'wikipedia': [],
        'user_agent': 'pulse/1.0 (+https://github.com/htmlfarmer/pulse)',
        'limit': 5,
        'max_places': 200,
        'max_features': 1000,
    }
