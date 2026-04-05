import os
import sys

# Add parent of shared/ to the Python path so it can be imported as a package
shared_path = os.getenv("SHARED_MODULE_PATH", "/app/shared")
sys.path.insert(0, os.path.dirname(shared_path))

from shared.config_loader import load_config

# Load unified configuration
_cfg = load_config()

# Gunicorn configuration
bind = f"0.0.0.0:{_cfg.gateway_port}"
workers = 4
worker_class = "uvicorn.workers.UvicornWorker"
timeout = _cfg.llm_timeout_s
accesslog = "-"  # Log to stdout
errorlog = "-"   # Log to stderr

print(f"🚀 [Gunicorn] Starting with unified config: bind={bind}, timeout={timeout}s")
