"""
LLMAgent: Provider-agnostic agent for the ContainerClaw multi-agent system.

Each agent has a persona, communicates via the LLM gateway using the
OpenAI Chat Completions wire format, and supports:
- Voting in elections (_vote)
- Text-only thinking (_think)
- Tool-augmented thinking via function calling (_think_with_tools)
- Multi-turn tool calling protocol (_send_function_responses)

The gateway handles translation to provider-specific formats (e.g., Gemini).
This agent speaks ONLY the OpenAI Chat Completions wire protocol.
"""

import asyncio
import json
import re

import config
import requests


class LLMAgent:
    def __init__(self, agent_id, persona, provider="", model="", spine=""):
        self.agent_id = agent_id
        self.persona = persona
        self.provider = provider or config.CONFIG.default_provider
        self.model = model or config.DEFAULT_MODEL
        self.spine = spine
        self.anchor_text = ""
        self.roster_str = ""
        self.session_context = ""  # Injected per-session context block
        self.gateway_url = f"{config.LLM_GATEWAY_URL}/v1/chat/completions"
        self._api_turns = []  # Structured turns for multi-turn tool calling

    @staticmethod
    def _sanitize_json(text: str) -> str:
        """Best-effort cleanup of LLM-generated JSON.

        Local models (Qwen, Llama) often produce:
        - Markdown code fences around JSON
        - Trailing commas before } or ]
        - Single quotes instead of double quotes
        - JavaScript-style comments
        - Unquoted True/False/None (Python literals)
        """
        if not text:
            return text

        # --- FIX: Defensive Parsing Strategy ---
        # 1. Broadly extract anything between markdown code fences if they exist
        match_fence = re.search(r"```(?:json)?\s*(.*?)\s*```", text, re.DOTALL)
        if match_fence:
            text = match_fence.group(1)

        # 2. Heuristic fallback: if it doesn't look like JSON yet, find the first '{' and last '}'
        # This handles models that say "Here is the JSON: { ... }" without fences.
        if not text.strip().startswith("{"):
            match_object = re.search(r"(\{.*\})", text, re.DOTALL)
            if match_object:
                text = match_object.group(1)

        text = text.strip()

        # 3. Traditional cleanup for common LLM syntax errors
        # Remove single-line // comments
        text = re.sub(r"//[^\n]*", "", text)
        # Remove trailing commas before } or ]
        text = re.sub(r",\s*([}\]])", r"\1", text)
        # Fix Python-style booleans/None
        text = re.sub(r"\bTrue\b", "true", text)
        text = re.sub(r"\bFalse\b", "false", text)
        text = re.sub(r"\bNone\b", "null", text)
        # Replace single quotes with double quotes (handles JSON keys/values)
        # Only replace if it looks like a key or a string value
        text = re.sub(r"(?<=[:,\[{\s])\s*'", ' "', text)
        text = re.sub(r"'(?=\s*[:,\]}\s])", '"', text)

        # Handle leading single quote at start of string
        if text.startswith("{'"):
            text = '{"' + text[2:]
        return text

    async def _call_gateway(
        self,
        sys_instr,
        history,
        is_json=False,
        tools=None,
        tool_choice=None,
        extra_turns=None,
    ):
        """Call the LLM gateway with OpenAI Chat Completions format.

        Args:
            sys_instr: System instruction string.
            history: Raw message history.
            is_json: If True, request JSON response format.
            tools: OpenAI-format tool definitions.
            tool_choice: OpenAI tool_choice setting ("auto", "required", etc.)
            extra_turns: Additional messages for multi-turn tool calling.
        """
        from shared.context_builder import ContextBuilder

        sys_instr_with_spine = (
            (self.spine + "\n\n" + sys_instr)
            if hasattr(self, "spine") and self.spine
            else sys_instr
        )
        # Inject session context between spine and instruction
        session_ctx = getattr(self, "session_context", "")
        if session_ctx:
            sys_instr_with_spine = sys_instr_with_spine + "\n\n" + session_ctx
        anchor_text = getattr(self, "anchor_text", "")

        messages = ContextBuilder.build_payload(
            raw_messages=history,
            config=config.CONFIG,
            actor_id=self.agent_id,
            system_prompt=sys_instr_with_spine,
            extra_turns=extra_turns,
            anchor_text=anchor_text,
            is_json=is_json,
        )

        payload = {
            "model": self.model,
            "messages": messages,
            "provider": self.provider,
        }

        if is_json:
            payload["response_format"] = {"type": "json_object"}
        if tools:
            payload["tools"] = tools
        if tool_choice:
            payload["tool_choice"] = tool_choice

        for attempt in range(3):
            try:
                res = await asyncio.to_thread(
                    requests.post,
                    self.gateway_url,
                    json=payload,
                    timeout=config.CONFIG.llm_timeout_s,
                )
                if res.status_code == 200:
                    return res.json()
                elif res.status_code in [500, 502, 503, 504] and attempt < 2:
                    wait = (attempt + 1) * 2
                    print(
                        f"⚠️ [{self.agent_id}] Gateway error {res.status_code}. Retrying in {wait}s..."
                    )
                    await asyncio.sleep(wait)
                    continue
                else:
                    print(
                        f"❌ [{self.agent_id}] API Error {res.status_code}: {res.text}"
                    )
                    return None
            except Exception as e:
                if attempt < 2:
                    wait = (attempt + 1) * 2
                    print(
                        f"⚠️ [{self.agent_id}] Gateway call failed: {e}. Retrying in {wait}s..."
                    )
                    await asyncio.sleep(wait)
                    continue
                print(f"❌ [{self.agent_id}] Gateway call failed after 3 attempts: {e}")
                return None

    def _extract_text(self, response) -> str | None:
        """Extract text content from an OpenAI Chat Completions response."""
        if not response:
            return None
        try:
            return response["choices"][0]["message"].get("content")
        except (KeyError, IndexError):
            return None

    def _extract_function_calls(self, response) -> list[dict]:
        """Extract tool calls from an OpenAI Chat Completions response."""
        if not response:
            return []
        try:
            tool_calls = response["choices"][0]["message"].get("tool_calls", [])
            return tool_calls
        except (KeyError, IndexError):
            return []

    async def _vote(self, history, roster, previous_votes=None):
        instr = config.CONFIG.prompts.vote.format(
            agent_id=self.agent_id, persona=self.persona, roster=roster
        )
        if previous_votes:
            instr += "\n\n" + config.CONFIG.prompts.vote_debate.format(
                previous_votes=previous_votes
            )

        try:
            raw_response = await self._call_gateway(instr, history, is_json=True)
            raw_text = self._extract_text(raw_response)
            if raw_text is None:
                return None
            sanitized = self._sanitize_json(raw_text)
            return json.loads(sanitized)
        except json.JSONDecodeError as e:
            print(f"❌ [{self.agent_id}] Vote parse failed: {e}")
            print(f"   Raw text: {repr(raw_text[:200] if raw_text else None)}")
            print(f"   Sanitized: {repr(sanitized[:200] if sanitized else None)}")
            return None
        except Exception as e:
            print(f"❌ [{self.agent_id}] Vote failed: {e}")
            return None

    async def _think(self, history):
        """Pure-text thinking — no tool use. Backward-compatible fallback."""
        instr = config.CONFIG.prompts.think.format(
            agent_id=self.agent_id, persona=self.persona, roster=self.roster_str
        )
        raw_response = await self._call_gateway(instr, history)
        return self._extract_text(raw_response)

    async def _think_with_tools(self, history, available_tools):
        """Enhanced thinking with OpenAI function calling protocol.

        Uses tool_choice="required" to force structured tool_calls output
        when tools are available. Returns (text, function_calls) where
        function_calls are in normalized format.
        """
        tool_names = ", ".join(t.name for t in available_tools)
        instr = config.CONFIG.prompts.think_with_tools.format(
            agent_id=self.agent_id,
            persona=self.persona,
            tool_names=tool_names,
            roster=self.roster_str,
        )

        # Build OpenAI function tool definitions
        tools = []
        for tool in available_tools:
            schema = tool.get_schema()
            tools.append(
                {
                    "type": "function",
                    "function": {
                        "name": tool.name,
                        "description": tool.description,
                        "parameters": schema,
                    },
                }
            )

        # Force function calling — model MUST emit tool_calls
        raw_response = await self._call_gateway(
            instr,
            history,
            tools=tools,
            tool_choice="required",
            extra_turns=self._api_turns,
        )

        text = self._extract_text(raw_response)
        raw_tool_calls = self._extract_function_calls(raw_response)

        # Preserve the model's response as an assistant message for multi-turn
        if raw_response and raw_tool_calls:
            try:
                assistant_msg = raw_response["choices"][0]["message"]
                self._api_turns.append(assistant_msg)
            except (KeyError, IndexError):
                pass

        # Normalize function calls to internal format
        calls = []
        for tc in raw_tool_calls:
            fn = tc.get("function", {})
            try:
                args = json.loads(fn.get("arguments", "{}"))
            except (json.JSONDecodeError, TypeError):
                args = {}
            calls.append(
                {
                    "name": fn.get("name", ""),
                    "args": args,
                    "id": tc.get("id", ""),
                }
            )

        return text, calls

    async def _send_function_responses(
        self, history, function_responses, available_tools
    ):
        """Send function execution results back to the model.

        Implements the OpenAI multi-turn tool calling protocol:
        append tool-role messages and request the model's next action.

        Args:
            history: The shared all_messages context (text format).
            function_responses: List of dicts with keys:
                'name' (str), 'response' (dict), 'id' (str)
            available_tools: List of Tool objects (for continued tool use).

        Returns:
            tuple: (text_response, function_calls) — same shape as _think_with_tools().
        """
        # Build tool result messages (OpenAI format)
        for fr in function_responses:
            result_content = fr["response"]
            if isinstance(result_content, dict):
                result_content = json.dumps(result_content)
            elif not isinstance(result_content, str):
                result_content = str(result_content)

            self._api_turns.append(
                {
                    "role": "tool",
                    "tool_call_id": fr["id"],
                    "name": fr["name"],
                    "content": result_content,
                }
            )

        instr = config.CONFIG.prompts.send_function_responses.format(
            agent_id=self.agent_id, persona=self.persona, roster=self.roster_str
        )

        tools = []
        for tool in available_tools:
            schema = tool.get_schema()
            tools.append(
                {
                    "type": "function",
                    "function": {
                        "name": tool.name,
                        "description": tool.description,
                        "parameters": schema,
                    },
                }
            )

        # Use auto mode for follow-up — model can choose text OR more tool calls
        raw_response = await self._call_gateway(
            instr,
            history,
            tools=tools,
            tool_choice="auto",
            extra_turns=self._api_turns,
        )

        text = self._extract_text(raw_response)
        raw_tool_calls = self._extract_function_calls(raw_response)

        # If more function calls, preserve this turn too
        if raw_response and raw_tool_calls:
            try:
                assistant_msg = raw_response["choices"][0]["message"]
                self._api_turns.append(assistant_msg)
            except (KeyError, IndexError):
                pass

        calls = []
        for tc in raw_tool_calls:
            fn = tc.get("function", {})
            try:
                args = json.loads(fn.get("arguments", "{}"))
            except (json.JSONDecodeError, TypeError):
                args = {}
            calls.append(
                {
                    "name": fn.get("name", ""),
                    "args": args,
                    "id": tc.get("id", ""),
                }
            )

        return text, calls

    async def _reflect(self, history):
        """Post-tool reflection — let the agent process tool results."""
        instr = config.CONFIG.prompts.reflect.format(
            agent_id=self.agent_id, persona=self.persona, roster=self.roster_str
        )

        raw_response = await self._call_gateway(instr, history)
        return self._extract_text(raw_response)


# Backward-compatible alias
GeminiAgent = LLMAgent
