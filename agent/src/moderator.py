import asyncio
import json
import os
import random
import time
import fluss
import pyarrow as pa
import requests
from typing import List, Callable

from tools import ToolDispatcher, ToolResult

MAX_HISTORY_MESSAGES = 100


class GeminiAgent:
    def __init__(self, agent_id, persona, api_key):
        self.agent_id = agent_id
        self.persona = persona
        self.api_key = api_key
        self.gateway_url = f"{os.getenv('LLM_GATEWAY_URL', 'http://llm-gateway:8000')}/v1/chat/completions"
        self.model = "gemini-3-flash-preview"

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

    async def _call_gateway(self, sys_instr, history, is_json=False, tools=None):
        payload = {
            "system_instruction": sys_instr,  # Raw string, Gateway wraps it
            "contents": self._format_history(history),
            "generationConfig": {"response_mime_type": "application/json"} if is_json else {}
        }
        if tools:
            payload["tools"] = tools
        try:
            res = await asyncio.to_thread(
                requests.post, self.gateway_url, json=payload, timeout=60
            )
            if res.status_code == 200:
                return res.json()
            else:
                print(f"❌ [{self.agent_id}] API Error {res.status_code}: {res.text}")
                return None
        except Exception as e:
            print(f"❌ [{self.agent_id}] Gateway call failed: {e}")
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

    async def _vote(self, history, candidates, previous_votes=None):
        instr = (
            f"You are {self.agent_id}. Persona: {self.persona}.\n"
            f"You are in a voting phase. A new message has arrived in the chat.\n"
            f"You must review the history and vote for the ONE agent who is best suited to respond.\n"
            f"Candidates: {candidates}.\n"
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
        """Enhanced thinking that supports Gemini function calling.

        Returns:
            tuple: (text_response: str | None, function_calls: list[dict])
                   function_calls items have keys 'name' and 'args'.
        """
        tool_names = ", ".join(t.name for t in available_tools)
        instr = (
            f"You are {self.agent_id}, participating in a multi-agent software engineering team. "
            f"Persona: {self.persona}. "
            f"You have access to tools: [{tool_names}]. "
            "Use them when you need to take action — read files, write code, run commands, "
            "manage the project board, or run tests. "
            "If no action is needed or you just spoke, respond with [WAIT].\n\n"
            "CRITICAL: If the Moderator just announced you won the election, you SHOULD contribute. "
            "Do not just [WAIT] if you were specifically chosen to speak."
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

        raw_response = await self._call_gateway(
            instr, history, tools=tool_declarations
        )
        text = self._extract_text(raw_response)
        fn_calls = self._extract_function_calls(raw_response)

        # Normalize function calls to simple dicts
        calls = []
        for fc in fn_calls:
            calls.append({
                "name": fc.get("name", ""),
                "args": fc.get("args", {}),
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
    MAX_TOOL_ROUNDS = 3  # Max reflect cycles per election turn

    def __init__(self, table, agents: List[GeminiAgent], emit_cb: Callable,
                 tool_dispatcher: ToolDispatcher | None = None):
        self.table = table
        self.agents = agents
        self.emit_cb = emit_cb
        self.tool_dispatcher = tool_dispatcher
        self.agent_names = [a.agent_id for a in agents]
        self.all_messages = []  # PERSISTENT HISTORY across poll cycles
        self.history_keys = set()
        self.writer = table.new_append().create_writer()
        self.pa_schema = pa.schema([
            pa.field("ts", pa.int64()), 
            pa.field("actor_id", pa.string()), 
            pa.field("content", pa.string())
        ])

    async def run(self, autonomous_steps=0):
        """
        Runs the moderator loop.
        autonomous_steps: Number of turns to run without human input.
                          -1 for infinite. 0 to wait for human.
        """
        scanner = await self.table.new_scan().create_record_batch_log_scanner()
        scanner.subscribe(bucket_id=0, start_offset=0)

        conchshell_status = "enabled" if self.tool_dispatcher else "disabled"
        self.emit_cb("Moderator", f"Multi-Agent System Online. ConchShell: {conchshell_status}.", "thought")
        print(f"⚖️ [Moderator] Active with agents: {self.agent_names}")
        print(f"🐚 [Moderator] ConchShell: {conchshell_status}")
        if autonomous_steps != 0:
            print(f"🤖 [Moderator] Autonomous Mode: {autonomous_steps} steps.")

        current_steps = 0

        while True:
            poll = await asyncio.to_thread(scanner.poll_arrow, timeout_ms=500)
            human_interrupted = False

            if poll.num_rows > 0:
                df = poll.to_pandas()
                for _, row in df.iterrows():
                    key = f"{row['ts']}-{row['actor_id']}"
                    if key not in self.history_keys:
                        self.history_keys.add(key)
                        msg_obj = {"actor_id": row['actor_id'], "content": row['content']}
                        self.all_messages.append(msg_obj)

                        if row['actor_id'] == "Human":
                            print(f"📢 [Human said]: {row['content']}")
                            self.emit_cb(row['actor_id'], row['content'], "output")
                            human_interrupted = True
                            current_steps = autonomous_steps  # Reset to initial value
                            if autonomous_steps != 0:
                                print(f"🔄 [Moderator] Human input detected. Resetting autonomous steps to {autonomous_steps}.")
                        elif row['actor_id'] in self.agent_names:
                            print(f"👂 [Heard] [{row['actor_id']}]: {row['content']}")
                            self.emit_cb(row['actor_id'], row['content'], "output")

            # Trigger if human spoke OR we still have autonomous steps to take
            if human_interrupted or (current_steps != 0):
                if not human_interrupted:
                    if current_steps > 0:
                        current_steps -= 1
                    print(f"🤖 [Autonomous Turn] {current_steps if current_steps >= 0 else 'inf'} steps remaining...")

                await asyncio.sleep(1.0)
                context_window = self.all_messages[-MAX_HISTORY_MESSAGES:]

                # Run the election
                winner, election_log, is_job_done = await self.elect_leader(context_window)

                # Persist election context to in-memory history (NOT Fluss)
                self.all_messages.append({"actor_id": "Moderator", "content": f"Election Summary:\n{election_log}"})

                # Terminate loop if consensus is reached
                if is_job_done:
                    print("🎉 [Moderator] Job is complete! Terminating the multi-agent loop.")
                    self.emit_cb("Moderator", "Consensus: Task Complete.", "finish")
                    break

                if winner:
                    winning_agent = next(a for a in self.agents if a.agent_id == winner)
                    print(f"🧠 [Moderator] {winner} won the election. Executing...")
                    self.emit_cb("Moderator", f"🏆 Winner: {winner}", "thought")

                    # ── ConchShell: tool-augmented execution ──
                    if self.tool_dispatcher:
                        resp = await self._execute_with_tools(winning_agent)
                    else:
                        resp = await self._execute_text_only(winning_agent)

                    if resp and "[WAIT]" not in resp:
                        print(f"📢 [{winner} says]: {resp}")
                        await self.publish(winner, resp)
                    else:
                        print(f"💤 [{winner}] chose to WAIT or failed to respond. Nudging...")
                        self.emit_cb("Moderator", f"💤 {winner} is waiting. Nudging...", "thought")
                        nudge_text = f"@{winner}, you won the election but chose to WAIT. Could you briefly explain why so the team knows what you're waiting for?"
                        self.all_messages.append({"actor_id": "Moderator", "content": nudge_text})
                        nudge_context = self.all_messages[-MAX_HISTORY_MESSAGES:]
                        resp = await winning_agent._think(nudge_context)

                        if resp:
                            print(f"📢 [{winner} explanation]: {resp}")
                            await self.publish(winner, resp)
                        else:
                            print(f"❌ [{winner}] remains silent after nudge.")

                self.emit_cb("Moderator", "Cycle complete.", "finish")

                # Reset per-cycle tool counter
                if self.tool_dispatcher:
                    self.tool_dispatcher.reset_cycle()

            await asyncio.sleep(1)

    async def _execute_with_tools(self, agent: GeminiAgent) -> str | None:
        """Execute the winning agent's turn with ConchShell tool support.

        Uses a think→act→reflect loop, capped at MAX_TOOL_ROUNDS iterations.
        Returns the agent's final text response (or None).
        """
        available_tools = self.tool_dispatcher.get_tools_for_agent(agent.agent_id)
        updated_context = self.all_messages[-MAX_HISTORY_MESSAGES:]

        final_text = None

        for round_num in range(self.MAX_TOOL_ROUNDS):
            if round_num == 0:
                text, fn_calls = await agent._think_with_tools(updated_context, available_tools)
            else:
                # Reflect on previous tool results
                reflect_text = await agent._reflect(updated_context)
                text = reflect_text
                fn_calls = []  # Reflection is text-only for now

            if text:
                final_text = text

            if not fn_calls:
                # No tool calls — agent is done acting
                break

            # Execute each tool call
            turn_tool_count = 0
            for call in fn_calls:
                if turn_tool_count >= self.tool_dispatcher.MAX_TOOLS_PER_TURN:
                    print(f"⚠️ [{agent.agent_id}] Hit per-turn tool limit ({self.tool_dispatcher.MAX_TOOLS_PER_TURN})")
                    break
                turn_tool_count += 1

                tool_name = call["name"]
                tool_args = call["args"]
                print(f"🔧 [{agent.agent_id}] Tool call: {tool_name}({json.dumps(tool_args)[:200]})")
                self.emit_cb(
                    agent.agent_id,
                    f"$ {tool_name} {json.dumps(tool_args)[:200]}",
                    "action",
                )

                result = await self.tool_dispatcher.execute(agent.agent_id, tool_name, tool_args)

                # Log tool result
                result_summary = result.output[:500] if result.success else f"ERROR: {result.error}"
                print(f"  → {'✅' if result.success else '❌'} {result_summary[:200]}")
                self.emit_cb(
                    agent.agent_id,
                    f"{'✅' if result.success else '❌'} {result_summary[:500]}",
                    "action",
                )

                # Add tool result to history so the agent can reflect on it
                self.all_messages.append({
                    "actor_id": "Moderator",
                    "content": (
                        f"[Tool Result for {agent.agent_id}] {tool_name}: "
                        f"{'SUCCESS' if result.success else 'FAILED'}\n"
                        f"{result.output[:1000]}"
                        f"{(' | Error: ' + result.error) if result.error else ''}"
                    ),
                })

            updated_context = self.all_messages[-MAX_HISTORY_MESSAGES:]

        return final_text

    async def _execute_text_only(self, agent: GeminiAgent) -> str | None:
        """Execute the winning agent's turn without tools (backward-compatible)."""
        updated_context = self.all_messages[-MAX_HISTORY_MESSAGES:]
        return await agent._think(updated_context)

    async def elect_leader(self, history):
        """Run a 3-round election. Returns (winner, election_log, is_job_done)."""
        previous_votes_context = None
        election_log_collector = []

        for r in range(1, 4):
            election_log_collector.append(f"--- Round {r} ---")
            self.emit_cb("Moderator", f"🗳️ Election Round {r}...", "thought")
            print(f"🗳️ [Moderator] Election Round {r} starting...")
            # Stagger votes with random jitter to avoid thundering-herd SSL drops
            async def _staggered_vote(agent, delay):
                await asyncio.sleep(delay)
                return await agent._vote(history, self.agent_names, previous_votes_context)

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
            self.emit_cb("Moderator", f"Round {r} {tally_str}", "thought")
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

    async def publish(self, actor_id, content):
        batch = pa.RecordBatch.from_arrays([
            pa.array([int(time.time() * 1000)], type=pa.int64()),
            pa.array([actor_id], type=pa.string()),
            pa.array([content], type=pa.string())
        ], schema=self.pa_schema)
        self.writer.write_arrow_batch(batch)
        await self.writer.flush()