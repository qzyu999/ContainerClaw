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

from shared.config_loader import load_config  # noqa: E402
from src.providers.openai_strategy import OpenAIStrategy
from src.providers.gemini_strategy import GeminiStrategy

# ── Strategy Registry ────────────────────────────────────────────

STRATEGY_MAP = {
    "openai": OpenAIStrategy,
    "gemini": GeminiStrategy,
}


def _load_strategies(client: httpx.AsyncClient) -> tuple[dict, str]:
    """Load provider strategies from config.yaml via shared loader."""
    try:
        cfg = load_config()
        strategies = {}
        for name, prov in cfg.providers.items():
            cls = STRATEGY_MAP.get(prov.type)
            if cls:
                # Strategies expect a dict for configuration
                strategies[name] = cls(prov.model_dump(), client)
                print(f"✅ [Gateway] Loaded provider: {name} ({prov.type})")

        print(f"🎯 [Gateway] Default provider: {cfg.default_provider}")
        return strategies, cfg.default_provider

    except Exception as e:
        print(f"❌ [Gateway] Failed to load unified config: {e}")
        # Fail fast to prevent silent startup with broken routing
        sys.exit(1)


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
