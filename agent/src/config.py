import os

# Agent Session Configuration
CLAW_SESSION_ID = os.getenv("CLAW_SESSION_ID", "default-session")
CONCHSHELL_ENABLED = os.getenv("CONCHSHELL_ENABLED", "true").lower() == "true"
AUTONOMOUS_STEPS = int(os.getenv("AUTONOMOUS_STEPS", "-1"))

# LLM & Moderation Configuration
DEFAULT_MODEL = os.getenv("DEFAULT_MODEL", "gemini-3-flash-preview")
LLM_GATEWAY_URL = os.getenv("LLM_GATEWAY_URL", "http://llm-gateway:8000")
MAX_HISTORY_MESSAGES = int(os.getenv("MAX_HISTORY_MESSAGES", 100))
MAX_TOOL_ROUNDS = int(os.getenv("MAX_TOOL_ROUNDS", 30))

# Network Configuration
FLUSS_BOOTSTRAP_SERVERS = os.getenv("FLUSS_BOOTSTRAP_SERVERS", "coordinator-server:9123")
