import grpc
import sys
import os

# Add agent/src to path to import generated protos
sys.path.append(os.path.join(os.getcwd(), "agent", "src"))

import agent_pb2
import agent_pb2_grpc

def run():
    # Connect to the agent running in the container
    # Since we are running on the host, we hit localhost:50051 (published port)
    channel = grpc.insecure_channel('localhost:50051')
    stub = agent_pb2_grpc.AgentServiceStub(channel)

    print("🦀 ContainerClaw Client Initialized")
    print("-----------------------------------")
    
    prompt = input("Enter a task for the Agent: ")
    
    # 1. Execute Task
    response = stub.ExecuteTask(agent_pb2.TaskRequest(prompt=prompt, session_id="user-session"))
    print(f"\n[AGENT RESPONSE] {response.message}\n")

    # 2. Stream Activity
    print("Streaming live activity...")
    print("-----------------------------------")
    try:
        for event in stub.StreamActivity(agent_pb2.ActivityRequest(session_id="user-session")):
            print(f"[{event.timestamp}] [{event.type.upper()}] {event.content}")
    except grpc.RpcError as e:
        print(f"\n[ERROR] Could not connect to Agent. Is the container running? ({e.code()})")

if __name__ == "__main__":
    run()
