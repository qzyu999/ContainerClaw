"""
ContainerClaw Agent Configuration — Thin wrapper over config_loader.

This file exists for backward compatibility. All modules that
`import config` get values sourced from config.yaml (via config_loader)
or env vars (via _from_env fallback).
"""

import os
import sys

# Add shared/ to the Python path for config_loader
sys.path.insert(0, os.getenv("SHARED_MODULE_PATH", "/app/shared"))

from config_loader import load_config  # noqa: E402

_cfg = load_config()

# ── Backward-compatible module-level constants ──────────────────
# These match the old `config.py` interface so existing imports work.

LLM_GATEWAY_URL = _cfg.gateway_url
DEFAULT_MODEL = _cfg.default_model
MAX_HISTORY_MESSAGES = _cfg.max_history_messages
MAX_HISTORY_CHARS = _cfg.max_history_chars
MAX_TOOL_ROUNDS = _cfg.max_tool_rounds
CONCHSHELL_ENABLED = _cfg.conchshell_enabled
AUTONOMOUS_STEPS = _cfg.autonomous_steps
FLUSS_BOOTSTRAP_SERVERS = _cfg.fluss_bootstrap_servers
SESSION_ID = _cfg.session_id

# Expose the full config object for new code
CONFIG = _cfg
