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

@app.route("/v1/chat/completions", methods=["POST"])
def proxy():
    body = request.get_json()
    model = body.get("model", "").lower()
    
    import sys
    print(f"Proxying request for model: {model}", file=sys.stderr)
    sys.stderr.flush()

    # 1. Gemini Routing
    if "gemini" in model:
        if not is_active("gemini"):
            return Response("Gemini API key is not configured.", status=401)
        
        # Map OpenAI-style messages to Gemini contents
        contents = []
        for m in body.get("messages", []):
            role = "user" if m["role"] == "user" else "model"
            contents.append({
                "role": role,
                "parts": [{"text": m["content"]}]
            })
        
        gemini_payload = {"contents": contents}
        
        # We try v1beta as the primary, and handle specific errors
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={KEYS['gemini']}"
        
        try:
            print(f"Requesting Gemini URL: {url.split('?')[0]}?key=REDACTED", file=sys.stderr)
            sys.stderr.flush()
            resp = session.post(
                url, 
                headers={"Content-Type": "application/json"}, 
                json=gemini_payload, 
                verify=certifi.where(),
                timeout=90
            )
            
            if resp.status_code != 200:
                print(f"Gemini error response ({resp.status_code}): {resp.text}")
                
            return Response(resp.text, status=resp.status_code, content_type="application/json")
            
        except requests.exceptions.SSLError as e:
            print(f"SSL Error proxying to Gemini: {str(e)}")
            return Response(f"SSL error: {str(e)}", status=500)
        except Exception as e:
            print(f"General Error proxying to Gemini: {str(e)}")
            return Response(f"General error: {str(e)}", status=500)
        
    # 2. Anthropic Routing
    elif "claude" in model:
        if not is_active("anthropic"):
            return Response("Anthropic API key is not configured.", status=401)
        # Placeholder for transformation logic
        return Response("Anthropic integration pending full mapping.", status=501)

    # 3. OpenAI / Default Routing
    else:
        if not is_active("openai"):
            return Response(f"No active API key found for model: {model}", status=401)
        url = "https://api.openai.com/v1/chat/completions"
        headers = {
            "Authorization": f"Bearer {KEYS['openai']}",
            "Content-Type": "application/json"
        }
        try:
            resp = session.post(url, json=body, headers=headers, timeout=90)
            return Response(resp.text, status=resp.status_code, content_type=resp.headers.get("content-type"))
        except Exception as e:
            print(f"Error proxying to OpenAI: {str(e)}")
            return Response(str(e), status=500)

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8000)
