"""
Unified module for constructing LLM context windows.
"""

from shared.config_loader import ClawConfig

class ContextBuilder:
    @staticmethod
    def build_payload(
        raw_messages: list[dict],
        config: ClawConfig,
        actor_id: str,
        system_prompt: str,
        extra_turns: list[dict] | None = None,
        anchor_text: str = "",
        is_json: bool = False,
    ) -> list[dict]:
        """Build the complete messages payload enforcing limits and format.

        Applies the Token Guard (character limit) dynamically, prioritizing
        the system prompt, anchor text, and any extra tool turns, and then fitting as
        much of the history as possible.
        """
        messages_payload = [{"role": "system", "content": system_prompt}]
        budget = config.max_history_chars - len(system_prompt) - len(anchor_text)
        
        extra = extra_turns or []
        for turn in extra:
            content = str(turn.get("content", ""))
            budget -= len(content)
            
        if budget < 0:
            print(f"⚠️ [ContextBuilder] Token Guard: extra turns and anchor alone exceed budget for {actor_id}!")
             
        recent_messages = raw_messages[-config.max_history_messages:]
        final_history = []
        
        for msg in reversed(recent_messages):
            content = str(msg.get("content", ""))
            actor = msg.get("actor_id", "System")
            role = "assistant" if actor == actor_id else "user"
            
            if actor == "Moderator":
                text = f"[Moderator Note]: {content}"
            elif role == "user":
                text = f"{actor}: {content}"
            else:
                text = content
                
            msg_len = len(text)
            if budget - msg_len < 0:
                print(f"⚠️ [ContextBuilder] Token Guard triggered. Truncating history at {len(final_history)} msgs.")
                break
            
            final_history.insert(0, {"role": role, "content": text})
            budget -= msg_len
            
        # 1. Add the main conversation history
        messages_payload.extend(final_history)
        
        # 2. Add directives BEFORE the active tool chain
        if anchor_text:
            messages_payload.append({"role": "user", "content": f"[ANCHOR — Operator Directive]: {anchor_text}"})

        if is_json:
            formatting_directive = (
                "[CRITICAL FORMATTING]: You must output ONLY raw, valid JSON. "
                "Do not use Markdown code blocks. Do not start your response with ```json. "
                "Start your response immediately with the { character."
            )
            messages_payload.append({"role": "user", "content": formatting_directive})
            
        # 3. Add the active tool chain LAST so the model remembers it is mid-action
        messages_payload.extend(extra)
        
        return messages_payload
