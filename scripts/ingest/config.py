"""Centralised config loader for loom.

All path resolution goes through this module.
Reads config/loom.yml (local overrides) or falls back to config/loom.example.yml.
"""

from pathlib import Path
from typing import Optional

import yaml

SCRIPTS_DIR = Path(__file__).resolve().parent.parent  # scripts/
LOOM_ROOT = SCRIPTS_DIR.parent  # loom repo root


def _load_yaml() -> dict:
    """Load loom.yml, falling back to loom.example.yml."""
    for name in ("loom.yml", "loom.example.yml"):
        p = LOOM_ROOT / "config" / name
        if p.exists():
            with open(p) as f:
                return yaml.safe_load(f) or {}
    return {}


_config: Optional[dict] = None


def _cfg() -> dict:
    global _config
    if _config is None:
        _config = _load_yaml()
    return _config


def _get(key: str, default: Optional[str] = None) -> Optional[str]:
    """Get a dot-separated key from config."""
    parts = key.split(".")
    d = _cfg()
    for part in parts:
        if isinstance(d, dict):
            d = d.get(part)
        else:
            return default
    return d if d is not None else default


# ── Public paths ──────────────────────────────────────────────

def raw_dir() -> Path:
    p = _get("data.raw_dir")
    return Path(p) if p else LOOM_ROOT / "raw"


def wiki_dir() -> Path:
    p = _get("data.wiki_dir")
    return Path(p) if p else LOOM_ROOT / "wiki"


def data_dir() -> Path:
    p = _get("data.data_dir")
    return Path(p) if p else LOOM_ROOT / "data"


def config_dir() -> Path:
    p = _get("data.config_dir")
    return Path(p) if p else LOOM_ROOT / "config"


def db_path() -> Path:
    p = _get("db_path")
    return Path(p) if p else data_dir() / "task-queue.db"


def log_dir() -> Path:
    p = _get("log_dir")
    return Path(p) if p else LOOM_ROOT / "logs"


# Quick test
if __name__ == "__main__":
    print(f"raw_dir:    {raw_dir()}")
    print(f"wiki_dir:   {wiki_dir()}")
    print(f"data_dir:   {data_dir()}")
    print(f"config_dir: {config_dir()}")
    print(f"db_path:    {db_path()}")
    print(f"log_dir:    {log_dir()}")
