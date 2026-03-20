import os
import requests
from flask import Flask, request, Response
import json
import certifi
import urllib3
from urllib3.util.retry import Retry
from requests.adapters import HTTPAdapter

app = Flask(__name__)

# Load keys from Docker Secrets
def get_secret(name):
    try:
        with open(f"/run/secrets/{name}", "r") as f:
            return f.read().strip()
    except Exception:
        return None

# Load all potential keys
KEYS = {
    "gemini": get_secret("gemini_api_key"),
    "anthropic": get_secret("anthropic_api_key"),
    "openai": get_secret("openai_api_key")
}

def is_active(key_name):
    key = KEYS.get(key_name)
    return key and not key.startswith("placeholder")

# Set up a resilient requests session with connection pooling + SSL retry
session = requests.Session()
retry_strategy = Retry(
    total=3,
    backoff_factor=1,
    status_forcelist=[429, 500, 502, 503, 504],
    allowed_methods=["POST"],        # Allow retry on POST (needed for LLM calls)
    raise_on_status=False,           # Don't raise — let us handle status codes
)
adapter = HTTPAdapter(
    max_retries=retry_strategy,
    pool_connections=10,             # Connection pool size per host
    pool_maxsize=10,                 # Max connections per pool
)
session.mount("https://", adapter)
session.mount("http://", adapter)
session.verify = certifi.where()     # Explicit CA bundle

@app.route('/v1/chat/completions', methods=['POST'])
def proxy():
    data = request.json
    # Priority: payload key → Docker secret → env var
    api_key = data.get('api_key') or KEYS.get('gemini') or os.getenv("GEMINI_API_KEY")
    
    if not api_key:
        return {"error": "No Gemini API key available (checked payload, secrets, env)"}, 500
    
    model = data.get('model', 'gemini-3-flash-preview')
    
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}"

    # Extract config and ensure thinking is enabled for SWE-bench
    gen_config = data.get('generationConfig', {})

    # Force 'HIGH' thinking if not specified and using a Gemini 3 model
    if 'gemini-3' in model:
        # Set thinking_level if it isn't already there
        if 'thinking_config' not in gen_config:
            gen_config['thinking_config'] = {'thinking_level': 'HIGH'}
        
        # SWE-bench often needs more than the default 4k tokens for complex fixes
        gen_config.setdefault('max_output_tokens', 8192)

    google_payload = {
        "contents": data.get('contents', []),
        "system_instruction": {"parts": [{"text": data.get('system_instruction', '')}]},
        "generationConfig": gen_config,
        "tools": data.get('tools', [])
    }
    
    try:
        # Use the resilient session (connection pooling + automatic retry on failure)
        res = session.post(url, json=google_payload, timeout=90)
        return res.json(), res.status_code
    except Exception as e:
        return {"error": f"Gateway request failed: {str(e)}"}, 502

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8000)
