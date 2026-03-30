"""
LLM Gateway — Provider-Agnostic Router.

Routes requests to the appropriate backend via the Strategy Pattern.
For OpenAI-compatible backends (MLX, vLLM, Ollama), requests pass through
unchanged. For Gemini, the GeminiStrategy handles bidirectional translation.

All provider configuration comes from config.yaml (mounted at /config/).
"""

import os
import sys
import json
from flask import Flask, request

# Add shared module path for config_loader
sys.path.insert(0, os.getenv("SHARED_MODULE_PATH", "/app/shared"))

from src.providers.openai_strategy import OpenAIStrategy
from src.providers.gemini_strategy import GeminiStrategy

app = Flask(__name__)

# ── Strategy Registry ────────────────────────────────────────────

STRATEGY_MAP = {
    "openai": OpenAIStrategy,
    "gemini": GeminiStrategy,
}


def _load_strategies() -> tuple[dict, str]:
    """Load provider strategies from config.yaml or env vars."""
    config_path = os.getenv("CLAW_CONFIG_PATH", "/config/config.yaml")
    strategies = {}
    default_provider = "gemini-cloud"

    try:
        # Try loading from config.yaml
        import yaml
        from pathlib import Path

        if Path(config_path).exists():
            with open(config_path) as f:
                raw = yaml.safe_load(f)

            llm = raw.get("llm", {})
            default_provider = llm.get("default_provider", "gemini-cloud")

            for name, prov in llm.get("providers", {}).items():
                prov_type = prov.get("type", "openai")
                cls = STRATEGY_MAP.get(prov_type)
                if cls:
                    # Resolve API key from Docker secret if needed
                    api_key = prov.get("api_key", "")
                    if not api_key and prov.get("api_key_secret"):
                        try:
                            api_key = Path(f"/run/secrets/{prov['api_key_secret']}").read_text().strip()
                        except Exception:
                            api_key = ""
                    prov["api_key"] = api_key
                    prov["name"] = name
                    strategies[name] = cls(prov)
                    print(f"✅ [Gateway] Loaded provider: {name} ({prov_type})")

            print(f"🎯 [Gateway] Default provider: {default_provider}")
            return strategies, default_provider

    except Exception as e:
        print(f"⚠️ [Gateway] Failed to load config.yaml: {e}. Falling back to env vars.")

    # Fallback: build Gemini-only strategy from env/secrets (backward compat)
    def get_secret(name):
        try:
            with open(f"/run/secrets/{name}", "r") as f:
                return f.read().strip()
        except Exception:
            return None

    api_key = get_secret("gemini_api_key") or os.getenv("GEMINI_API_KEY", "")
    strategies["gemini-cloud"] = GeminiStrategy({
        "name": "gemini-cloud",
        "base_url": "https://generativelanguage.googleapis.com/v1beta",
        "api_key": api_key,
        "settings": {"thinking_level": "HIGH", "max_output_tokens": 8192},
    })
    print("⚠️ [Gateway] Using fallback Gemini-only configuration.")
    return strategies, "gemini-cloud"


# Initialize on module load
strategies, default_provider = _load_strategies()


# ── Routes ───────────────────────────────────────────────────────

@app.route('/v1/chat/completions', methods=['POST'])
def proxy():
    """Route LLM requests to the appropriate provider strategy."""
    data = request.json

    # Determine provider: explicit in request → default from config
    provider_name = data.pop("provider", None) or default_provider
    strategy = strategies.get(provider_name)

    if not strategy:
        return {"error": f"Unknown provider: {provider_name}. "
                f"Available: {list(strategies.keys())}"}, 400

    return strategy.send(data)


@app.route('/health', methods=['GET'])
def health():
    """Health check endpoint."""
    return {
        "status": "ok",
        "providers": list(strategies.keys()),
        "default_provider": default_provider,
    }


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8000)
