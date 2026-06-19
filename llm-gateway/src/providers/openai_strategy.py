"""
OpenAI-compatible passthrough strategy.

For backends that natively speak the OpenAI Chat Completions API:
MLX-LM, vLLM, Ollama, llama.cpp, OpenAI itself, and any enterprise
endpoint that accepts the same request/response schema.

Zero translation — the payload is forwarded as-is with only
authentication headers and URL routing applied.

Configurable options (via provider config):
  - auth_scheme: "bearer" (default) or "basic" — controls Authorization header
  - endpoint_path: custom path override (default: auto-detect from base_url)
"""

import asyncio

import httpx


class OpenAIStrategy:
    """Transparent proxy for OpenAI-compatible backends."""

    def __init__(self, provider_config, client: httpx.AsyncClient):
        self.name = provider_config.get("name", "openai")
        self.base_url = provider_config["base_url"]
        self.api_key = provider_config.get("api_key", "")
        self.settings = provider_config.get("settings", {})
        self.auth_scheme = provider_config.get("auth_scheme", "bearer")
        self.endpoint_path = provider_config.get("endpoint_path", "")
        self.verify_ssl = provider_config.get("verify_ssl", True)
        self.client = client
        self.semaphore = asyncio.Semaphore(20)

    def _build_url(self) -> str:
        """Construct the full request URL from base_url and endpoint_path."""
        base = self.base_url.rstrip("/")

        # If an explicit endpoint_path is configured, use it directly
        if self.endpoint_path:
            path = self.endpoint_path.lstrip("/")
            return f"{base}/{path}"

        # Default behavior: append /chat/completions, avoiding /v1 duplication
        if base.endswith("/v1"):
            return f"{base}/chat/completions"
        return f"{base}/v1/chat/completions"

    def _build_auth_header(self) -> dict:
        """Build the Authorization header based on configured auth_scheme."""
        if not self.api_key:
            return {}
        scheme = self.auth_scheme.lower()
        if scheme == "basic":
            return {"Authorization": f"basic {self.api_key}"}
        # Default: bearer
        return {"Authorization": f"Bearer {self.api_key}"}

    async def send(self, payload: dict) -> tuple:
        """Forward payload to OpenAI-compatible backend asynchronously.

        Returns:
            (response_dict, status_code) — response is passed through unchanged.
        """
        headers = {
            "Content-Type": "application/json",
            "accept": "application/json",
        }
        headers.update(self._build_auth_header())

        url = self._build_url()

        # Inject provider-level settings into the payload (e.g., conversation_guid)
        # These are defaults — the payload itself takes precedence if already set.
        for key, value in self.settings.items():
            if key not in payload:
                payload[key] = value

        try:
            async with self.semaphore:
                if not self.verify_ssl:
                    # Create a separate client for non-verified requests
                    async with httpx.AsyncClient(verify=False, timeout=self.client.timeout) as unverified:
                        res = await unverified.post(url, json=payload, headers=headers)
                else:
                    res = await self.client.post(url, json=payload, headers=headers)
            return res.json(), res.status_code
        except Exception as e:
            return {"error": f"OpenAI strategy request failed: {str(e)}"}, 502
