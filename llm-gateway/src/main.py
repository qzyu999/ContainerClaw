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

# Set up a resilient requests session
session = requests.Session()
retry_strategy = Retry(
    total=3,
    backoff_factor=1,
    status_forcelist=[429, 500, 502, 503, 504],
)
adapter = HTTPAdapter(max_retries=retry_strategy)
session.mount("https://", adapter)
session.mount("http://", adapter)

@app.route('/v1/chat/completions', methods=['POST'])
def proxy():
    data = request.json
    api_key = data.get('api_key') or os.getenv("GEMINI_API_KEY")
    model = data.get('model', 'gemini-3-flash-preview')
    
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}"
    
    # Mirror the Agent's payload exactly as it was in your base code
    google_payload = {
        "contents": data.get('contents', []),
        "system_instruction": {"parts": [{"text": data.get('system_instruction', '')}]},
        "generationConfig": data.get('generationConfig', {})
    }
    
    res = requests.post(url, json=google_payload, timeout=60)
    return res.json(), res.status_code

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8000)
