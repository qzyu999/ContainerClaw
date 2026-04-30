"""
Command interception dispatcher for ContainerClaw moderator.

Handles user commands (messages starting with '/') so that the
moderator loop doesn't need inline command parsing. New commands
can be registered without modifying _handle_single_message().
"""

from typing import Awaitable, Callable


class CommandDispatcher:
    """Routes slash-commands to registered async handler functions.

    Usage:
        dispatcher = CommandDispatcher()
        dispatcher.register("/stop", handle_stop)
        dispatcher.register("/automation=", handle_automation)

        was_command = await dispatcher.dispatch(content, moderator)
    """

    def __init__(self):
        self._handlers: list[tuple[str, Callable]] = []

    def register(self, prefix: str, handler: Callable[..., Awaitable[None]]):
        """Register a handler for a command prefix.

        Args:
            prefix: The command prefix to match (e.g., "/stop", "/automation=").
            handler: Async callable(content, moderator) invoked when content
                     starts with prefix.
        """
        self._handlers.append((prefix, handler))

    async def dispatch(self, content: str, moderator) -> bool:
        """Attempt to dispatch content as a command.

        Args:
            content: The raw message content (e.g., "/stop" or "/automation=5").
            moderator: The StageModerator instance (for state mutation).

        Returns:
            True if content was a recognized command (caller should NOT
            trigger an election). False if content is a regular message.
        """
        if not content.startswith("/"):
            return False

        for prefix, handler in self._handlers:
            if content.startswith(prefix):
                await handler(content, moderator)
                return True

        # Unrecognized slash-command — treat as regular message
        return False


# ── Built-in Command Handlers ───────────────────────────────────────

async def _handle_stop(content: str, moderator):
    """Halt all autonomous execution immediately.
    
    If a ReconciliationController is wired, uses halt() to cancel
    running election/execution tasks. Otherwise falls back to
    budget zeroing.
    """
    reconciler = getattr(moderator, '_reconciler', None)
    if reconciler:
        reconciler.halt()
    else:
        moderator.base_budget = 0
        moderator.current_steps = 0
    print("🛑 [Moderator] /stop received. Halting autonomy.")
    await moderator.publish("Moderator", "🛑 Automation halted by user demand.", "system")


async def _handle_automation(content: str, moderator):
    """Set the autonomous step budget: /automation=N."""
    try:
        val = int(content.split("=")[1])
        moderator.base_budget = val
        moderator.current_steps = val
        # If reconciler is in SUSPENDED state, transition back to IDLE
        reconciler = getattr(moderator, '_reconciler', None)
        if reconciler:
            from reconciler import State
            if reconciler.state == State.SUSPENDED:
                reconciler.state = State.IDLE
                print(f"🔄 [Reconciler] Exiting SUSPENDED → IDLE (budget={val})")
        print(f"🤖 [Moderator] /automation={val} received. Budget updated.")
        await moderator.publish("Moderator", f"🤖 Step budget updated to: {val}", "system")
    except (IndexError, ValueError):
        print("⚠️ [Moderator] Invalid /automation command format.")


async def _handle_subagents(content: str, moderator):
    """Show status of active subagents."""
    mgr = getattr(moderator, 'subagent_manager', None)
    if not mgr:
        await moderator.publish("Moderator", "⚠️ SubagentManager not available.", "system")
        return
    status = mgr.get_status()
    await moderator.publish("Moderator", f"📊 {status}", "system")


async def _handle_cancel_subagent(content: str, moderator):
    """Cancel a specific subagent: /cancel_subagent=<task_id>."""
    mgr = getattr(moderator, 'subagent_manager', None)
    if not mgr:
        await moderator.publish("Moderator", "⚠️ SubagentManager not available.", "system")
        return
    try:
        task_id = content.split("=")[1].strip()
        success = await mgr.cancel(task_id)
        if success:
            await moderator.publish("Moderator", f"🛑 Subagent {task_id} cancellation requested.", "system")
        else:
            await moderator.publish("Moderator", f"⚠️ Subagent {task_id} not found.", "system")
    except (IndexError, ValueError):
        await moderator.publish("Moderator", "⚠️ Usage: /cancel_subagent=<task_id>", "system")


def create_default_dispatcher() -> CommandDispatcher:
    """Create a CommandDispatcher pre-loaded with all built-in commands."""
    dispatcher = CommandDispatcher()
    dispatcher.register("/stop", _handle_stop)
    dispatcher.register("/automation=", _handle_automation)
    dispatcher.register("/subagents", _handle_subagents)
    dispatcher.register("/cancel_subagent=", _handle_cancel_subagent)
    return dispatcher
