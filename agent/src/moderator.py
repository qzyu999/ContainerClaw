import asyncio
import json
import os
import random
import time
import fluss
import pyarrow as pa
import requests
from typing import List

import config

from tools import ToolDispatcher, ToolResult


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


class StageModerator:
    def __init__(self, table, agents: List[GeminiAgent],
                 session_id: str,
                 tool_dispatcher: ToolDispatcher | None = None,
                 sessions_table=None,
                 fluss_conn=None):
        self.table = table
        self.agents = agents
        self.session_id = session_id
        self.tool_dispatcher = tool_dispatcher
        self.sessions_table = sessions_table
        self.fluss_conn = fluss_conn
        self.agent_names = [a.agent_id for a in agents]
        self.roster_str = ", ".join([f"{a.agent_id} ({a.persona})" for a in agents])
        self.all_messages = []  # Read-cache rebuilt from Fluss on startup
        self.history_keys = set()
        self.last_replayed_offset = 0
        self.writer = table.new_append().create_writer()
        self.pa_schema = pa.schema([
            pa.field("session_id", pa.string()),
            pa.field("ts", pa.int64()),
            pa.field("actor_id", pa.string()),
            pa.field("content", pa.string()),
            pa.field("type", pa.string()),
            pa.field("tool_name", pa.string()),
            pa.field("tool_success", pa.bool_()),
            pa.field("parent_actor", pa.string()),
        ])

    def _get_context_window(self, size: int | None = None) -> list[dict]:
        """Return the most recent messages for LLM context.

        Reads from self.all_messages (populated by the Fluss poll loop
        and startup replay).
        """
        n = size or config.MAX_HISTORY_MESSAGES
        messages = self.all_messages[-n:]
        
        # Token Guard: Enforce a character-based budget (proxy for token limit)
        # to prevent context drift in models with ~128k context windows.
        char_limit = config.MAX_HISTORY_CHARS
        budget = char_limit
        final_msgs = []
        
        # Walk backwards until budget is exhausted
        for msg in reversed(messages):
            content = msg.get("content", "")
            msg_len = len(content)
            if budget - msg_len < 0:
                print(f"⚠️ [Moderator] Token Guard triggered. Truncating history at {len(final_msgs)} msgs.")
                break
            final_msgs.insert(0, msg)
            budget -= msg_len
            
        return final_msgs

    async def _replay_history(self):
        """Replay the Fluss log from session creation time to rebuild all_messages.
        
        Optimized: Looks up session start time and seeks the scanner.
        """
        start_ts = 0
        if self.sessions_table:
            try:
                # Scan sessions table to find start time (it's a Log table now)
                scanner = await self.sessions_table.new_scan().create_record_batch_log_scanner()
                for b in range(16):
                    scanner.subscribe(bucket_id=b, start_offset=0)
                
                found = False
                for _ in range(5): # Max 5 polls
                    poll = await asyncio.to_thread(scanner.poll_arrow, timeout_ms=500)
                    if poll.num_rows == 0: continue
                    
                    id_arr = poll["session_id"]
                    created_arr = poll["created_at"]
                    for i in range(poll.num_rows):
                        if id_arr[i].as_py() == self.session_id:
                            start_ts = int(created_arr[i].as_py())
                            print(f"📜 [Moderator] Session {self.session_id} started at {start_ts}. Replaying from there...")
                            found = True
                            break
                    if found: break
            except Exception as e:
                print(f"⚠️ [Moderator] Failed to lookup session start time: {e}")
                import traceback
                traceback.print_exc()

        self.scanner = await self.table.new_scan().create_record_batch_log_scanner()
        
        if start_ts > 0 and self.fluss_conn:
            admin = await self.fluss_conn.get_admin()
            offsets = await admin.list_offsets(
                self.table.get_table_path(),
                list(range(16)),
                fluss.OffsetSpec.timestamp(start_ts)
            )
            self.scanner.subscribe_buckets(offsets)
        else:
            print(f"📜 [Moderator] Replaying FULL Fluss history for session: {self.session_id}...")
            for b in range(16):
                self.scanner.subscribe(bucket_id=b, start_offset=0)

        total_replayed = 0
        while True:
            poll = await asyncio.to_thread(self.scanner.poll_arrow, timeout_ms=500)
            if poll.num_rows == 0:
                break  # Caught up to head of log

            # Use dict-style access for pyarrow.RecordBatch
            sess_arr = poll["session_id"]
            actor_arr = poll["actor_id"]
            content_arr = poll["content"]
            ts_arr = poll["ts"]

            for i in range(poll.num_rows):
                if sess_arr[i].as_py() != self.session_id:
                    continue

                ts = ts_arr[i].as_py()
                actor_id = actor_arr[i].as_py()
                content = content_arr[i].as_py()

                key = f"{ts}-{actor_id}"
                if key not in self.history_keys:
                    self.history_keys.add(key)
                    self.all_messages.append({
                        "actor_id": actor_id,
                        "content": content,
                        "ts": ts
                    })
                    total_replayed += 1

        self.all_messages.sort(key=lambda x: x["ts"])
        self.last_replayed_offset = total_replayed
        print(f"✅ [Moderator] Replayed {total_replayed} messages from Fluss.")

    async def _poll_once(self):
        """Poll the Fluss scanner once to pick up recently published messages.

        Used inside _execute_with_tools and after nudge publishes to ensure
        Fluss-written messages are available in all_messages before the next
        LLM call.
        """
        poll = await asyncio.to_thread(self.scanner.poll_arrow, timeout_ms=600)
        if poll.num_rows > 0:
            df = poll.to_pandas()
            for _, row in df.iterrows():
                # Filter by session_id
                if row.get("session_id") != self.session_id:
                    continue

                key = f"{row['ts']}-{row['actor_id']}"
                if key not in self.history_keys:
                    self.history_keys.add(key)
                    self.all_messages.append({
                        "actor_id": row["actor_id"],
                        "content": row["content"],
                        "ts": row["ts"]
                    })
            # Ensure context maintains strict chronological order
            self.all_messages.sort(key=lambda x: x["ts"])

    async def run(self, autonomous_steps=0):
        """
        Runs the moderator loop.
        autonomous_steps: Number of turns to run without human input.
                          -1 for infinite. 0 to wait for human.
        """
        # Baseline Capture: Store the original budget to allow resets upon Human interaction
        base_budget = autonomous_steps

        # Replay history from Fluss for crash recovery
        await self._replay_history()
        # self.scanner is now positioned at the tail!

        conchshell_status = "enabled" if self.tool_dispatcher else "disabled"
        await self.publish("Moderator", f"Multi-Agent System Online. ConchShell: {conchshell_status}.", "thought")
        print(f"⚖️ [Moderator] Active with agents: {self.agent_names}")
        print(f"🐚 [Moderator] ConchShell: {conchshell_status}")
        if base_budget != 0:
            print(f"🤖 [Moderator] Autonomous Mode: {base_budget} steps.")

        # After replay, if we have history context, resume autonomous mode
        # immediately (no need to wait for a new Human message to trigger it).
        if self.last_replayed_offset > 0 and base_budget != 0:
            current_steps = base_budget
            print(f"🔄 [Moderator] Resuming autonomous mode from replayed history ({self.last_replayed_offset} msgs).")
        else:
            current_steps = 0

        while True:
            poll = await asyncio.to_thread(self.scanner.poll_arrow, timeout_ms=500)
            human_interrupted = False

            if poll.num_rows > 0:
                df = poll.to_pandas()
                for _, row in df.iterrows():
                    # Filter by session_id
                    if row.get("session_id") != self.session_id:
                        continue

                    key = f"{row['ts']}-{row['actor_id']}"
                    if key not in self.history_keys:
                        self.history_keys.add(key)
                        msg_obj = {"actor_id": row['actor_id'], "content": row['content'], "ts": row['ts']}
                        self.all_messages.append(msg_obj)

                        if row['actor_id'] == "Human":
                            print(f"📢 [Human said]: {row['content']}")
                            human_interrupted = True
                            current_steps = base_budget  # Reset to captured baseline
                            if base_budget != 0:
                                print(f"🔄 [Moderator] Human input detected. Resetting budget to {base_budget} steps.")
                        elif row['actor_id'] in self.agent_names:
                            print(f"👂 [Heard] [{row['actor_id']}]: {row['content']}")


                        # Memory management: trim in-memory cache.
                        # Older messages are still in Fluss — _replay_history() can recover.
                        max_in_memory = config.MAX_HISTORY_MESSAGES * 3
                        if len(self.all_messages) > max_in_memory:
                            self.all_messages = self.all_messages[-config.MAX_HISTORY_MESSAGES * 2:]
                            # W-4: Clear history_keys to cap memory. The scanner offset
                            # has moved past all processed messages, so they won't be
                            # re-polled — dedup keys for trimmed messages are unnecessary.
                            self.history_keys.clear()
                            print(f"🧹 [Moderator] Trimmed in-memory history to {len(self.all_messages)} messages.")

                # Ensure context maintains strict chronological order after polling new batches
                self.all_messages.sort(key=lambda x: x["ts"])

            # Trigger if human spoke OR we still have autonomous steps to take
            if human_interrupted or (current_steps != 0):
                if not human_interrupted:
                    if current_steps > 0:
                        current_steps -= 1
                    print(f"🤖 [Autonomous Turn] {current_steps if current_steps >= 0 else 'inf'} steps remaining...")

                await asyncio.sleep(1.0)
                context_window = self._get_context_window()

                # Run the election
                winner, election_log, is_job_done = await self.elect_leader(context_window)

                # Persist election context to Fluss (and thus in-memory history via the poll loop)
                await self.publish("Moderator", f"Election Summary:\n{election_log}", "voting")

                # Transition to IDLE state if consensus is reached
                if is_job_done:
                    print("🎉 [Moderator] Job is complete! Pausing the multi-agent loop.")
                    await self.publish("Moderator", "Consensus: Task Complete.", "finish")
                    if self.tool_dispatcher:
                        self.tool_dispatcher.cleanup()
                    # Exhaust steps to stop LLM calls, but keep polling Fluss
                    current_steps = 0
                    continue

                if winner:
                    winning_agent = next(a for a in self.agents if a.agent_id == winner)
                    print(f"🧠 [Moderator] {winner} won the election. Executing...")
                    await self.publish("Moderator", f"🏆 Winner: {winner}", "thought")

                    # ── ConchShell: tool-augmented execution ──
                    if self.tool_dispatcher:
                        resp = await self._execute_with_tools(winning_agent)
                    else:
                        resp = await self._execute_text_only(winning_agent)

                    if resp and "[WAIT]" not in resp:
                        print(f"📢 [{winner} says]: {resp}")
                        await self.publish(winner, resp, "output")
                    else:
                        print(f"💤 [{winner}] chose to WAIT or failed to respond. Nudging...")
                        await self.publish("Moderator", f"💤 {winner} is waiting. Nudging...", "thought")
                        nudge_text = f"@{winner}, you won the election but chose to WAIT. Could you briefly explain why so the team knows what you're waiting for?"
                        await self.publish("Moderator", nudge_text, "system")
                        # Poll Fluss to pick up the nudge we just published
                        await self._poll_once()
                        nudge_context = self._get_context_window()
                        resp = await winning_agent._think(nudge_context)

                        if resp:
                            print(f"📢 [{winner} explanation]: {resp}")
                            await self.publish(winner, resp, "output")
                        else:
                            print(f"❌ [{winner}] remains silent after nudge.")

                await self.publish("Moderator", "Cycle complete.", "checkpoint")

            await asyncio.sleep(1)

    async def _execute_with_tools(self, agent: GeminiAgent) -> str | None:
        """Execute the winning agent's turn with ConchShell tool support.

        Implements the full Gemini function calling protocol:
        1. _think_with_tools() → model returns functionCall parts (forced via ANY mode)
        2. Execute tools via ToolDispatcher
        3. _send_function_responses() → model receives results, may request more tools
        4. Loop until model returns text (final response) or max rounds exceeded

        Returns the agent's final text response (or None).
        """
        available_tools = self.tool_dispatcher.get_tools_for_agent(agent.agent_id)
        shared_context = self._get_context_window()

        # Clear the per-agent turn buffer for this execution cycle
        agent._api_turns = []

        final_text = None
        last_round_results = []

        for round_num in range(config.MAX_TOOL_ROUNDS):
            if round_num == 0:
                text, fn_calls = await agent._think_with_tools(
                    shared_context, available_tools
                )
            else:
                # Build functionResponse parts from the last round's results
                function_responses = []
                for call_result in last_round_results:
                    function_responses.append({
                        "name": call_result["name"],
                        "response": {
                            "result": call_result["output"],
                            "success": call_result["success"],
                            "error": call_result.get("error"),
                        },
                        "id": call_result["id"],
                    })

                text, fn_calls = await agent._send_function_responses(
                    shared_context, function_responses, available_tools
                )

            if text:
                final_text = text

            if not fn_calls:
                # Model chose text response — done with tools
                break

            # No artificial per-turn or per-cycle limits — the model self-regulates
            # via the function calling protocol: it stops calling tools when it has
            # enough results to produce a text summary. The MAX_TOOL_ROUNDS config
            # (default: 30) serves as a safety backstop for runaway loops.
            last_round_results = []

            for call in fn_calls:

                tool_name = call["name"]
                tool_args = call["args"]
                call_id = call["id"]  # Gemini 3 function call ID

                print(f"🔧 [{agent.agent_id}] Tool call: {tool_name}({json.dumps(tool_args)[:200]})")
                await self.publish(
                    agent.agent_id,
                    f"$ {tool_name} {json.dumps(tool_args)[:200]}",
                    "action",
                )

                result = await self.tool_dispatcher.execute(
                    agent.agent_id, tool_name, tool_args
                )

                # Log tool result
                result_summary = result.output[:500] if result.success else f"ERROR: {result.error}"
                print(f"  → {'✅' if result.success else '❌'} {result_summary[:200]}")
                await self.publish(
                    agent.agent_id,
                    f"{'✅' if result.success else '❌'} {result_summary[:500]}",
                    "action",
                )

                # Publish full tool result to Fluss
                tool_result_content = (
                    f"[Tool Result for {agent.agent_id}] {tool_name}: "
                    f"{'SUCCESS' if result.success else 'FAILED'}\n"
                    f"{result.output[:1000]}"
                    f"{(' | Error: ' + result.error) if result.error else ''}"
                )
                await self.publish(
                    "Moderator", tool_result_content, "action",
                    tool_name=tool_name,
                    tool_success=result.success,
                    parent_actor=agent.agent_id,
                )

                # Accumulate results for functionResponse construction
                # Adaptive Verbosity: allow more context for read-heavy tools
                read_tools = ["repo_map", "structured_search", "advanced_read"]
                limit = 8000 if tool_name in read_tools else 2000
                
                output = result.output
                if len(output) > limit:
                    output = output[:limit] + "\n\n[TRUNCATED: Result too large for context window. Narrow your search or use pagination.]"

                last_round_results.append({
                    "name": tool_name,
                    "id": call_id,
                    "output": output,
                    "success": result.success,
                    "error": result.error,
                })

            # Poll Fluss to pick up published messages
            await self._poll_once()
            shared_context = self._get_context_window()

        # Clear the per-agent turn buffer — cycle complete
        agent._api_turns = []

        return final_text

    async def _execute_text_only(self, agent: GeminiAgent) -> str | None:
        """Execute the winning agent's turn without tools (backward-compatible)."""
        updated_context = self._get_context_window()
        return await agent._think(updated_context)

    async def elect_leader(self, history):
        """Run a 3-round election. Returns (winner, election_log, is_job_done)."""
        previous_votes_context = None
        election_log_collector = []

        for r in range(1, 4):
            election_log_collector.append(f"--- Round {r} ---")
            await self.publish("Moderator", f"🗳️ Election Round {r}...", "thought")
            print(f"🗳️ [Moderator] Election Round {r} starting...")
            # Stagger votes with random jitter to avoid thundering-herd SSL drops
            async def _staggered_vote(agent, delay):
                await asyncio.sleep(delay)
                return await agent._vote(history, self.roster_str, previous_votes_context)

            jittered = [
                _staggered_vote(a, random.uniform(0, 2.0))
                for a in self.agents
            ]
            votes = await asyncio.gather(*jittered)

            tally = {}
            attribution_list = []
            valid_votes_count = 0
            done_votes_count = 0

            for agent, vote_result in zip(self.agents, votes):
                if vote_result and "vote" in vote_result:
                    valid_votes_count += 1
                    nominee = vote_result['vote']
                    reason = vote_result.get('reason', 'N/A')

                    # Defensively parse the boolean in case the LLM returns a string "true"
                    is_done_raw = vote_result.get('is_done', False)
                    is_done = is_done_raw.lower() == 'true' if isinstance(is_done_raw, str) else bool(is_done_raw)
                    done_reason = vote_result.get('done_reason', 'N/A')

                    if is_done:
                        done_votes_count += 1

                    tally[nominee] = tally.get(nominee, 0) + 1
                    vote_str = f"{agent.agent_id} voted for {nominee} ('{reason}') | Done: {is_done} ('{done_reason}')"
                    attribution_list.append(vote_str)
                    election_log_collector.append(vote_str)
                    print(f"🗣️ [{agent.agent_id}] voted for {nominee} -> \"{reason}\" | Done: {is_done} -> \"{done_reason}\"")
                else:
                    print(f"⚠️ [{agent.agent_id}] failed to cast a valid vote.")

            if valid_votes_count == 0:
                return random.choice(self.agent_names), "No valid votes received.", False

            # Check for unanimous agreement that the job is done
            is_job_done = (done_votes_count == valid_votes_count) and (valid_votes_count > 0)

            tally_str = f"Tally: {tally}"
            election_log_collector.append(tally_str)
            await self.publish("Moderator", f"Round {r} {tally_str}", "thought")
            print(f"📊 [Moderator] Round {r} {tally_str}")

            # If everyone agrees the task is finished, return immediately
            if is_job_done:
                election_log_collector.append("Consensus reached: Task is complete.")
                print("✅ [Moderator] All agents agree the job is completed.")
                return None, "\n".join(election_log_collector), True

            max_votes = max(tally.values())
            winners = [name for name, count in tally.items() if count == max_votes]

            if len(winners) == 1:
                return winners[0], "\n".join(election_log_collector), False

            previous_votes_context = " | ".join(attribution_list)
            print(f"⚖️ [Moderator] Round {r} ended in a tie: {winners}")

        choice = random.choice(winners)
        election_log_collector.append(f"Tie persists. Circuit breaker chose: {choice}")
        print(f"🎲 [Moderator] Tie persists. Circuit breaker choosing: {choice}")
        return choice, "\n".join(election_log_collector), False

    async def publish(self, actor_id, content, m_type="output",
                      tool_name="", tool_success=False, parent_actor=""):
        try:
            batch = pa.RecordBatch.from_arrays([
                pa.array([self.session_id], type=pa.string()),
                pa.array([int(time.time() * 1000)], type=pa.int64()),
                pa.array([actor_id], type=pa.string()),
                pa.array([content], type=pa.string()),
                pa.array([m_type], type=pa.string()),
                pa.array([tool_name], type=pa.string()),
                pa.array([tool_success], type=pa.bool_()),
                pa.array([parent_actor], type=pa.string()),
            ], schema=self.pa_schema)
            self.writer.write_arrow_batch(batch)
            # Ensure each published message is immediately committed to the Fluss log.
            # This ensures that history is available for instant retrieval on refresh.
            if hasattr(self.writer, "flush"):
                self.writer.flush()
            print(f"📝 [Moderator] Published to Fluss: {actor_id} ({m_type})")
        except Exception as e:
            print(f"❌ [Moderator] Failed to publish to Fluss: {e}")
            import traceback
            traceback.print_exc()