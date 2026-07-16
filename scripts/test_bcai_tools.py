"""Quick test: Does the BCAI enterprise endpoint support OpenAI function calling?"""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("CLAW_CONFIG_PATH", str(ROOT / "config.yaml"))

from shared.config_loader import load_config
import requests

cfg = load_config()
provider = cfg.providers.get("enterprise")
if not provider:
    print("No 'enterprise' provider found in config. Available:", list(cfg.providers.keys()))
    sys.exit(1)

# Build request matching what ContainerClaw sends
url = f"{provider.base_url.rstrip('/')}/{provider.endpoint_path.lstrip('/')}"
headers = {"Content-Type": "application/json", "accept": "application/json"}
if provider.auth_scheme == "basic":
    headers["Authorization"] = f"basic {provider.api_key}"
else:
    headers["Authorization"] = f"Bearer {provider.api_key}"

# Test 1: Basic chat (no tools)
print(f"=== Test 1: Basic Chat ===")
print(f"URL: {url}")
print(f"Model: {cfg.default_model}")

payload_basic = {
    "model": cfg.default_model,
    "messages": [
        {"role": "system", "content": "You are a helpful assistant. Respond briefly."},
        {"role": "user", "content": "Say hello in exactly 3 words."}
    ],
    **provider.settings,
}

resp = requests.post(url, json=payload_basic, headers=headers, verify=False, timeout=60)
print(f"Status: {resp.status_code}")
if resp.status_code == 200:
    data = resp.json()
    msg = data.get("choices", [{}])[0].get("message", {})
    print(f"Response: {msg.get('content', '(empty)')}")
else:
    print(f"Error: {resp.text[:500]}")
    sys.exit(1)

# Test 2: JSON mode (voting)
print(f"\n=== Test 2: JSON Response Format ===")
payload_json = {
    "model": cfg.default_model,
    "messages": [
        {"role": "system", "content": "Respond only in valid JSON."},
        {"role": "user", "content": 'Return: {"vote": "Alice", "reason": "test"}'}
    ],
    "response_format": {"type": "json_object"},
    **provider.settings,
}

resp = requests.post(url, json=payload_json, headers=headers, verify=False, timeout=60)
print(f"Status: {resp.status_code}")
if resp.status_code == 200:
    data = resp.json()
    msg = data.get("choices", [{}])[0].get("message", {})
    print(f"Response: {msg.get('content', '(empty)')}")
else:
    print(f"Error: {resp.text[:500]}")

# Test 3: Tool calling
print(f"\n=== Test 3: Function Calling (tools) ===")
payload_tools = {
    "model": cfg.default_model,
    "messages": [
        {"role": "system", "content": "You are a coding assistant. Use tools when asked."},
        {"role": "user", "content": "Read the file README.md"}
    ],
    "tools": [{
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "Read a file from the workspace",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "File path to read"}
                },
                "required": ["path"]
            }
        }
    }],
    "tool_choice": "required",
    **provider.settings,
}

resp = requests.post(url, json=payload_tools, headers=headers, verify=False, timeout=60)
print(f"Status: {resp.status_code}")
if resp.status_code == 200:
    data = resp.json()
    msg = data.get("choices", [{}])[0].get("message", {})
    tool_calls = msg.get("tool_calls", [])
    print(f"Content: {msg.get('content', '(none)')}")
    print(f"Tool calls: {len(tool_calls)}")
    for tc in tool_calls:
        print(f"  -> {tc.get('function', {}).get('name')}: {tc.get('function', {}).get('arguments')}")
else:
    print(f"Error: {resp.text[:500]}")

print("\n=== Done ===")
