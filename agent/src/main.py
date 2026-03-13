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
        self.histories = {}    # session_id -> list of messages
        self.state_root = "/workspace/.claw_state"
        
        # Load any existing state for the default session on startup
        self._load_session_state(self.session_id)

    def _get_state_path(self, session_id):
        return os.path.join(self.state_root, session_id)

    def _load_session_state(self, session_id):
        """Loads session history and plan from disk if they exist."""
        state_dir = self._get_state_path(session_id)
        history_path = os.path.join(state_dir, "history.json")
        
        if os.path.exists(history_path):
            try:
                with open(history_path, "r") as f:
                    self.histories[session_id] = json.load(f)
                print(f"[{session_id}] Loaded history from {history_path}")
            except Exception as e:
                print(f"[{session_id}] Error loading history: {e}")

    def checkpoint_session(self, session_id=None):
        """Saves current session state to disk."""
        if session_id is None:
            session_id = self.session_id
            
        state_dir = self._get_state_path(session_id)
        os.makedirs(state_dir, exist_ok=True)
        
        history = self.histories.get(session_id)
        if history:
            history_path = os.path.join(state_dir, "history.json")
            try:
                with open(history_path, "w") as f:
                    json.dump(history, f, indent=2)
                print(f"[{session_id}] Checkpointed history to {history_path}")
            except Exception as e:
                print(f"[{session_id}] Error saving history: {e}")

    def _get_history(self, session_id):
        if session_id not in self.histories:
            self.histories[session_id] = []
        return self.histories[session_id]

    def _get_queue(self, session_id):
        if session_id not in self.event_queues:
            self.event_queues[session_id] = queue.Queue()
        return self.event_queues[session_id]

    def _emit(self, session_id, e_type, content):
        print(f"[{session_id}] [{e_type.upper()}] {content}")
        q = self._get_queue(session_id)
        event = agent_pb2.ActivityEvent(
            timestamp=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            type=e_type,
            content=content,
            risk_score=0.1
        )
        q.put(event)

    def ExecuteTask(self, request, context):
        print(f"Received task for session {request.session_id}: {request.prompt}")
        session_id = request.session_id
        
        # Clear the queue for this session to avoid log leakage from previous runs
        if session_id in self.event_queues:
            q = self.event_queues[session_id]
            while not q.empty():
                try:
                    q.get_nowait()
                except queue.Empty:
                    break
        
        # Start the autonomous loop in a background thread
        thread = threading.Thread(target=self._run_loop, args=(session_id, request.prompt))
        thread.daemon = True
        thread.start()
        return agent_pb2.TaskStatus(accepted=True, message="Agent loop activated.")

    def StreamActivity(self, request, context):
        session_id = request.session_id
        q = self._get_queue(session_id)
        print(f"User started streaming activity for session: {session_id}")
        
        # 1. Yield existing history as "history" type events first
        history = self._get_history(session_id)
        for msg in history:
            role = msg.get("role", "thought")
            content = msg.get("content", "")
            # Map role to event type
            e_type = "thought" if role == "assistant" else "user"
            if "Observation:" in content: e_type = "output"
            
            event = agent_pb2.ActivityEvent(
                timestamp=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                type=e_type,
                content=content,
                risk_score=0.1
            )
            yield event

        # 2. Continue streaming new events
        while self.is_running:
            try:
                event = q.get(timeout=2.0)
                yield event
                # Stop streaming if we get a termination type
                if event.type in ["error", "finish"]:
                    break
            except queue.Empty:
                continue

    def ListWorkspace(self, request, context):
        items = self._ls_workspace(request.session_id)
        # _ls_workspace returns a string with \n, split it
        files = items.split("\n") if items != "(empty directory)" and "Error" not in items else []
        return agent_pb2.WorkspaceResponse(files=files)

    def _execute_command(self, session_id, command):
        """Executes a command in the sandbox and returns the output."""
        try:
            # We restrict commands for safety here even though Seccomp is active
            allowed_cmds = ["ls", "cat", "git", "echo", "pwd", "mkdir"]
            base_cmd = command.split()[0]
            if base_cmd not in allowed_cmds and base_cmd != "python":
                 return f"Error: Command '{base_cmd}' not in allowed list."

            import subprocess
            result = subprocess.run(
                command,
                shell=True,
                capture_output=True,
                text=True,
                timeout=10,
                cwd="/workspace"
            )
            return result.stdout if result.returncode == 0 else result.stderr
        except Exception as e:
            return f"Execution error: {str(e)}"

    def _write_file(self, session_id, path, content):
        """Writes content to a file in the workspace."""
        try:
            full_path = os.path.join("/workspace", path.lstrip("/"))
            os.makedirs(os.path.dirname(full_path), exist_ok=True)
            with open(full_path, "w") as f:
                f.write(content)
            return f"Successfully wrote to {path}"
        except Exception as e:
            return f"Write error: {str(e)}"

    def _read_file(self, session_id, path):
        """Reads content from a file in the workspace."""
        try:
            full_path = os.path.join("/workspace", path.lstrip("/"))
            if not os.path.exists(full_path):
                return f"Error: File '{path}' does not exist."
            with open(full_path, "r") as f:
                return f.read()
        except Exception as e:
            return f"Read error: {str(e)}"

    def _ls_workspace(self, session_id, path="."):
        """Lists files in the workspace or a subdirectory."""
        try:
            full_path = os.path.join("/workspace", path.lstrip("/"))
            if not os.path.isdir(full_path):
                return f"Error: '{path}' is not a directory."
            items = os.listdir(full_path)
            # Filter out the .claw_state directory to prevent agent from messing with its own state
            items = [i for i in items if i != ".claw_state"]
            return "\n".join(items) if items else "(empty directory)"
        except Exception as e:
            return f"List error: {str(e)}"

    def _run_loop(self, session_id, prompt):
        self._emit(session_id, "thought", f"Starting autonomous loop for: {prompt}")
        history = self._get_history(session_id)
        
        # System-like instruction for the loop
        instruction = (
            "You are an autonomous agent. Plan your steps and use tools. "
            "Available tools: execute_command(cmd), write_file(path, content), read_file(path), ls_workspace(path). "
            "Respond in JSON format: {\"thought\": \"...\", \"tool\": \"execute_command\", \"args\": \"ls -la\"} "
            "Or for files: {\"thought\": \"...\", \"tool\": \"write_file\", \"path\": \"...\", \"content\": \"...\"} "
            "Or finish: {\"thought\": \"...\", \"finish\": \"message\"}"
        )
        
        current_messages = history + [{"role": "user", "content": f"Context: {instruction}\n\nTask: {prompt}"}]
        
        max_steps = 5
        for step in range(max_steps):
            try:
                # 1. Ask Gemini for next action
                response_text = self._call_llm(session_id, current_messages)
                
                # Robust JSON extraction
                import re
                json_match = re.search(r'\{.*\}', response_text, re.DOTALL)
                try:
                    if json_match:
                        action = json.loads(json_match.group())
                    else:
                        raise ValueError("No JSON block found")
                except Exception as e:
                    print(f"JSON Parse Error: {e}. Raw: {response_text}")
                    # Fallback if Gemini doesn't follow JSON exactly
                    action = {"thought": response_text, "finish": "Task concluded (fallback)"}

                self._emit(session_id, "thought", action.get("thought", "Thinking..."))
                
                if "finish" in action:
                    history.append({"role": "user", "content": prompt})
                    history.append({"role": "assistant", "content": action["thought"]})
                    self._emit(session_id, "finish", action["finish"])
                    self.checkpoint_session(session_id)
                    break
                
                # 2. Execute Tool
                tool_name = action.get("tool")
                args = action.get("args")
                
                output = "Unknown tool"
                if tool_name == "execute_command":
                    self._emit(session_id, "thought", f"Executing: {args}")
                    output = self._execute_command(session_id, args)
                elif tool_name == "write_file":
                    path = action.get("path")
                    content = action.get("content")
                    self._emit(session_id, "thought", f"Writing to: {path}")
                    output = self._write_file(session_id, path, content)
                elif tool_name == "read_file":
                    path = action.get("path")
                    self._emit(session_id, "thought", f"Reading: {path}")
                    output = self._read_file(session_id, path)
                elif tool_name == "ls_workspace":
                    path = action.get("path", ".")
                    self._emit(session_id, "thought", f"Listing: {path}")
                    output = self._ls_workspace(session_id, path)
                
                self._emit(session_id, "output", output)
                
                # 3. Feed back to history and checkpoint
                # We update the persistent history, not just the local current_messages
                history.append({"role": "assistant", "content": response_text})
                history.append({"role": "user", "content": f"Observation: {output}"})
                
                current_messages.append({"role": "assistant", "content": response_text})
                current_messages.append({"role": "user", "content": f"Observation: {output}"})
                self.checkpoint_session(session_id)
                
            except Exception as e:
                self._emit(session_id, "error", f"Autonomous loop step {step} failed: {str(e)}")
                break
        else:
            self._emit(session_id, "finish", "Task timed out after max steps.")

    def _call_llm(self, session_id, messages):
        max_retries = 8 # Increased retries
        retry_delay = 5

        
        payload = {
            # "model": "gemini-2.5-flash", # Hardcoded for now
            "model": "gemini-2.5-flash-lite", # Hardcoded for now
            "messages": messages
        }
        
        for attempt in range(max_retries):
            try:
                resp = requests.post(
                    f"{self.gateway_url}/v1/chat/completions",
                    json=payload,
                    timeout=60 # Increased timeout
                )
                if resp.status_code == 200:
                    data = resp.json()
                    if "candidates" in data:
                        return data["candidates"][0]["content"]["parts"][0]["text"]
                    return data["choices"][0]["message"]["content"]
                elif resp.status_code == 429:
                    error_msg = "Quota Exceeded (429). Please try again later or use a different API key."
                    self._emit(session_id, "error", error_msg)
                    raise Exception(error_msg)
                else:
                    print(f"Attempt {attempt + 1}: Gateway returned {resp.status_code}")
                    print(f"Response body: {resp.text}")
                    if attempt == max_retries - 1:
                        raise Exception(f"Gateway error {resp.status_code}: {resp.text}")
            except Exception as e:
                print(f"Attempt {attempt+1} failed: {str(e)}")
            
            if attempt < max_retries - 1:
                time.sleep(retry_delay)
                retry_delay *= 2
        
        raise Exception("Failed to reach LLM Gateway after multiple retries")


def serve():
    server = grpc.server(concurrent.futures.ThreadPoolExecutor(max_workers=10))
    def handle_exit(signum, frame):
        print(f"SIGTERM received ({signum}). Saving state...")
        # Checkpoint all active sessions
        for sid in list(agent_service.histories.keys()):
            agent_service.checkpoint_session(sid)
        server.stop(0)
        sys.exit(0)

    agent_service = AgentService()
    agent_pb2_grpc.add_AgentServiceServicer_to_server(agent_service, server)
    server.add_insecure_port('0.0.0.0:50051')
    
    print("ContainerClaw Agent gRPC Server starting on port 50051...")
    server.start()

    signal.signal(signal.SIGTERM, handle_exit)
    signal.signal(signal.SIGINT, handle_exit)
    
    try:
        while True:
            time.sleep(86400)
    except KeyboardInterrupt:
        server.stop(0)

if __name__ == "__main__":
    serve()
