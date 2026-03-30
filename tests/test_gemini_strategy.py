import json
import sys
import os
from pathlib import Path

# Add project root and specific modules to path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "llm-gateway" / "src"))
sys.path.insert(0, str(PROJECT_ROOT / "shared"))

from providers.gemini_strategy import GeminiStrategy
from config_loader import ProviderConfig

def test_gemini_strategy_preserves_thoughts():
    """
    Cryptographically freezes the '_gemini_parts' out-of-band tunnel.
     Ensures that Gemini's proprietary `thought_signature` parts are perfectly
    preserved during translation and re-injection.
    """
    # llm-gateway initializes strategies using raw parsed dicts, not the Pydantic models (yet)
    prov = {
        "name": "test-gemini",
        "type": "gemini",
        "base_url": "https://generativelanguage.googleapis.com/v1beta",
        "api_key": "test-key",
        "settings": {}
    }
    strategy = GeminiStrategy(prov)

    # 1. Mock a raw response from Gemini containing a thought signature and a function call
    mock_gemini_response = {
        "candidates": [{
            "content": {
                "parts": [
                    {"text": "I should call a tool."},
                    {"thought_signature": "0x1234abcd", "thought": "Analyzing the need for..."},
                    {"functionCall": {"name": "read_file", "args": {"path": "main.py"}}}
                ],
                "role": "model"
            }
        }]
    }

    # 2. Extract into OpenAI format (simulating gateway receive)
    openai_resp = strategy._from_gemini(mock_gemini_response, "gemini-3-flash-preview")

    # The returned message MUST contain our hidden '_gemini_parts' field
    msg = openai_resp["choices"][0]["message"]
    assert "_gemini_parts" in msg, "_gemini_parts hook was stripped during translation!"
    assert len(msg["_gemini_parts"]) == 3
    assert "thought_signature" in msg["_gemini_parts"][1]

    # 3. Simulate Agent sending the history back in the next turn
    mock_agent_request = {
        "model": "gemini-3-flash-preview",
        "messages": [
            {"role": "user", "content": "Hello"},
            msg  # The assistant message with the injected _gemini_parts
        ]
    }

    # 4. Translate back to Gemini format
    gemini_payload = strategy._to_gemini(mock_agent_request, "gemini-3-flash-preview")

    # The assistant message MUST bypass standard translation and inject the raw parts
    assert len(gemini_payload["contents"]) == 2
    assistant_contents = gemini_payload["contents"][1]
    assert assistant_contents["role"] == "model"
    
    # Verify the native signature is still intact!
    injected_parts = assistant_contents["parts"]
    assert len(injected_parts) == 3
    assert injected_parts[1].get("thought_signature") == "0x1234abcd", "Thought signature was lost during re-injection!"
