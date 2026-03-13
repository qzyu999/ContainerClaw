import os
import signal
import sys
import time
import concurrent.futures
import grpc
import requests
import json
import subprocess

# Import generated gRPC code
import agent_pb2
import agent_pb2_grpc

class AgentService(agent_pb2_grpc.AgentServiceServicer):
    def __init__(self):
        self.gateway_url = os.getenv("LLM_GATEWAY_URL", "http://llm-gateway:8000")
        self.session_id = os.getenv("CLAW_SESSION_ID", "default-session")
        self.is_running = True
        self.active_tasks = {}

    def ExecuteTask(self, request, context):
        print(f"Received task: {request.prompt}")
        # In a real implementation, we'd start a background thread for the thought loop
        # For Phase 1/2 MVP, we acknowledge the task and start a mock sequence
        return agent_pb2.TaskStatus(accepted=True, message="Task accepted and execution loop started.")

    def StreamActivity(self, request, context):
        print(f"User started streaming activity for session: {request.session_id}")
        
        # This is a sample autonomous sequence
        events = [
            ("thought", f"Planning task: {request.session_id}"),
            ("thought", "Step 1: Analyzing workspace directory..."),
            ("tool_call", "ls -R /workspace"),
            ("output", "Found README.md, docker-compose.yml"),
            ("thought", "Step 2: Checking Gemini API availability via Gateway..."),
            ("thought", "Gemini API connection successful. Ready for autonomous actions.")
        ]
        
        for e_type, content in events:
            if not self.is_running:
                break
            yield agent_pb2.ActivityEvent(
                timestamp=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                type=e_type,
                content=content,
                risk_score=0.1
            )
            time.sleep(1.5)

def serve():
    server = grpc.server(concurrent.futures.ThreadPoolExecutor(max_workers=10))
    agent_pb2_grpc.add_AgentServiceServicer_to_server(AgentService(), server)
    server.add_insecure_port('0.0.0.0:50051')
    
    print("ContainerClaw Agent gRPC Server starting on port 50051...")
    server.start()
    
    def handle_exit(signum, frame):
        print(f"SIGTERM received ({signum}). Shutting down...")
        server.stop(0)
        sys.exit(0)

    signal.signal(signal.SIGTERM, handle_exit)
    signal.signal(signal.SIGINT, handle_exit)
    
    try:
        while True:
            time.sleep(86400)
    except KeyboardInterrupt:
        server.stop(0)

if __name__ == "__main__":
    serve()
