"""
GeminiAgent: LLM-backed agent for the ContainerClaw multi-agent system.

Each agent has a persona, communicates via the LLM gateway, and supports:
- Voting in elections (_vote)
- Text-only thinking (_think)
- Tool-augmented thinking via Gemini function calling (_think_with_tools)
- Multi-turn function calling protocol (_send_function_responses)
"""

import asyncio
import json
import requests

import config


class GeminiAgent:
    def __init__(self, agent_id, persona, api_key):
        self.agent_id = agent_id
        self.persona = persona
        self.api_key = api_key
        self.gateway_url = f"{config.LLM_GATEWAY_URL}/v1/chat/completions"
        self.model = config.DEFAULT_MODEL
        self._api_turns = []  # Structured turns for Gemini function calling protocol

    def _format_history(self, raw_messages):
        """Tailors the history for this specific agent's perspective."""
        formatted = []
        for msg in raw_messages:
            actor = msg['actor_id']
            content = msg['content']
            
            # If I sent it, role is "model". If anyone else sent it, "user".
            role = "model" if actor == self.agent_id else "user"
            
            # Formatting for the prompt
            if actor == "Moderator":
                text = f"[Moderator Note]: {content}"
            elif role == "user":
                text = f"{actor}: {content}"
            else:
                text = content  # Model role doesn't need prefix
            
            formatted.append({"role": role, "parts": [{"text": text}]})
        return formatted

    async def _call_gateway(self, sys_instr, history, is_json=False, 
                             tools=None, tool_config=None, 
                             extra_turns=None):
        contents = self._format_history(history)
        # Append structured API turns (functionCall/functionResponse) if present
        if extra_turns:
            contents.extend(extra_turns)

        payload = {
            "system_instruction": sys_instr,  # Raw string, Gateway wraps it
            "contents": contents,
            "generationConfig": {"response_mime_type": "application/json"} if is_json else {}
        }
        if tools:
            payload["tools"] = tools
        if tool_config:
            payload["tool_config"] = tool_config
        for attempt in range(3):
            try:
                res = await asyncio.to_thread(
                    requests.post, self.gateway_url, json=payload, timeout=60
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
        """Extract text content from a Gemini API response."""
        if not response:
            return None
        try:
            parts = response['candidates'][0]['content']['parts']
            text_parts = [p['text'] for p in parts if 'text' in p]
            return "\n".join(text_parts).strip() if text_parts else None
        except (KeyError, IndexError):
            return None

    def _extract_function_calls(self, response) -> list[dict]:
        """Extract function call parts from a Gemini API response."""
        if not response:
            return []
        try:
            parts = response['candidates'][0]['content']['parts']
            return [p['functionCall'] for p in parts if 'functionCall' in p]
        except (KeyError, IndexError):
            return []

    async def _vote(self, history, roster, previous_votes=None):
        instr = (
            f"You are {self.agent_id}. Persona: {self.persona}.\n"
            f"You are in a voting phase. A new message has arrived in the chat.\n"
            f"You must review the history and vote for the ONE agent who is best suited to respond.\n"
            f"The team roster and roles are: {roster}.\n"
            f"Please collaborate together in an agile format, leveraging each others unique abilities and tools.\n"
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
            return json.loads(raw_text)
        except Exception as e:
            print(f"❌ [{self.agent_id}] Vote parse failed: {e}")
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
        """Enhanced thinking with Gemini native function calling protocol.

        Uses mode=ANY to force structured functionCall output when tools
        are available. Returns (text, function_calls) where function_calls
        include the 'id' field required for functionResponse mapping.
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

        # Build Gemini function declarations
        tool_declarations = [{
            "function_declarations": [
                {
                    "name": tool.name,
                    "description": tool.description,
                    "parameters": tool.get_schema(),
                }
                for tool in available_tools
            ]
        }]

        # Force function calling mode to ANY — model MUST emit functionCall parts
        # or structured text, never text-formatted tool imitations
        tool_config = {
            "function_calling_config": {
                "mode": "ANY"
            }
        }

        raw_response = await self._call_gateway(
            instr, history, 
            tools=tool_declarations,
            tool_config=tool_config,
            extra_turns=self._api_turns,
        )

        text = self._extract_text(raw_response)
        fn_calls = self._extract_function_calls(raw_response)

        # Preserve the model's response turn for the function calling protocol.
        # This includes thought_signature fields that Gemini 3 requires to be
        # echoed back in subsequent turns.
        if raw_response and fn_calls:
            try:
                model_turn = raw_response['candidates'][0]['content']
                self._api_turns.append(model_turn)
            except (KeyError, IndexError):
                pass

        # Normalize function calls — preserve 'id' for functionResponse mapping
        calls = []
        for fc in fn_calls:
            calls.append({
                "name": fc.get("name", ""),
                "args": fc.get("args", {}),
                "id": fc.get("id", ""),  # Gemini 3 always returns an id
            })

        return text, calls

    async def _send_function_responses(self, history, function_responses, 
                                        available_tools):
        """Send function execution results back to the model.

        Implements Step 4 of the Gemini function calling protocol:
        append functionResponse parts and request the model's next action.

        Args:
            history: The shared all_messages context (text format).
            function_responses: List of dicts with keys:
                'name' (str), 'response' (dict), 'id' (str)
            available_tools: List of Tool objects (for continued tool use).

        Returns:
            tuple: (text_response, function_calls) — same shape as _think_with_tools().
        """
        # Build the functionResponse turn
        response_parts = []
        for fr in function_responses:
            response_parts.append({
                "functionResponse": {
                    "name": fr["name"],
                    "response": fr["response"],
                    "id": fr["id"],
                }
            })

        # Append the functionResponse turn to the per-agent buffer
        self._api_turns.append({
            "role": "user",
            "parts": response_parts,
        })

        instr = (
            f"You are {self.agent_id}. Persona: {self.persona}. "
            "You executed tools and the results are provided. "
            "Based on these results, decide your next action: "
            "call more tools if needed, or provide a text summary of what you accomplished."
        )

        tool_declarations = [{
            "function_declarations": [
                {
                    "name": tool.name,
                    "description": tool.description,
                    "parameters": tool.get_schema(),
                }
                for tool in available_tools
            ]
        }]

        # Use AUTO mode for follow-up — model can choose text OR more tool calls
        tool_config = {
            "function_calling_config": {
                "mode": "AUTO"
            }
        }

        raw_response = await self._call_gateway(
            instr, history,
            tools=tool_declarations,
            tool_config=tool_config,
            extra_turns=self._api_turns,
        )

        text = self._extract_text(raw_response)
        fn_calls = self._extract_function_calls(raw_response)

        # If more function calls, preserve this turn too
        if raw_response and fn_calls:
            try:
                model_turn = raw_response['candidates'][0]['content']
                self._api_turns.append(model_turn)
            except (KeyError, IndexError):
                pass

        calls = []
        for fc in fn_calls:
            calls.append({
                "name": fc.get("name", ""),
                "args": fc.get("args", {}),
                "id": fc.get("id", ""),
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
