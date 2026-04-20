"""
src/state/adapter_factory.py

Factory that reads dltrf.yaml and returns the correct StateAdapter.

Usage:
    from src.state.adapter_factory import load_adapter, load_dltrf_config

    # Get the full config
    cfg = load_dltrf_config()

    # Get the state adapter for the configured DB type
    adapter = load_adapter()
    adapter.snapshot(Path("checkpoints/baseline.checkpoint"))
    adapter.restore(Path("checkpoints/baseline.checkpoint"))
"""

import logging
import os
from pathlib import Path
from typing import Optional

import yaml

from .base_adapter import StateAdapter, StateAdapterError
from .sqlite_adapter import SQLiteAdapter
from .postgres_adapter import PostgresAdapter
from .mysql_adapter import MySQLAdapter

logger = logging.getLogger(__name__)

# Locations searched in order — first match wins
_CONFIG_SEARCH_PATHS = [
    os.environ.get("DLTRF_CONFIG", ""),          # explicit env var
    "/app/dltrf.yaml",                            # inside Docker container (mounted volume)
    "dltrf.yaml",                                  # cwd (when running from host)
    "../dltrf.yaml",                               # one level up (replay-engine subdir)
    os.path.join(os.path.dirname(__file__), "..", "..", "..", "dltrf.yaml"),  # project root
]

_KNOWN_TYPES = {
    "sqlite":   SQLiteAdapter,
    "postgres": PostgresAdapter,
    "mysql":    MySQLAdapter,
}


def _find_config_file() -> Optional[Path]:
    """Search known locations for dltrf.yaml. Return first found path."""
    for candidate in _CONFIG_SEARCH_PATHS:
        if not candidate:
            continue
        p = Path(candidate).resolve()
        if p.is_file():
            return p
    return None


def load_dltrf_config() -> dict:
    """
    Load and return the full parsed dltrf.yaml as a dict.

    Raises:
        FileNotFoundError: If dltrf.yaml is not found in any search path.
        yaml.YAMLError:    If the file is malformed.
    """
    config_path = _find_config_file()
    if config_path is None:
        searched = [p for p in _CONFIG_SEARCH_PATHS if p]
        raise FileNotFoundError(
            f"dltrf.yaml not found. Searched:\n"
            + "\n".join(f"  {p}" for p in searched)
            + "\n\nSet DLTRF_CONFIG env var or place dltrf.yaml in the project root."
        )

    logger.info(f"Loading DLTRF config from {config_path}")
    with open(config_path, encoding="utf-8") as f:
        config = yaml.safe_load(f) or {}

    return config


def load_adapter(config: Optional[dict] = None) -> StateAdapter:
    """
    Read dltrf.yaml (or use provided config dict) and return the correct adapter.

    Args:
        config: Optional pre-loaded config dict. If None, loads from dltrf.yaml.

    Returns:
        Configured StateAdapter instance ready for snapshot() / restore().

    Raises:
        StateAdapterError: If db_type is unknown or required config fields are missing.
        FileNotFoundError: If dltrf.yaml cannot be found and config is None.
    """
    if config is None:
        config = load_dltrf_config()

    state_cfg = config.get("state_management", {})
    if not state_cfg:
        raise StateAdapterError(
            "dltrf.yaml is missing the state_management section. "
            "Add it with at minimum: type: sqlite"
        )

    db_type = state_cfg.get("type", "sqlite").lower().strip()

    # Handle custom adapter (user provides shell scripts)
    if db_type == "custom":
        from .custom_adapter import CustomAdapter
        return CustomAdapter(state_cfg)

    adapter_class = _KNOWN_TYPES.get(db_type)
    if adapter_class is None:
        raise StateAdapterError(
            f"Unknown state_management.type: '{db_type}'. "
            f"Supported types: {', '.join(_KNOWN_TYPES.keys())}, custom"
        )

    logger.info(f"Using {adapter_class.__name__} for db_type='{db_type}'")
    return adapter_class(state_cfg)


def get_target_url(config: Optional[dict] = None) -> str:
    """
    Build the target application URL from dltrf.yaml target section.

    Returns:
        URL string like 'http://juice-shop:3000'
    """
    if config is None:
        try:
            config = load_dltrf_config()
        except FileNotFoundError:
            return os.environ.get("TARGET_APP_URL", "http://juice-shop:3000")

    target = config.get("target", {})
    protocol = target.get("protocol", "http").rstrip(":/")
    host     = target.get("host", "juice-shop")
    port     = int(target.get("port", 3000))

    return f"{protocol}://{host}:{port}"


def get_checkpoint_dir(config: Optional[dict] = None) -> Path:
    """Return the checkpoint directory path (host-side)."""
    config_path = _find_config_file()
    if config_path:
        # Checkpoints live next to dltrf.yaml
        return config_path.parent / "checkpoints"
    return Path("checkpoints")


def get_checkpoint_path(config: Optional[dict] = None) -> Path:
    """Return the full path to the baseline checkpoint file."""
    if config is None:
        try:
            config = load_dltrf_config()
        except FileNotFoundError:
            config = {}

    state_cfg = config.get("state_management", {})
    cp_name   = state_cfg.get("checkpoint_name", "baseline")
    db_type   = state_cfg.get("type", "sqlite").lower()

    checkpoint_dir  = get_checkpoint_dir(config)
    base            = checkpoint_dir / f"{cp_name}.checkpoint"

    # SQL dumps get a .sql extension
    if db_type in ("postgres", "mysql"):
        return base.with_suffix(".checkpoint.sql")
    return base