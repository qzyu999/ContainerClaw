"""
Context window management for ContainerClaw moderator.

Owns the in-memory message cache (populated from Fluss replay and
live polling) and provides token-budget-aware windowing for LLM
context construction.
"""

import config


class ContextManager:
    """Manages the in-memory message history and LLM context windowing.
    
    Responsibilities:
    - Deduplication of messages via timestamp-actor key
    - Token-budget-aware context window slicing
    - Cache trimming to prevent unbounded memory growth
    """

    def __init__(self):
        self.all_messages: list[dict] = []
        self.history_keys: set[str] = set()

    def add_message(self, actor_id: str, content: str, ts: int,
                     event_id: str | None = None) -> bool:
        """Add a message to the cache. Returns True if it was new (not a duplicate).
        
        Uses event_id (UUID) as the primary dedup key when available,
        falling back to ts-actor_id for backward compatibility with 
        legacy records that lack event_id.
        """
        key = event_id if event_id else f"{ts}-{actor_id}"
        if key in self.history_keys:
            return False

        self.history_keys.add(key)
        self.all_messages.append({
            "actor_id": actor_id,
            "content": content,
            "ts": ts,
        })
        return True

    def get_window(self, size: int | None = None) -> list[dict]:
        """Return the most recent messages.
        
        Token guard and precise truncation is now delegated to the shared ContextBuilder
        during payload assembly.

        Args:
            size: Max number of messages to return. Defaults to config.MAX_HISTORY_MESSAGES.
        
        Returns:
            List of message dicts in chronological order.
        """
        n = size or config.MAX_HISTORY_MESSAGES
        return self.all_messages[-n:]

    def sort(self):
        """Ensure messages are in strict chronological order."""
        self.all_messages.sort(key=lambda x: x["ts"])

    def trim(self):
        """Trim in-memory cache to prevent unbounded growth."""
        max_in_memory = config.MAX_HISTORY_MESSAGES * 3
        if len(self.all_messages) > max_in_memory:
            self.all_messages = self.all_messages[-config.MAX_HISTORY_MESSAGES * 2:]
            self.history_keys.clear()
            print(f"Sweep: Trimmed in-memory history to {len(self.all_messages)} messages.")
