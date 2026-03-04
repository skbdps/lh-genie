"""
config.py — Load application configuration from config.yaml and .env.
"""

from __future__ import annotations

import os
from pathlib import Path

import yaml
from dotenv import load_dotenv

# Load .env file (if present) before reading config
load_dotenv()

_CONFIG: dict | None = None


def get_config() -> dict:
    """Load and cache config.yaml. Merges with environment variables."""
    global _CONFIG
    if _CONFIG is not None:
        return _CONFIG

    config_path = Path(__file__).parent.parent / "config.yaml"
    with open(config_path, "r") as f:
        _CONFIG = yaml.safe_load(f)

    # Override trino MCP URL from env if set
    if os.getenv("MCP_TRINO_URL"):
        _CONFIG.setdefault("trino", {})["mcp_url"] = os.getenv("MCP_TRINO_URL")
    if os.getenv("TRINO_DEFAULT_USER"):
        _CONFIG.setdefault("trino", {})["default_user"] = os.getenv("TRINO_DEFAULT_USER")

    return _CONFIG
