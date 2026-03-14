import os
import json
from flask import Flask, request, Response, jsonify

app = Flask(__name__)
LOG_DIR = "/var/lib/fluss/logs"
os.makedirs(LOG_DIR, exist_ok=True)

@app.route("/v1/events", methods=["POST"])
def ingest():
    event = request.get_json()
    session_id = event.get("session_id", "default")
    
    # Save to disk (append-only JSONL)
    log_path = os.path.join(LOG_DIR, f"{session_id}.jsonl")
    with open(log_path, "a") as f:
        f.write(json.dumps(event) + "\n")
    
    return "OK", 200

@app.route("/v1/logs/<session_id>", methods=["GET"])
def get_logs(session_id):
    log_path = os.path.join(LOG_DIR, f"{session_id}.jsonl")
    if not os.path.exists(log_path):
        return jsonify([])
    
    logs = []
    with open(log_path, "r") as f:
        for line in f:
            logs.append(json.loads(line))
    return jsonify(logs)

@app.route("/v1/logs/<session_id>/stream", methods=["GET"])
def stream_logs(session_id):
    def generate():
        log_path = os.path.join(LOG_DIR, f"{session_id}.jsonl")
        # Tail-like implementation
        import time
        last_pos = 0
        while True:
            if os.path.exists(log_path):
                with open(log_path, "r") as f:
                    f.seek(last_pos)
                    for line in f:
                        yield f"data: {line}\n\n"
                    last_pos = f.tell()
            time.sleep(1)
            
    return Response(generate(), mimetype="text/event-stream")

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=9092)
