"""
Gemini translation strategy.

Translates between OpenAI Chat Completions wire format and Google's
Gemini generateContent API. This is the ONLY place Gemini-specific
logic exists in the system.

Translation covers:
- messages → contents/parts (with system_instruction extraction)
- tools → function_declarations
- tool_choice → function_calling_config
- Response: candidates → choices (text + tool_calls)
"""

import requests
import certifi
from urllib3.util.retry import Retry
from requests.adapters import HTTPAdapter


class GeminiStrategy:
    """Translates OpenAI wire format ↔ Gemini generateContent format."""

    def __init__(self, provider_config):
        self.name = provider_config.get("name", "gemini")
        self.base_url = provider_config["base_url"]
        self.api_key = provider_config.get("api_key", "")
        self.settings = provider_config.get("settings", {})
        self.session = self._build_session()

    def _build_session(self):
        """Build a resilient requests session with connection pooling."""
        session = requests.Session()
        retry_strategy = Retry(
            total=3,
            backoff_factor=1,
            status_forcelist=[429, 500, 502, 503, 504],
            allowed_methods=["POST"],
            raise_on_status=False,
        )
        adapter = HTTPAdapter(
            max_retries=retry_strategy,
            pool_connections=10,
            pool_maxsize=10,
        )
        session.mount("https://", adapter)
        session.mount("http://", adapter)
        session.verify = certifi.where()
        return session

    def send(self, payload: dict) -> tuple:
        """Translate OpenAI payload → Gemini, call API, translate response back.

        Returns:
            (openai_response_dict, status_code)
        """
        model = payload.get("model", "gemini-3-flash-preview")
        gemini_payload = self._to_gemini(payload, model)

        url = f"{self.base_url}/models/{model}:generateContent?key={self.api_key}"

        try:
            res = self.session.post(url, json=gemini_payload, timeout=300)
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
        system_instruction = ""

        for msg in openai_payload.get("messages", []):
            role = msg.get("role", "user")
            content = msg.get("content", "")

            if role == "system":
                system_instruction = content
                continue

            if role == "tool":
                # OpenAI tool result → Gemini functionResponse
                contents.append({
                    "role": "user",
                    "parts": [{
                        "functionResponse": {
                            "name": msg.get("name", ""),
                            "response": {"result": content},
                            "id": msg.get("tool_call_id", ""),
                        }
                    }]
                })
                continue

            if role == "assistant":
                gemini_role = "model"
                # Check for tool_calls in assistant message
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
                        parts.append({
                            "functionCall": {
                                "name": fn.get("name", ""),
                                "args": args,
                                "id": tc.get("id", ""),
                            }
                        })
                    contents.append({"role": gemini_role, "parts": parts})
                    continue
            else:
                gemini_role = "user"

            contents.append({
                "role": gemini_role,
                "parts": [{"text": content}]
            })

        gemini = {"contents": contents}

        if system_instruction:
            gemini["system_instruction"] = {"parts": [{"text": system_instruction}]}

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
                "max_output_tokens",
                self.settings.get("max_output_tokens", 8192)
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
                declarations.append({
                    "name": fn.get("name", ""),
                    "description": fn.get("description", ""),
                    "parameters": fn.get("parameters", {}),
                })
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
                tool_calls.append({
                    "id": fc.get("id", ""),
                    "type": "function",
                    "function": {
                        "name": fc.get("name", ""),
                        "arguments": json.dumps(fc.get("args", {})),
                    }
                })

        text = "\n".join(text_parts).strip() if text_parts else None

        # Build OpenAI-format message
        message = {"role": "assistant"}
        if text:
            message["content"] = text
        else:
            message["content"] = None
        if tool_calls:
            message["tool_calls"] = tool_calls

        finish_reason = "stop"
        if tool_calls:
            finish_reason = "tool_calls"

        return {
            "id": f"gemini-{model}",
            "object": "chat.completion",
            "model": model,
            "choices": [{
                "index": 0,
                "message": message,
                "finish_reason": finish_reason,
            }],
            "usage": gemini_response.get("usageMetadata", {}),
        }
