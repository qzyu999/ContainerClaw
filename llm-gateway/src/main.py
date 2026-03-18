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
    try:
        data = request.json
        api_key = data.get('api_key') or open("/run/secrets/gemini_api_key").read().strip()
        model_id = data.get('model', 'gemini-3-flash-preview')
        
        # 1. TRANSLATE: OpenAI messages -> Gemini contents
        gemini_messages = []
        for m in data.get('messages', []):
            # Gemini roles are strictly 'user' or 'model'
            role = "model" if m['role'] == "assistant" else "user"
            gemini_messages.append({
                "role": role,
                "parts": [{"text": m['content']}] # Gemini's required nesting
            })

        # 2. CONSTRUCT: The real Google API payload
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_id}:generateContent?key={api_key}"
        
        payload = {
            "contents": gemini_messages,
            "system_instruction": {
                "parts": [{"text": data.get('system_instruction', '')}]
            },
            "generationConfig": {
                "response_mime_type": data.get('response_mime_type', 'text/plain'),
                "temperature": 0.7
            }
        }

        # 3. FORWARD: Hit the internet (the Gateway has egress access!)
        res = requests.post(url, json=payload, timeout=30)
        
        # If Google returns an error (400/403), return it to the Agent so we can see why
        if res.status_code != 200:
            print(f"⚠️ Google API Error: {res.text}")
            return res.json(), res.status_code

        return res.json(), 200

    except Exception as e:
        print(f"🔥 Gateway Crash: {e}")
        return {"error": str(e)}, 500

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8000)
