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
import requests

import config


class LLMAgent:
    def __init__(self, agent_id, persona, provider="", model=""):
        self.agent_id = agent_id
        self.persona = persona
        self.provider = provider or config.CONFIG.default_provider
        self.model = model or config.DEFAULT_MODEL
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
        # Strip markdown code fences
        text = re.sub(r'^```(?:json)?\s*', '', text, flags=re.MULTILINE)
        text = re.sub(r'```\s*$', '', text, flags=re.MULTILINE)
        text = text.strip()
        # Remove single-line // comments
        text = re.sub(r'//[^\n]*', '', text)
        # Remove trailing commas before } or ]
        text = re.sub(r',\s*([}\]])', r'\1', text)
        # Fix Python-style booleans/None
        text = re.sub(r'\bTrue\b', 'true', text)
        text = re.sub(r'\bFalse\b', 'false', text)
        text = re.sub(r'\bNone\b', 'null', text)
        # Replace single quotes with double quotes (handles JSON keys/values)
        # This is a simple heuristic: replace ' with " when it looks like JSON structure
        text = re.sub(r"(?<=[:,\[{\s])\s*'", ' "', text)
        text = re.sub(r"'(?=\s*[:,\]}\s])", '"', text)
        # Handle leading single quote at start of string
        if text.startswith("{'"):
            text = '{"' + text[2:]
        return text

    def _format_history(self, raw_messages):
        """Tailors the history for this specific agent's perspective.

        Converts internal message format → OpenAI Chat Completions messages.
        """
        formatted = []
        for msg in raw_messages:
            actor = msg['actor_id']
            content = msg['content']

            # If I sent it, role is "assistant". If anyone else sent it, "user".
            role = "assistant" if actor == self.agent_id else "user"

            # Formatting for the prompt
            if actor == "Moderator":
                text = f"[Moderator Note]: {content}"
            elif role == "user":
                text = f"{actor}: {content}"
            else:
                text = content  # Assistant role doesn't need prefix

            formatted.append({"role": role, "content": text})
        return formatted

    async def _call_gateway(self, sys_instr, history, is_json=False,
                             tools=None, tool_choice=None,
                             extra_turns=None):
        """Call the LLM gateway with OpenAI Chat Completions format.

        Args:
            sys_instr: System instruction string.
            history: Raw message history.
            is_json: If True, request JSON response format.
            tools: OpenAI-format tool definitions.
            tool_choice: OpenAI tool_choice setting ("auto", "required", etc.)
            extra_turns: Additional messages for multi-turn tool calling.
        """
        messages = [{"role": "system", "content": sys_instr}]
        messages.extend(self._format_history(history))

        # Append structured tool calling turns if present
        if extra_turns:
            messages.extend(extra_turns)

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
                    requests.post, self.gateway_url, json=payload, timeout=120
                )
                if res.status_code == 200:
                    return res.json()
                elif res.status_code in [500, 502, 503, 504] and attempt < 2:
                    wait = (attempt + 1) * 2
                    print(f"⚠️ [{self.agent_id}] Gateway error {res.status_code}. Retrying in {wait}s...")
                    await asyncio.sleep(wait)
                    continue
                else:
                    print(f"❌ [{self.agent_id}] API Error {res.status_code}: {res.text}")
                    return None
            except Exception as e:
                if attempt < 2:
                    wait = (attempt + 1) * 2
                    print(f"⚠️ [{self.agent_id}] Gateway call failed: {e}. Retrying in {wait}s...")
                    await asyncio.sleep(wait)
                    continue
                print(f"❌ [{self.agent_id}] Gateway call failed after 3 attempts: {e}")
                return None

    def _extract_text(self, response) -> str | None:
        """Extract text content from an OpenAI Chat Completions response."""
        if not response:
            return None
        try:
            return response['choices'][0]['message'].get('content')
        except (KeyError, IndexError):
            return None

    def _extract_function_calls(self, response) -> list[dict]:
        """Extract tool calls from an OpenAI Chat Completions response."""
        if not response:
            return []
        try:
            tool_calls = response['choices'][0]['message'].get('tool_calls', [])
            return tool_calls
        except (KeyError, IndexError):
            return []

    async def _vote(self, history, roster, previous_votes=None):
        instr = (
            f"You are {self.agent_id}. Persona: {self.persona}.\n"
            "You are in a voting phase. A new message has arrived in the chat.\n"
            "You must review the history and vote for the ONE agent who is best suited to respond.\n"
            f"The team roster and roles are: {roster}.\n"
            "CRITICAL: You must only vote for one of the primary agents listed in the roster or the vote is invalidated.\n"
            "Please collaborate together in an agile format, leveraging each others unique abilities and tools.\n"
            "If someone specifically addressed an agent, vote for them. Otherwise, vote based on merit.\n"
            "You must also evaluate if the overall task is completely finished.\n"
            "Respond ONLY in valid JSON with the following keys:\n"
            "- 'vote' (string: name of the agent)\n"
            "- 'reason' (string: one sentence reason for the vote)\n"
            "- 'is_done' (boolean: true if the job is complete, false otherwise)\n"
            "- 'done_reason' (string: one sentence explaining why the job is or isn't done)."
        )
        if previous_votes:
            instr += (
                "\n\n### DEBATE MODE ###\n"
                f"Previous round results:\n{previous_votes}\n"
                "You are in a tie-breaker round. Read the reasoning from other agents above. "
                "Acknowledge their points. You must now either defend your original choice with stronger logic or "
                "concede and vote for another agent if their reasoning was more compelling. "
                "We must reach a consensus."
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
        instr = (
            f"You are {self.agent_id}, participating in a multi-agent chat. "
            f"Persona: {self.persona}. "
            "Respond to the conversation if appropriate. "
            "If no action is needed or you just spoke, respond with [WAIT].\n\n"
            "CRITICAL: If the Moderator just announced you won the election, you SHOULD contribute. "
            "If you are waiting for someone else to finish research, acknowledge it and explain what you expect from them. "
            "Do not just [WAIT] if you were specifically chosen to speak."
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
        instr = (
            f"You are {self.agent_id}, participating in a multi-agent software engineering team. "
            f"Persona: {self.persona}. "
            f"You have access to tools: [{tool_names}]. "
            "Use them when you need to take action — read files, write code, run commands, "
            "manage the project board, or run tests. "
            "If no action is needed, respond with text explaining why.\n\n"
            "CRITICAL: If the Moderator just announced you won the election, you SHOULD contribute. "
            "Do not skip your turn if you were specifically chosen to speak."
        )

        # Build OpenAI function tool definitions
        tools = []
        for tool in available_tools:
            schema = tool.get_schema()
            tools.append({
                "type": "function",
                "function": {
                    "name": tool.name,
                    "description": tool.description,
                    "parameters": schema,
                }
            })

        # Force function calling — model MUST emit tool_calls
        raw_response = await self._call_gateway(
            instr, history,
            tools=tools,
            tool_choice="required",
            extra_turns=self._api_turns,
        )

        text = self._extract_text(raw_response)
        raw_tool_calls = self._extract_function_calls(raw_response)

        # Preserve the model's response as an assistant message for multi-turn
        if raw_response and raw_tool_calls:
            try:
                assistant_msg = raw_response['choices'][0]['message']
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
            calls.append({
                "name": fn.get("name", ""),
                "args": args,
                "id": tc.get("id", ""),
            })

        return text, calls

    async def _send_function_responses(self, history, function_responses,
                                        available_tools):
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

            self._api_turns.append({
                "role": "tool",
                "tool_call_id": fr["id"],
                "name": fr["name"],
                "content": result_content,
            })

        instr = (
            f"You are {self.agent_id}. Persona: {self.persona}. "
            "You executed tools and the results are provided. "
            "Based on these results, decide your next action: "
            "call more tools if needed, or provide a text summary of what you accomplished."
        )

        tools = []
        for tool in available_tools:
            schema = tool.get_schema()
            tools.append({
                "type": "function",
                "function": {
                    "name": tool.name,
                    "description": tool.description,
                    "parameters": schema,
                }
            })

        # Use auto mode for follow-up — model can choose text OR more tool calls
        raw_response = await self._call_gateway(
            instr, history,
            tools=tools,
            tool_choice="auto",
            extra_turns=self._api_turns,
        )

        text = self._extract_text(raw_response)
        raw_tool_calls = self._extract_function_calls(raw_response)

        # If more function calls, preserve this turn too
        if raw_response and raw_tool_calls:
            try:
                assistant_msg = raw_response['choices'][0]['message']
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
            calls.append({
                "name": fn.get("name", ""),
                "args": args,
                "id": tc.get("id", ""),
            })

        return text, calls

    async def _reflect(self, history):
        """Post-tool reflection — let the agent process tool results."""
        instr = (
            f"You are {self.agent_id}. Persona: {self.persona}. "
            "You just executed some tools. The results are in the conversation history above. "
            "Summarize what happened and decide your next step: "
            "respond with your findings, take more tool actions, or say [WAIT] if done."
        )

        raw_response = await self._call_gateway(instr, history)
        return self._extract_text(raw_response)


# Backward-compatible alias
GeminiAgent = LLMAgent
