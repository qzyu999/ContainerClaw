"""
Tool execution engine for ContainerClaw agents.

Implements the OpenAI function-calling protocol loop:
1. Agent thinks with tools (forced via tool_choice="required")
2. Tools are executed via ToolDispatcher
3. Results are sent back as tool-role messages
4. Loop until agent produces text or max rounds exceeded

Includes a circuit breaker that halts execution after 3 consecutive
tool failures to prevent runaway error loops.
"""

import asyncio
import json
from typing import Awaitable, Callable

import config
from tools import ToolDispatcher, ToolResult


class ToolExecutor:
    """Execute an agent's turn with tool support and circuit breaking.
    
    Uses callback injection to avoid circular imports with the moderator:
    - publish_fn: write events to Fluss
    - get_context_fn: get current context window
    - poll_fn: poll Fluss for mid-turn human interrupts
    """

    def __init__(
        self,
        tool_dispatcher: ToolDispatcher,
        publish_fn: Callable[..., Awaitable[None]],
        get_context_fn: Callable[[], list[dict]],
        poll_fn: Callable[[], Awaitable[bool]],
    ):
        self.dispatcher = tool_dispatcher
        self.publish = publish_fn
        self.get_context = get_context_fn
        self.poll = poll_fn

    async def execute_with_tools(self, agent, check_halt_fn: Callable[[], bool],
                                  parent_event_id: str = "") -> str | None:
        """Run an agent's full tool-augmented turn.

        Args:
            agent: GeminiAgent instance.
            check_halt_fn: Returns True if execution should be aborted
                           (e.g., user sent /stop mid-turn).
            parent_event_id: The event_id this execution chains from
                             (e.g., the winner announcement).
        
        Returns:
            Agent's final text response, or None.
        """
        available_tools = self.dispatcher.get_tools_for_agent(agent.agent_id)
        shared_context = self.get_context()

        # Clear the per-agent turn buffer for this execution cycle
        agent._api_turns = []

        final_text = None
        last_round_results = []
        consecutive_failures = 0
        current_parent = parent_event_id  # Track head of tool-call chain

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

            # Always capture the latest LLM text as the candidate final response
            if text and text.strip():
                cleaned_text = text.strip()
                final_text = cleaned_text
                
                # Publish agent reasoning as a 'thought' event so it persists
                # in Fluss traces. Without this, chain-of-thought is only stored
                # in the ephemeral _api_turns buffer and never archived.
                current_parent = await self.publish(
                    agent.agent_id,
                    cleaned_text,
                    "thought",
                    parent_event_id=current_parent,
                    edge_type="SEQUENTIAL",
                )

            if not fn_calls:
                # Model chose text response — done with tools
                break

            last_round_results = []

            for call in fn_calls:
                tool_name = call["name"]
                tool_args = call["args"]
                call_id = call["id"]

                print(f"🔧 [{agent.agent_id}] Tool call: {tool_name}({json.dumps(tool_args)[:200]})")
                # Log tool call (Use agent.agent_id for UI filtering)
                tool_call_id = await self.publish(
                    agent.agent_id,
                    f"[{agent.agent_id} Action]: $ {tool_name} {json.dumps(tool_args)[:200]}",
                    "action",
                    parent_event_id=current_parent,
                    edge_type="SEQUENTIAL",
                )

                # Inject parent_event_id for delegate tool (so subagents can chain)
                if tool_name == "delegate":
                    tool_args["_parent_event_id"] = tool_call_id

                # Define async chunk publisher for real-time telemetry
                async def publish_chunk(chunk: bytes):
                    # For now, we publish as a transparent 'telemetry' event.
                    # In production, this would go to a dedicated Fluss byte-stream topic.
                    await self.publish(
                        agent.agent_id, 
                        chunk.decode(errors="replace"), 
                        "telemetry",
                        parent_event_id=tool_call_id,
                        edge_type="SEQUENTIAL"
                    )

                result = await self.dispatcher.execute(
                    agent.agent_id, tool_name, tool_args, publish_fn=publish_chunk
                )

                # Circuit Breaker logic
                if not result.success:
                    consecutive_failures += 1
                else:
                    consecutive_failures = 0

                if consecutive_failures >= 3:
                    msg = f"🛑 Circuit Breaker: {agent.agent_id} halted after 3 consecutive tool failures."
                    print(f"⚠️ [Circuit Breaker] {msg}")
                    await self.publish(
                        "Moderator", msg, "system",
                        parent_event_id=tool_call_id,
                        edge_type="SEQUENTIAL",
                    )
                    return "🛑 Execution stopped due to consecutive tool failures."

                # Log tool result (child of tool call)
                result_summary = result.output[:500] if result.success else f"ERROR: {result.error}"
                print(f"  → {'✅' if result.success else '❌'} {result_summary[:200]}")
                # Log tool result summary (Use agent.agent_id for UI filtering)
                tool_result_id = await self.publish(
                    agent.agent_id,
                    f"[{agent.agent_id} Result]: {'✅' if result.success else '❌'} {result_summary[:500]}",
                    "action",
                    parent_event_id=tool_call_id,
                    edge_type="SEQUENTIAL",
                )

                # Publish full tool result to Fluss (child of tool call)
                tool_result_content = (
                    f"[Tool Result for {agent.agent_id}] {tool_name}: "
                    f"{'SUCCESS' if result.success else 'FAILED'}\n"
                    f"{result.output[:1000]}"
                    f"{(' | Error: ' + result.error) if result.error else ''}"
                )
                await self.publish(
                    agent.agent_id, tool_result_content, "action",
                    tool_name=tool_name,
                    tool_success=result.success,
                    parent_actor=agent.agent_id,
                    parent_event_id=tool_call_id,
                    edge_type="SEQUENTIAL",
                )

                # Advance chain head for next tool call
                current_parent = tool_result_id

                # Accumulate results for functionResponse construction
                # Adaptive Verbosity: per-tool-class context limits
                READ_TOOLS = {"repo_map", "structured_search", "advanced_read"}
                EXEC_TOOLS = {"session_shell", "execute_in_sandbox", "test_runner"}

                if tool_name in READ_TOOLS:
                    limit = 8000
                elif tool_name in EXEC_TOOLS:
                    limit = 4000  # Raised: execution output is high-value
                else:
                    limit = 2000

                output = result.output
                # Tail-biased truncation for execution tools:
                # keep last 75% where assertions and errors live
                if tool_name in EXEC_TOOLS and len(output) > limit:
                    tail_budget = limit * 3 // 4
                    head_budget = limit - tail_budget
                    output = (
                        output[:head_budget]
                        + "\n\n[... TRUNCATED ...]\n\n"
                        + output[-tail_budget:]
                    )
                elif len(output) > limit:
                    output = output[:limit] + "\n\n[TRUNCATED: Result too large for context window. Narrow your search or use pagination.]"

                last_round_results.append({
                    "name": tool_name,
                    "id": call_id,
                    "output": output,
                    "success": result.success,
                    "error": result.error,
                })

            # Poll Fluss to pick up published messages
            interrupted = await self.poll()
            if interrupted and check_halt_fn():
                print(f"🛑 [Moderator] {agent.agent_id} execution halted mid-turn by user command.")
                return "🛑 Turn aborted by user command."

        else:
            # FIX 3: Triggered if the loop exhausted without breaking
            warning_msg = f"\n\n🛑 Execution halted: Exceeded max tool rounds ({config.MAX_TOOL_ROUNDS})."
            final_text = (final_text or "") + warning_msg
            await self.publish(
                "Moderator", warning_msg, "system",
                parent_event_id=current_parent,
                edge_type="SEQUENTIAL",
            )

        # Clear the per-agent turn buffer — cycle complete
        agent._api_turns = []

        return final_text

    @staticmethod
    async def execute_text_only(agent, get_context_fn: Callable[[], list[dict]]) -> str | None:
        """Execute the winning agent's turn without tools (backward-compatible)."""
        updated_context = get_context_fn()
        return await agent._think(updated_context)
