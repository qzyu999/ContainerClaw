import os
from flask import Flask, request, Response
import requests
import certifi
import json

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

@app.route("/v1/chat/completions", methods=["POST"])
def proxy():
    body = request.get_json()
    model = body.get("model", "").lower()
    
    # 1. Gemini Routing
    if "gemini" in model:
        if not is_active("gemini"):
            return Response("Gemini API key is not configured.", status=401)
        
        last_msg = body["messages"][-1]["content"]
        gemini_payload = {"contents": [{"parts": [{"text": last_msg}]}]}
        
        # Try v1beta first, fallback to v1 if it fails with 404
        endpoints = [
            f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={KEYS['gemini']}",
            f"https://generativelanguage.googleapis.com/v1/models/{model}:generateContent?key={KEYS['gemini']}"
        ]
        
        last_resp = None
        for url in endpoints:
            try:
                resp = requests.post(url, headers={"Content-Type": "application/json"}, json=gemini_payload, verify=certifi.where())
                if resp.status_code != 404:
                    return Response(resp.text, status=resp.status_code, content_type="application/json")
                # Store as a flask Response if we want to return it later
                last_resp = Response(resp.text, status=resp.status_code, content_type="application/json")
            except Exception as e:
                print(f"FAILED TO CALL GEMINI AT {url}: {str(e)}")
                import traceback
                traceback.print_exc()
                last_resp = Response(str(e), status=500)
        
        return last_resp if last_resp else Response("No endpoints reachable", status=502)
        
    # 2. Anthropic Routing
    elif "claude" in model:
        if not is_active("anthropic"):
            return Response("Anthropic API key is not configured.", status=401)
        url = "https://api.anthropic.com/v1/messages"
        headers = {
            "x-api-key": KEYS["anthropic"],
            "anthropic-version": "2023-06-01",
            "content-type": "application/json"
        }
        # Transformation logic can be added here

    # 3. OpenAI / Default Routing
    else:
        if not is_active("openai"):
            return Response(f"No active API key found for model: {model}", status=401)
        url = "https://api.openai.com/v1/chat/completions"
        headers = {
            "Authorization": f"Bearer {KEYS['openai']}",
            "Content-Type": "application/json"
        }

    # Audit the request (log to Fluss placeholder)
    print(f"Proxying request for model: {model}")
    
    resp = requests.post(url, json=body, headers=headers, stream=True)
    
    return Response(resp.iter_content(chunk_size=1024), 
                    status=resp.status_code, 
                    content_type=resp.headers.get("content-type"))

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8000)
