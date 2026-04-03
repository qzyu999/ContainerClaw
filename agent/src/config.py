"""
ContainerClaw Agent Configuration — Thin wrapper over config_loader.

All modules that `import config` get values sourced from config.yaml
(via config_loader). config.yaml is the single source of truth.
"""

import os
import sys

# Add parent of shared/ to the Python path so it can be imported as a package
shared_path = os.getenv("SHARED_MODULE_PATH", "/app/shared")
sys.path.insert(0, os.path.dirname(shared_path))

from shared.config_loader import load_config  # noqa: E402

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
SUBAGENT_TTL_SECONDS = _cfg.subagent_ttl_seconds
FLUSS_BOOTSTRAP_SERVERS = _cfg.fluss_bootstrap_servers
SESSION_ID = _cfg.session_id

# ── Tool Settings ───────────────────────────────────────────────
TOOLS = _cfg.tool_settings
WORKSPACE_ROOT = TOOLS.workspace_root
TOOL_TIMEOUTS = TOOLS.timeouts
SEARCH_LIMITS = TOOLS.search_limits

# Expose the full config object for new code
CONFIG = _cfg
