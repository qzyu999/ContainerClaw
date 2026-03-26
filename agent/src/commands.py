"""
Command interception dispatcher for ContainerClaw moderator.

Handles user commands (messages starting with '/') so that the
moderator loop doesn't need inline command parsing. New commands
can be registered without modifying _handle_single_message().
"""

from typing import Callable, Awaitable


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
    """Halt all autonomous execution immediately."""
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
        print(f"🤖 [Moderator] /automation={val} received. Budget updated.")
        await moderator.publish("Moderator", f"🤖 Step budget updated to: {val}", "system")
    except (IndexError, ValueError):
        print("⚠️ [Moderator] Invalid /automation command format.")


def create_default_dispatcher() -> CommandDispatcher:
    """Create a CommandDispatcher pre-loaded with all built-in commands."""
    dispatcher = CommandDispatcher()
    dispatcher.register("/stop", _handle_stop)
    dispatcher.register("/automation=", _handle_automation)
    return dispatcher
