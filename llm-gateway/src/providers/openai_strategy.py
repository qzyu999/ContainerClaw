"""
OpenAI-compatible passthrough strategy.

For backends that natively speak the OpenAI Chat Completions API:
MLX-LM, vLLM, Ollama, llama.cpp, and OpenAI itself.

Zero translation — the payload is forwarded as-is with only
authentication headers and URL routing applied.
"""

import httpx
import asyncio

class OpenAIStrategy:
    """Transparent proxy for OpenAI-compatible backends."""

    def __init__(self, provider_config, client: httpx.AsyncClient):
        self.name = provider_config.get("name", "openai")
        self.base_url = provider_config["base_url"]
        self.api_key = provider_config.get("api_key", "")
        self.settings = provider_config.get("settings", {})
        self.client = client
        self.semaphore = asyncio.Semaphore(20)

    async def send(self, payload: dict) -> tuple:
        """Forward payload to OpenAI-compatible backend asynchronously.

        Returns:
            (response_dict, status_code) — response is passed through unchanged.
        """
        headers = {
            "Content-Type": "application/json",
        }
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        # Ensure base_url ends without /v1 duplication
        base = self.base_url.rstrip("/")
        if base.endswith("/v1"):
            url = f"{base}/chat/completions"
        else:
            url = f"{base}/v1/chat/completions"

        try:
            async with self.semaphore:
                res = await self.client.post(url, json=payload, headers=headers)
            return res.json(), res.status_code
        except Exception as e:
            return {"error": f"OpenAI strategy request failed: {str(e)}"}, 502

