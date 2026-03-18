import os
import signal
import sys
import time
import threading
import asyncio
import grpc
import concurrent.futures
from moderator import StageModerator, GeminiAgent
import fluss
import pyarrow as pa

# Generated gRPC stubs
import agent_pb2
import agent_pb2_grpc

class AgentService(agent_pb2_grpc.AgentServiceServicer):
    def __init__(self, fluss_conn, table):
        self.session_id = os.getenv("CLAW_SESSION_ID", "default-session")
        self.is_running = True
        self.event_queues = {} 
        self.fluss_conn = fluss_conn
        self.table = table
        
        # Start the Moderator in a background thread
        self.loop = asyncio.new_event_loop()
        threading.Thread(target=self._run_moderator_thread, daemon=True).start()

    def _run_moderator_thread(self):
        asyncio.set_event_loop(self.loop)
        # Load API Key
        try:
            api_key = open("/run/secrets/gemini_api_key", "r").read().strip()
        except:
            api_key = os.getenv("GEMINI_API_KEY")

        agents = [
            GeminiAgent("Alice", "Software architect.", api_key),
            GeminiAgent("Bob", "Project manager.", api_key),
            GeminiAgent("Carol", "Software engineer.", api_key),
            GeminiAgent("David", "Software QA tester.", api_key),
            GeminiAgent("Eve", "Business user.", api_key)
        ]
        
        autonomous_steps = int(os.getenv("AUTONOMOUS_STEPS", "-1"))
        self.moderator = StageModerator(self.table, agents, self._bridge_to_ui)
        print("--- ⚖️ STAGE ACTIVE (Democratic Moderator) ---")
        self.loop.run_until_complete(self.moderator.run(autonomous_steps=autonomous_steps))

    def _bridge_to_ui(self, actor_id, content, e_type):
        q = self._get_queue(self.session_id)
        q.put(agent_pb2.ActivityEvent(
            timestamp=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            type=e_type,
            content=content,
            actor_id=actor_id
        ))

    def _get_queue(self, session_id):
        if session_id not in self.event_queues:
            import queue
            self.event_queues[session_id] = queue.Queue()
        return self.event_queues[session_id]

    def ExecuteTask(self, request, context):
        print(f"📥 Received task from UI: {request.prompt}")
        
        # We wrap this in a future so we can catch errors in the logs
        future = asyncio.run_coroutine_threadsafe(
            self.moderator.publish("Human", request.prompt), 
            self.loop
        )
        
        # Add a logging callback
        def done_callback(f):
            try:
                f.result()
                print("📝 Successfully wrote 'Human' message to Fluss.")
            except Exception as e:
                print(f"❌ FAILED to write to Fluss: {e}")

        future.add_done_callback(done_callback)
        return agent_pb2.TaskStatus(accepted=True, message="Task received.")

    def StreamActivity(self, request, context):
        session_id = request.session_id
        q = self._get_queue(session_id)
        
        try:
            # Send Handshake
            yield agent_pb2.ActivityEvent(
                timestamp=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                type="thought",
                content=f"Connected to session: {session_id}"
            )

            while self.is_running:
                # Check if the client is still there
                if not context.is_active():
                    break
                try:
                    event = q.get(timeout=1.0)
                    yield event
                except:
                    continue
        finally:
            print(f"🔌 Cleanly disconnected from session: {session_id}")

async def init_infrastructure():
    print("🛰️ Initializing Fluss Infrastructure...")
    config = fluss.Config({"bootstrap.servers": "coordinator-server:9123"})
    
    # Retry connection — coordinator may not be listening yet
    conn = None
    for attempt in range(30):
        try:
            conn = await fluss.FlussConnection.create(config)
            print("✅ Connected to Fluss Coordinator.")
            break
        except Exception as e:
            print(f"⏳ Waiting for Fluss Coordinator (attempt {attempt+1}/30)... {e}")
            await asyncio.sleep(2)
    
    if not conn:
        raise Exception("❌ Failed to connect to Fluss Coordinator after 30 attempts.")
    
    admin = await conn.get_admin()
    
    table_path = fluss.TablePath("containerclaw", "chatroom")
    await admin.create_database("containerclaw", ignore_if_exists=True)
    
    # Define Schema
    schema = pa.schema([
        pa.field("ts", pa.int64()), 
        pa.field("actor_id", pa.string()), 
        pa.field("content", pa.string())
    ])
    descriptor = fluss.TableDescriptor(fluss.Schema(schema), bucket_count=1)

    # 1. Wait for Metadata/Creation
    print("⏳ Waiting for Table Creation...")
    for attempt in range(15):
        try:
            await admin.create_table(table_path, descriptor, ignore_if_exists=True)
            print(f"✅ Coordinator confirmed: {table_path} exists.")
            break
        except Exception as e:
            await asyncio.sleep(3)

    # 2. Wait for Data Plane Visibility (THE FIX)
    print("💎 Attempting to connect to Data Plane...")
    table = None
    for attempt in range(10):
        try:
            table = await conn.get_table(table_path)
            print("🚀 Successfully connected to Table Data Plane.")
            break
        except Exception as e:
            print(f"⏳ Table not found in local metadata yet (attempt {attempt+1}/10)...")
            await asyncio.sleep(2)
            
    if not table:
        raise Exception("❌ Failed to resolve Table Data Plane after 10 attempts.")
        
    return conn, table

def serve():
    # 1. Block until Fluss is ready
    conn, table = asyncio.run(init_infrastructure())
    
    # 2. Start gRPC
    server = grpc.server(concurrent.futures.ThreadPoolExecutor(max_workers=10))
    agent_service = AgentService(conn, table)
    agent_pb2_grpc.add_AgentServiceServicer_to_server(agent_service, server)
    server.add_insecure_port('0.0.0.0:50051')
    server.start()
    print("🚀 Agent gRPC Server Online on port 50051.")
    server.wait_for_termination()

if __name__ == "__main__":
    serve()