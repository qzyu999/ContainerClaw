"""
OpenAI-compatible passthrough strategy.

For backends that natively speak the OpenAI Chat Completions API:
MLX-LM, vLLM, Ollama, llama.cpp, and OpenAI itself.

Zero translation — the payload is forwarded as-is with only
authentication headers and URL routing applied.
"""

import requests
import certifi
from requests.adapters import HTTPAdapter


class OpenAIStrategy:
    """Transparent proxy for OpenAI-compatible backends."""

    def __init__(self, provider_config):
        self.name = provider_config.get("name", "openai")
        self.base_url = provider_config["base_url"]
        self.api_key = provider_config.get("api_key", "")
        self.settings = provider_config.get("settings", {})
        self.session = self._build_session()

    def _build_session(self):
        """Build a requests session with connection pooling (no auto-retry).

        Auto-retry is intentionally disabled here. Local inference servers
        (MLX, Ollama) can only serve one request at a time — retry storms
        from 5 concurrent agents cause connection drops. The agent layer
        has its own retry loop with backoff.
        """
        session = requests.Session()
        adapter = HTTPAdapter(
            pool_connections=10,
            pool_maxsize=10,
        )
        session.mount("https://", adapter)
        session.mount("http://", adapter)
        session.verify = certifi.where()
        return session

    def send(self, payload: dict) -> tuple:
        """Forward payload to OpenAI-compatible backend.

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
            res = self.session.post(url, json=payload, headers=headers, timeout=300)
            return res.json(), res.status_code
        except Exception as e:
            return {"error": f"OpenAI strategy request failed: {str(e)}"}, 502
