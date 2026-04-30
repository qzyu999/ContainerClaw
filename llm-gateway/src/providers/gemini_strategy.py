"""
Gemini translation strategy.

Translates between OpenAI Chat Completions wire format and Google's
Gemini generateContent API. This is the ONLY place Gemini-specific
logic exists in the system.

Translation covers:
- messages → contents/parts (with system_instruction extraction)
- tools → function_declarations
- Response: candidates → choices (text + tool_calls)
"""

import asyncio

import httpx
from tenacity import (
    retry,
    retry_if_result,
    stop_after_attempt,
    wait_exponential,
    wait_random,
)


def is_transient_error(exception):
    if isinstance(exception, httpx.HTTPStatusError):
        # Retry on Rate Limit (429) or Server Errors (5xx)
        return exception.response.status_code in {429, 500, 502, 503, 504}
    # Retry on network-level failures (DNS, Connection Refused, etc.)
    return isinstance(exception, httpx.TransportError)


class GeminiStrategy:
    """Translates OpenAI wire format ↔ Gemini generateContent format."""

    def __init__(self, provider_config, client: httpx.AsyncClient):
        self.name = provider_config.get("name", "gemini")
        self.base_url = provider_config["base_url"]
        self.api_key = provider_config.get("api_key", "")
        self.settings = provider_config.get("settings", {})
        self.client = client
        # Cap concurrent outbound requests per upstream provider to avoid rate-limit bans
        self.semaphore = asyncio.Semaphore(20)

    @retry(
        wait=wait_exponential(multiplier=1, max=10) + wait_random(0, 1),
        stop=stop_after_attempt(3),
        retry=retry_if_result(is_transient_error),
        reraise=True,
    )
    async def _post_with_retry(self, url: str, json_payload: dict) -> httpx.Response:
        """Execute post request under a concurrency semaphore with status-aware retry."""
        async with self.semaphore:
            res = await self.client.post(url, json=json_payload)
            # Raise an HTTPStatusError if it warrants a retry or is an error
            # so `is_transient_error` logic can decide if it should retry
            if res.status_code >= 400:
                res.raise_for_status()
            return res

    async def send(self, payload: dict) -> tuple:
        """Translate OpenAI payload → Gemini, call API, translate response back.

        Returns:
            (openai_response_dict, status_code)
        """
        model = payload.get("model", "gemini-3-flash-preview")
        gemini_payload = self._to_gemini(payload, model)

        url = f"{self.base_url}/models/{model}:generateContent?key={self.api_key}"

        try:
            res = await self._post_with_retry(url, gemini_payload)
            if res.status_code == 200:
                return self._from_gemini(res.json(), model), 200
            else:
                return res.json(), res.status_code
        except Exception as e:
            return {"error": f"Gemini strategy request failed: {str(e)}"}, 502

    # ── OpenAI → Gemini Translation ──────────────────────────────

    def _to_gemini(self, openai_payload: dict, model: str) -> dict:
        """Convert OpenAI Chat Completions request → Gemini generateContent."""
        contents = []
        system_instructions = []

        current_tool_parts = []

        for msg in openai_payload.get("messages", []):
            role = msg.get("role", "user")
            content = msg.get("content", "")

            if role == "system":
                system_instructions.append(content)
                continue

            if role == "tool":
                # OpenAI tool result → Gemini functionResponse part
                current_tool_parts.append(
                    {
                        "functionResponse": {
                            "name": msg.get("name", ""),
                            "response": {"result": content},
                            "id": msg.get("tool_call_id", ""),
                        }
                    }
                )
                continue

            # Flush accumulated tool parts before adding next message
            if current_tool_parts:
                contents.append({"role": "user", "parts": current_tool_parts})
                current_tool_parts = []

            if role == "assistant":
                gemini_role = "model"

                # If we have preserved raw Gemini parts, use them directly!
                # This ensures `thought_signature` and `thought` are perfectly echoed.
                if "_gemini_parts" in msg:
                    contents.append(
                        {"role": gemini_role, "parts": msg["_gemini_parts"]}
                    )
                    continue

                # Check for tool_calls in assistant message (cross-provider mapping fallback)
                tool_calls = msg.get("tool_calls", [])
                if tool_calls:
                    parts = []
                    if content:
                        parts.append({"text": content})
                    for tc in tool_calls:
                        fn = tc.get("function", {})
                        import json

                        try:
                            args = json.loads(fn.get("arguments", "{}"))
                        except (json.JSONDecodeError, TypeError):
                            args = {}
                        parts.append(
                            {
                                "functionCall": {
                                    "name": fn.get("name", ""),
                                    "args": args,
                                    "id": tc.get("id", ""),
                                }
                            }
                        )
                    contents.append({"role": gemini_role, "parts": parts})
                    continue
            else:
                gemini_role = "user"

            contents.append({"role": gemini_role, "parts": [{"text": content}]})

        # Flush any trailing tool parts
        if current_tool_parts:
            contents.append({"role": "user", "parts": current_tool_parts})

        gemini = {"contents": contents}

        if system_instructions:
            gemini["system_instruction"] = {
                "parts": [{"text": "\n\n".join(system_instructions)}]
            }

        # Generation config
        gen_config = {}
        if openai_payload.get("response_format", {}).get("type") == "json_object":
            gen_config["response_mime_type"] = "application/json"
        if "max_tokens" in openai_payload:
            gen_config["max_output_tokens"] = openai_payload["max_tokens"]
        if "temperature" in openai_payload:
            gen_config["temperature"] = openai_payload["temperature"]

        # Apply Gemini-specific settings (thinking config, etc.)
        if "gemini-3" in model:
            thinking_level = self.settings.get("thinking_level", "HIGH")
            if "thinking_config" not in gen_config:
                gen_config["thinking_config"] = {"thinking_level": thinking_level}
            gen_config.setdefault(
                "max_output_tokens", self.settings.get("max_output_tokens", 8192)
            )

        if gen_config:
            gemini["generationConfig"] = gen_config

        # Tools translation
        if openai_payload.get("tools"):
            gemini["tools"] = self._convert_tools(openai_payload["tools"])

        # Tool choice translation
        tool_choice = openai_payload.get("tool_choice")
        if tool_choice:
            gemini["tool_config"] = self._convert_tool_choice(tool_choice)

        return gemini

    def _convert_tools(self, openai_tools: list) -> list:
        """OpenAI tools format → Gemini function_declarations."""
        declarations = []
        for tool in openai_tools:
            if tool.get("type") == "function":
                fn = tool.get("function", {})
                declarations.append(
                    {
                        "name": fn.get("name", ""),
                        "description": fn.get("description", ""),
                        "parameters": fn.get("parameters", {}),
                    }
                )
        return [{"function_declarations": declarations}] if declarations else []

    def _convert_tool_choice(self, tool_choice) -> dict:
        """OpenAI tool_choice → Gemini function_calling_config."""
        if tool_choice == "required":
            return {"function_calling_config": {"mode": "ANY"}}
        elif tool_choice == "none":
            return {"function_calling_config": {"mode": "NONE"}}
        elif tool_choice == "auto":
            return {"function_calling_config": {"mode": "AUTO"}}
        elif isinstance(tool_choice, dict):
            # Force specific function
            fn_name = tool_choice.get("function", {}).get("name", "")
            return {
                "function_calling_config": {
                    "mode": "ANY",
                    "allowed_function_names": [fn_name],
                }
            }
        return {"function_calling_config": {"mode": "AUTO"}}

    # ── Gemini → OpenAI Translation ──────────────────────────────

    def _from_gemini(self, gemini_response: dict, model: str) -> dict:
        """Convert Gemini generateContent response → OpenAI Chat Completions."""
        try:
            candidate = gemini_response["candidates"][0]
            parts = candidate.get("content", {}).get("parts", [])
        except (KeyError, IndexError):
            return {
                "error": "Failed to parse Gemini response",
                "raw": gemini_response,
            }

        # Extract text and function calls
        text_parts = []
        tool_calls = []

        for part in parts:
            if "text" in part:
                text_parts.append(part["text"])
            elif "functionCall" in part:
                fc = part["functionCall"]
                import json

                tool_calls.append(
                    {
                        "id": fc.get("id", ""),
                        "type": "function",
                        "function": {
                            "name": fc.get("name", ""),
                            "arguments": json.dumps(fc.get("args", {})),
                        },
                    }
                )

        text = "\n".join(text_parts).strip() if text_parts else None

        # Build OpenAI-format message
        message = {"role": "assistant"}
        if text:
            message["content"] = text
        else:
            message["content"] = None
        if tool_calls:
            message["tool_calls"] = tool_calls

        # Required to pass `thought` and `thought_signature` parts back to Gemini in identical form
        message["_gemini_parts"] = parts

        finish_reason = "stop"
        if tool_calls:
            finish_reason = "tool_calls"

        return {
            "id": f"gemini-{model}",
            "object": "chat.completion",
            "model": model,
            "choices": [
                {
                    "index": 0,
                    "message": message,
                    "finish_reason": finish_reason,
                }
            ],
            "usage": gemini_response.get("usageMetadata", {}),
        }
