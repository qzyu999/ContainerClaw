import grpc
import sys
import os
import time
import json
import requests

# Add agent/src to path
sys.path.append(os.path.join(os.getcwd(), "agent", "src"))
import agent_pb2
import agent_pb2_grpc

def test():
    channel = grpc.insecure_channel('localhost:50051')
    stub = agent_pb2_grpc.AgentServiceStub(channel)
    session_id = "test-session-123"

    print(f"--- Phase 1: Initial Task ---")
    prompt = "Think about why 42 is the answer to everything. Create a plan to research it. Write '42 is life' to life.txt."
    response = stub.ExecuteTask(agent_pb2.TaskRequest(prompt=prompt, session_id=session_id))
    print(f"Response: {response.message}")

    # Wait for completion (poll logs)
    print("Waiting for agent to finish...")
    for _ in range(30):
        time.sleep(2)
        r = requests.get(f"http://localhost:9092/v1/logs/{session_id}")
        logs = r.json()
        if any(l.get("event_type") == "finish" for l in logs):
            print("Agent finished!")
            break
    else:
        print("Timeout waiting for agent.")
        return

    print("\n--- Phase 2: Verify Persistence on Disk ---")
    state_dir = f".claw_state/{session_id}"
    for f in ["history.json", "plan.json", "workspace_hashes.json"]:
        path = os.path.join(os.getcwd(), state_dir, f)
        if os.path.exists(path):
            print(f"[OK] Found {f}")
            with open(path, "r") as json_f:
                data = json.load(json_f)
                if f == "plan.json":
                    print(f"Plan Content: {json.dumps(data)}")
        else:
            print(f"[FAIL] Missing {f}")

    print("\n--- Phase 3: Resume Verification ---")
    # We don't restart containers here to save time, but we simulate a new request to the same session
    resume_prompt = "What was the previous plan you created? Also, what is in life.txt?"
    response = stub.ExecuteTask(agent_pb2.TaskRequest(prompt=resume_prompt, session_id=session_id))
    
    print("Waiting for resumption logs...")
    for _ in range(15):
        time.sleep(2)
        r = requests.get(f"http://localhost:9092/v1/logs/{session_id}")
        logs = r.json()
        # Look for messages after the first "finish"
        finish_indices = [i for i, l in enumerate(logs) if l.get("event_type") == "finish"]
        if len(finish_indices) > 1:
            print("Resumption finished!")
            # Print the thought where it mentions the previous plan
            for l in logs[finish_indices[0]:]:
                if l.get("event_type") == "thought":
                    print(f"Agent Thought: {l['payload']['content']}")
            break
    else:
        print("Timeout waiting for resumption.")

if __name__ == "__main__":
    test()
