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
import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from contextlib import asynccontextmanager

# Add shared module path for config_loader
sys.path.insert(0, os.getenv("SHARED_MODULE_PATH", "/app/shared"))

from src.providers.openai_strategy import OpenAIStrategy
from src.providers.gemini_strategy import GeminiStrategy

# ── Strategy Registry ────────────────────────────────────────────

STRATEGY_MAP = {
    "openai": OpenAIStrategy,
    "gemini": GeminiStrategy,
}


def _load_strategies(client: httpx.AsyncClient) -> tuple[dict, str]:
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
                    strategies[name] = cls(prov, client)
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
    }, client)
    print("⚠️ [Gateway] Using fallback Gemini-only configuration.")
    return strategies, "gemini-cloud"


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Setup client with connection pools and timeouts for LLMs
    limits = httpx.Limits(max_connections=100, max_keepalive_connections=20)
    timeout = httpx.Timeout(10.0, read=90.0)
    async with httpx.AsyncClient(timeout=timeout, limits=limits) as client:
        # Load strategies and attach them to the application state
        strategies, default_provider = _load_strategies(client)
        app.state.strategies = strategies
        app.state.default_provider = default_provider
        yield
    # Client is automatically closed here


app = FastAPI(lifespan=lifespan)


# ── Routes ───────────────────────────────────────────────────────

@app.post('/v1/chat/completions')
async def proxy(request: Request):
    """Route LLM requests to the appropriate provider strategy."""
    try:
        data = await request.json()
    except Exception:
        return JSONResponse({"error": "Invalid JSON"}, status_code=400)

    # Determine provider: explicit in request → default from config
    provider_name = data.pop("provider", None) or app.state.default_provider
    strategy = app.state.strategies.get(provider_name)

    if not strategy:
        return JSONResponse(
            {"error": f"Unknown provider: {provider_name}. Available: {list(app.state.strategies.keys())}"}, 
            status_code=400
        )

    res, status_code = await strategy.send(data)
    return JSONResponse(content=res, status_code=status_code)


@app.get('/health')
async def health():
    """Health check endpoint."""
    return {
        "status": "ok",
        "providers": list(app.state.strategies.keys()),
        "default_provider": app.state.default_provider,
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("src.main:app", host="0.0.0.0", port=8000)
