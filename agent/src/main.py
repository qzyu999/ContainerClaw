import os
import signal
import sys
import time
import concurrent.futures
import grpc
import requests
import json
import subprocess
import threading
import queue

# Import generated gRPC code
import agent_pb2
import agent_pb2_grpc

class AgentService(agent_pb2_grpc.AgentServiceServicer):
    def __init__(self):
        self.gateway_url = os.getenv("LLM_GATEWAY_URL", "http://llm-gateway:8000")
        self.session_id = os.getenv("CLAW_SESSION_ID", "default-session")
        self.is_running = True
        self.event_queues = {} # session_id -> Queue

    def _get_queue(self, session_id):
        if session_id not in self.event_queues:
            self.event_queues[session_id] = queue.Queue()
        return self.event_queues[session_id]

    def _emit(self, session_id, e_type, content):
        q = self._get_queue(session_id)
        event = agent_pb2.ActivityEvent(
            timestamp=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            type=e_type,
            content=content,
            risk_score=0.1
        )
        q.put(event)

    def ExecuteTask(self, request, context):
        print(f"Received task: {request.prompt}")
        session_id = request.session_id
        # Start the autonomous loop in a background thread
        thread = threading.Thread(target=self._run_loop, args=(session_id, request.prompt))
        thread.daemon = True
        thread.start()
        return agent_pb2.TaskStatus(accepted=True, message="Agent loop activated.")

    def StreamActivity(self, request, context):
        q = self._get_queue(request.session_id)
        print(f"User started streaming activity for session: {request.session_id}")
        
        while self.is_running:
            try:
                event = q.get(timeout=2.0)
                yield event
                # Stop streaming if we get a termination type
                if event.type in ["error", "finish"]:
                    break
            except queue.Empty:
                continue

    def _run_loop(self, session_id, prompt):
        self._emit(session_id, "thought", f"Starting autonomous loop for prompt: {prompt}")
        
        try:
            # 1. Ask Gemini for a plan
            # Using 'gemini-1.5-flash' which is the standard name
            plan = self._call_llm(session_id, f"Plan the following task: {prompt}")
            self._emit(session_id, "thought", f"Plan developed: {plan[:100]}...")
            
            # Simple demonstration of a tool call
            self._emit(session_id, "thought", "Executing step 1: Environment check")
            result = self._execute_command(session_id, "ls -la /workspace")
            self._emit(session_id, "output", result)
            
            self._emit(session_id, "thought", "Task sequence complete.")
            self._emit(session_id, "finish", "Execution finished.")
        except Exception as e:
            self._emit(session_id, "error", f"Autonomous loop failed: {str(e)}")

    def _call_llm(self, session_id, prompt):
        max_retries = 3
        retry_delay = 2
        
        # We target an available gemini model
        payload = {
            "model": "gemini-2.5-flash",
            "messages": [{"role": "user", "content": prompt}]
        }
        
        for attempt in range(max_retries):
            try:
                resp = requests.post(
                    f"{self.gateway_url}/v1/chat/completions",
                    json=payload,
                    timeout=30
                )
                if resp.status_code == 200:
                    data = resp.json()
                    # Handle both Google format and OpenAI format for convenience
                    if "candidates" in data:
                        return data["candidates"][0]["content"]["parts"][0]["text"]
                    return data["choices"][0]["message"]["content"]
                
                print(f"Attempt {attempt+1}: Gateway returned {resp.status_code}")
            except Exception as e:
                print(f"Attempt {attempt+1} failed: {str(e)}")
            
            if attempt < max_retries - 1:
                time.sleep(retry_delay)
                retry_delay *= 2
        
        raise Exception("Failed to reach LLM Gateway after multiple retries")

    def _execute_command(self, session_id, cmd):
        self._emit(session_id, "tool_call", cmd)
        try:
            # We enforce running in /workspace
            result = subprocess.check_output(cmd, shell=True, stderr=subprocess.STDOUT, cwd="/workspace")
            return result.decode("utf-8")
        except subprocess.CalledProcessError as e:
            return f"Error: {e.output.decode('utf-8')}"

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
