import os
import sys
import time
import json
import queue
import threading
from flask import Flask, Response, request
from flask_cors import CORS
import grpc

# Add proto to path
sys.path.append(os.path.join(os.path.dirname(__file__), "proto"))

import agent_pb2
import agent_pb2_grpc

app = Flask(__name__)
CORS(app) # Allow frontend to hit the bridge

AGENT_URL = os.getenv("AGENT_URL", "agent:50051")

def get_grpc_stub():
    # Retry logic for initial connection (DNS/startup race)
    for i in range(5):
        try:
            channel = grpc.insecure_channel(AGENT_URL)
            # Try to check if channel is ready
            grpc.channel_ready_future(channel).result(timeout=2)
            return agent_pb2_grpc.AgentServiceStub(channel)
        except Exception as e:
            print(f"Bridge: Agent not ready yet (attempt {i+1}): {e}")
            time.sleep(2)
    
    # Fallback to a non-ready channel if retries fail
    channel = grpc.insecure_channel(AGENT_URL)
    return agent_pb2_grpc.AgentServiceStub(channel)

@app.route("/events/<session_id>")
def stream_events(session_id):
    def generate():
        print(f"Bridge: Starting SSE stream for session {session_id}")
        stub = get_grpc_stub()
        try:
            # Consume gRPC stream
            stream = stub.StreamActivity(agent_pb2.ActivityRequest(session_id=session_id))
            for event in stream:
                data = {
                    "timestamp": event.timestamp,
                    "type": event.type,
                    "content": event.content,
                    "risk_score": event.risk_score
                }
                # SSE Format: data: <json>\n\n
                yield f"data: {json.dumps(data)}\n\n"
        except grpc.RpcError as e:
            print(f"Bridge: gRPC Error: {e}")
            yield f"data: {json.dumps({'type': 'error', 'content': f'Connection to agent lost: {e.code()}'})}\n\n"
        except Exception as e:
            print(f"Bridge: Unexpected Error: {e}")
            yield f"data: {json.dumps({'type': 'error', 'content': str(e)})}\n\n"

    return Response(generate(), mimetype="text/event-stream")

@app.route("/task", methods=["POST"])
def proxy_task():
    data = request.json
    session_id = data.get("session_id", "default-session")
    prompt = data.get("prompt", "")
    
    # Simple retry for task submission
    for i in range(3):
        try:
            stub = get_grpc_stub()
            response = stub.ExecuteTask(agent_pb2.TaskRequest(prompt=prompt, session_id=session_id))
            return {"status": "ok", "message": response.message}
        except grpc.RpcError as e:
            if e.code() == grpc.StatusCode.UNAVAILABLE and i < 2:
                print(f"Bridge: Agent unavailable, retrying task {i+1}...")
                time.sleep(2)
                continue
            return {"status": "error", "message": f"gRPC Error: {e.code()} - {e.details()}"}, 500
        except Exception as e:
            return {"status": "error", "message": str(e)}, 500

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5001, threaded=True)
