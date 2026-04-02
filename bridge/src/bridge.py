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

AGENT_URL = "localhost:50051"

def get_grpc_stub():
    # 60 attempts * 2s = 2 minutes of patience
    # This is plenty of time for the Fluss Tablet Server to boot
    for i in range(60): 
        try:
            channel = grpc.insecure_channel(AGENT_URL)
            # This is the "Python version" of the nc command
            # It blocks until port 50051 is actually open
            grpc.channel_ready_future(channel).result(timeout=2)
            print(f"✅ Bridge: Connected to Agent on attempt {i+1}")
            return agent_pb2_grpc.AgentServiceStub(channel)
        except Exception:
            if i % 5 == 0:
                print(f"⏳ Bridge: Waiting for Agent gRPC... (Attempt {i+1}/60)")
            time.sleep(2)
    
    raise Exception("❌ Bridge: Timeout waiting for Agent.")

@app.route("/sessions")
def list_sessions():
    """List available sessions from the agent registry."""
    try:
        stub = get_grpc_stub()
        response = stub.ListSessions(agent_pb2.Empty())
        sessions = [
            {
                "session_id": s.session_id,
                "title": s.title,
                "created_at": s.created_at,
                "last_active_at": s.last_active_at
            }
            for s in response.sessions
        ]
        return {"status": "ok", "sessions": sessions}
    except Exception as e:
        print(f"Bridge: ListSessions Error: {e}")
        return {"status": "error", "message": str(e)}, 500

@app.route("/sessions/new", methods=["POST"])
def create_session():
    """Create a new session via the agent."""
    data = request.json or {}
    title = data.get("title", "")
    try:
        stub = get_grpc_stub()
        s = stub.CreateSession(agent_pb2.CreateSessionRequest(title=title))
        return {
            "status": "ok",
            "session": {
                "session_id": s.session_id,
                "title": s.title,
                "created_at": s.created_at,
                "last_active_at": s.last_active_at
            }
        }
    except Exception as e:
        print(f"Bridge: CreateSession Error: {e}")
        return {"status": "error", "message": str(e)}, 500

@app.route("/events/<session_id>")
def stream_events(session_id):
    def generate():
        print(f"Bridge: Starting SSE stream for session {session_id}", flush=True)
        try:
            stub = get_grpc_stub()
            # Consume gRPC stream
            stream = stub.StreamActivity(agent_pb2.ActivityRequest(session_id=session_id))
            for event in stream:
                data = {
                    "timestamp": event.timestamp,
                    "type": event.type,
                    "content": event.content,
                    "risk_score": event.risk_score,
                    "actor_id": event.actor_id
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
                print(f"Bridge: Agent unavailable, retrying task {i+1}...", flush=True)
                time.sleep(2)
                continue
            return {"status": "error", "message": f"gRPC Error: {e.code()} - {e.details()}"}, 500
        except Exception as e:
            return {"status": "error", "message": str(e)}, 500

@app.route("/history/<session_id>")
def history_stream(session_id):
    """Fetch full chat history for a session."""
    try:
        stub = get_grpc_stub()
        response = stub.GetHistory(agent_pb2.ActivityRequest(session_id=session_id))
        events = [
            {
                "timestamp": event.timestamp,
                "type": event.type,
                "content": event.content,
                "risk_score": event.risk_score,
                "actor_id": event.actor_id
            }
            for event in response.events
        ]
        return {"status": "ok", "events": events}
    except Exception as e:
        print(f"Bridge: GetHistory Error: {e}", flush=True)
        return {"status": "error", "message": str(e)}, 500

@app.route("/board/<session_id>")
def get_board(session_id):
    """Fetch project board items from the agent."""
    try:
        stub = get_grpc_stub()
        response = stub.GetBoard(agent_pb2.ActivityRequest(session_id=session_id))
        items = [
            {
                "id": item.id,
                "type": item.type,
                "title": item.title,
                "description": item.description,
                "status": item.status,
                "assigned_to": item.assigned_to or None,
                "created_at": item.created_at,
            }
            for item in response.items
        ]
        return {"status": "ok", "items": items}
    except Exception as e:
        print(f"Bridge: GetBoard Error: {e}", flush=True)
        return {"status": "error", "message": str(e)}, 500

@app.route("/workspace/<session_id>")
def list_workspace(session_id):
    """List workspace files (backward compatible endpoint)."""
    try:
        stub = get_grpc_stub()
        response = stub.ListWorkspace(agent_pb2.WorkspaceRequest(session_id=session_id))
        files = [{"path": f.path, "is_directory": f.is_directory,
                  "size_bytes": f.size_bytes, "modified_at": f.modified_at}
                 for f in response.files]
        return {"status": "ok", "files": files}
    except Exception as e:
        return {"status": "error", "message": str(e)}, 500

@app.route("/workspace/<session_id>/tree")
def workspace_tree(session_id):
    """Recursive directory listing for Explorer file tree."""
    try:
        stub = get_grpc_stub()
        response = stub.ListWorkspace(agent_pb2.WorkspaceRequest(session_id=session_id))
        files = [{"path": f.path, "is_directory": f.is_directory,
                  "size_bytes": f.size_bytes, "modified_at": f.modified_at}
                 for f in response.files]
        return {"status": "ok", "files": files}
    except Exception as e:
        return {"status": "error", "message": str(e)}, 500

@app.route("/workspace/<session_id>/file")
def workspace_file(session_id):
    """Return file contents for Monaco editor."""
    path = request.args.get("path", "")
    if not path:
        return {"status": "error", "message": "Missing 'path' query parameter"}, 400
    try:
        stub = get_grpc_stub()
        response = stub.ReadFile(agent_pb2.FileRequest(session_id=session_id, path=path))
        return {"status": "ok", "content": response.content,
                "language": response.language, "path": response.path}
    except grpc.RpcError as e:
        return {"status": "error", "message": f"{e.code()}: {e.details()}"}, 404
    except Exception as e:
        return {"status": "error", "message": str(e)}, 500

@app.route("/workspace/<session_id>/diff")
def workspace_diff(session_id):
    """Return diff data for Monaco diff view."""
    path = request.args.get("path", "")
    if not path:
        return {"status": "error", "message": "Missing 'path' query parameter"}, 400
    try:
        stub = get_grpc_stub()
        response = stub.DiffFile(agent_pb2.DiffRequest(session_id=session_id, path=path))
        return {"status": "ok", "original": response.original,
                "modified": response.modified, "diff_text": response.diff_text}
    except grpc.RpcError as e:
        return {"status": "error", "message": f"{e.code()}: {e.details()}"}, 404
    except Exception as e:
        return {"status": "error", "message": str(e)}, 500


# ── Telemetry Endpoints (Fluss-Native) ─────────────────────────────
#
# Reads from Fluss PK tables written by the Flink telemetry job.
# - dag_edges:    log scan filtered by session_id
# - live_metrics: point lookup by session_id (O(1))
#
# Uses a dedicated background event loop thread to run async Fluss
# operations, since Flask threads can't use asyncio.run() safely.

import asyncio
import concurrent.futures

# Dedicated event loop for Fluss async operations
_fluss_loop = asyncio.new_event_loop()
_fluss_thread = threading.Thread(target=_fluss_loop.run_forever, daemon=True)
_fluss_thread.start()

_fluss_conn = None
_fluss_tables = {}


def _run_async(coro):
    """Run an async coroutine on the dedicated Fluss event loop thread."""
    future = asyncio.run_coroutine_threadsafe(coro, _fluss_loop)
    return future.result(timeout=15)


async def _ensure_fluss_conn():
    """Lazy-init a persistent Fluss connection."""
    global _fluss_conn
    if _fluss_conn is not None:
        return _fluss_conn

    bootstrap = os.getenv("FLUSS_BOOTSTRAP_SERVERS", "")
    if not bootstrap:
        return None

    import fluss
    config = fluss.Config({"bootstrap.servers": bootstrap})
    _fluss_conn = await fluss.FlussConnection.create(config)
    print(f"✅ Bridge: Connected to Fluss at {bootstrap}")
    return _fluss_conn


async def _get_table(table_name):
    """Get a Fluss table handle, cached."""
    import fluss
    if table_name in _fluss_tables:
        return _fluss_tables[table_name]

    conn = await _ensure_fluss_conn()
    if conn is None:
        return None

    try:
        table_path = fluss.TablePath("containerclaw", table_name)
        table = await conn.get_table(table_path)
        _fluss_tables[table_name] = table
        return table
    except Exception as e:
        print(f"⚠️ Bridge: Table '{table_name}' not available: {e}")
        return None


async def _lookup_dag_edges(session_id):
    """Scan the chatroom log table and project DAG edges.

    The chatroom log (append-only) contains parent_event_id and edge_type
    fields on each event. We project these into edge dicts for the UI.
    This avoids scanning the dag_edges PK table (which doesn't support
    log scanning in the Fluss Python SDK).
    """
    table = await _get_table("chatroom")
    if table is None:
        return []

    edges = []
    try:
        import fluss
        conn = await _ensure_fluss_conn()
        admin = await conn.get_admin()
        table_path = fluss.TablePath("containerclaw", "chatroom")
        table_info = await admin.get_table_info(table_path)
        num_buckets = table_info.num_buckets

        scanner = await table.new_scan().create_record_batch_log_scanner()
        scanner.subscribe_buckets(
            {b: fluss.EARLIEST_OFFSET for b in range(num_buckets)}
        )

        for _ in range(20):  # Poll up to 20 times
            batches = await scanner._async_poll_batches(500)
            if not batches:
                break
            for record_batch in batches:
                batch = record_batch.batch
                sid_arr = batch.column("session_id")
                eid_arr = batch.column("event_id")
                actor_arr = batch.column("actor_id")
                type_arr = batch.column("type")
                ts_arr = batch.column("ts")

                # Fetch content for smart labels
                has_content = "content" in batch.schema.names
                content_arr = batch.column("content") if has_content else None

                has_parent_event_id = "parent_event_id" in batch.schema.names
                has_edge_type = "edge_type" in batch.schema.names
                parent_eid_arr = batch.column("parent_event_id") if has_parent_event_id else None
                edge_type_arr = batch.column("edge_type") if has_edge_type else None

                for i in range(batch.num_rows):
                    if sid_arr[i].as_py() != session_id:
                        continue

                    parent_eid = parent_eid_arr[i].as_py() if parent_eid_arr else ""
                    edge_type = edge_type_arr[i].as_py() if edge_type_arr else "SEQUENTIAL"
                    event_type = type_arr[i].as_py()
                    actor = actor_arr[i].as_py()
                    
                    # Safely extract and decode content
                    raw_content = content_arr[i].as_py() if content_arr else ""
                    content = raw_content.decode("utf-8") if isinstance(raw_content, bytes) else str(raw_content)

                    if event_type in ("finish", "done", "checkpoint"):
                        status = "DONE"
                    elif event_type in ("action", "voting"):
                        status = "THINKING"
                    elif event_type == "thought":
                        status = "DONE"  # Thoughts are past events, not actively computing
                    else:
                        status = "ACTIVE"

                    if event_type == "checkpoint":
                        label = "Checkpoint"
                    elif event_type == "finish":
                        label = "Task Complete"
                    elif "Starting Election" in content:
                        label = "Election"
                    elif "Winner:" in content:
                        # e.g., "🏆 Winner: Alice" -> crop if it's too long
                        label = content[:25]
                    else:
                        label = actor

                    edges.append({
                        "parent": parent_eid if parent_eid else "ROOT",
                        "child": eid_arr[i].as_py(),
                        "child_label": label,
                        "edge_type": edge_type if edge_type else "SEQUENTIAL",
                        "status": status,
                        "updated_at": ts_arr[i].as_py(),
                        "ts": ts_arr[i].as_py(),
                    })
    except Exception as e:
        print(f"Bridge: DAG edges scan error: {e}")
        import traceback
        traceback.print_exc()

    return edges


@app.route("/telemetry/dag/<session_id>")
def telemetry_dag(session_id):
    """Return DAG edges by scanning the chatroom log table directly."""
    bootstrap = os.getenv("FLUSS_BOOTSTRAP_SERVERS", "")
    if not bootstrap:
        return {"status": "ok", "edges": []}
    try:
        edges = _run_async(_lookup_dag_edges(session_id))
        return {"status": "ok", "edges": edges}
    except Exception as e:
        print(f"Bridge: Telemetry DAG Error: {e}")
        return {"status": "ok", "edges": []}


@app.route("/telemetry/dag/<session_id>/stream")
def telemetry_dag_stream(session_id):
    """SSE endpoint: tail the chatroom log for real-time DAG edge updates."""
    bootstrap = os.getenv("FLUSS_BOOTSTRAP_SERVERS", "")
    if not bootstrap:
        return Response("data: []\n\n", mimetype="text/event-stream")

    def generate():
        import fluss
        try:
            table = _run_async(_get_table("chatroom"))
            if table is None:
                yield f"data: {json.dumps({'type': 'error', 'message': 'chatroom table not available'})}\n\n"
                return

            conn = _run_async(_ensure_fluss_conn())
            admin = _run_async(conn.get_admin())
            table_path = fluss.TablePath("containerclaw", "chatroom")
            table_info = _run_async(admin.get_table_info(table_path))
            num_buckets = table_info.num_buckets

            scanner = _run_async(
                table.new_scan().create_record_batch_log_scanner()
            )
            # Subscribe from LATEST — only new events after this point
            scanner.subscribe_buckets(
                {b: fluss.LATEST_OFFSET for b in range(num_buckets)}
            )

            while True:
                batches = _run_async(scanner._async_poll_batches(1000))
                if not batches:
                    yield ": heartbeat\n\n"
                    continue
                for record_batch in batches:
                    batch = record_batch.batch
                    sid_arr = batch.column("session_id")
                    eid_arr = batch.column("event_id")
                    actor_arr = batch.column("actor_id")
                    type_arr = batch.column("type")
                    ts_arr = batch.column("ts")

                    # Fetch content for smart labels
                    has_content = "content" in batch.schema.names
                    content_arr = batch.column("content") if has_content else None

                    has_parent_event_id = "parent_event_id" in batch.schema.names
                    has_edge_type = "edge_type" in batch.schema.names
                    parent_eid_arr = batch.column("parent_event_id") if has_parent_event_id else None
                    edge_type_arr = batch.column("edge_type") if has_edge_type else None

                    for i in range(batch.num_rows):
                        if sid_arr[i].as_py() != session_id:
                            continue

                        parent_eid = parent_eid_arr[i].as_py() if parent_eid_arr else ""
                        edge_type = edge_type_arr[i].as_py() if edge_type_arr else "SEQUENTIAL"
                        event_type = type_arr[i].as_py()
                        actor = actor_arr[i].as_py()

                        # Safely extract and decode content
                        raw_content = content_arr[i].as_py() if content_arr else ""
                        content = raw_content.decode("utf-8") if isinstance(raw_content, bytes) else str(raw_content)

                        # --- FIX P4: Better Status Mapping ---
                        if event_type in ("finish", "done", "checkpoint"):
                            status = "DONE"
                        elif event_type in ("action", "voting"):
                            status = "THINKING"
                        elif event_type == "thought":
                            status = "DONE"  # Thoughts are past events
                        else:
                            status = "ACTIVE"

                        # --- FIX P3: Smart Content-Derived Labels ---
                        if event_type == "checkpoint":
                            label = "Checkpoint"
                        elif event_type == "finish":
                            label = "Task Complete"
                        elif "Starting Election" in content:
                            label = "Election"
                        elif "Winner:" in content:
                            label = content[:25]
                        else:
                            label = actor

                        edge = {
                            "parent": parent_eid if parent_eid else "ROOT",
                            "child": eid_arr[i].as_py(),
                            "child_label": label,
                            "edge_type": edge_type if edge_type else "SEQUENTIAL",
                            "status": status,
                            "updated_at": ts_arr[i].as_py(),
                            "ts": ts_arr[i].as_py(),
                        }
                        yield f"data: {json.dumps(edge)}\n\n"
        except GeneratorExit:
            return
        except Exception as e:
            print(f"Bridge: DAG Stream Error: {e}")
            yield f"data: {json.dumps({'type': 'error', 'message': str(e)})}\n\n"

    return Response(generate(), mimetype="text/event-stream")


async def _lookup_metrics(session_id):
    """Point lookup on live_metrics PK table. O(1)."""
    table = await _get_table("live_metrics")
    if table is None:
        return None

    lookuper = table.new_lookup().create_lookuper()
    result = await lookuper.lookup({"session_id": session_id})
    return result


@app.route("/telemetry/metrics/<session_id>")
def telemetry_metrics(session_id):
    """Return aggregated metrics for HUD sparklines."""
    bootstrap = os.getenv("FLUSS_BOOTSTRAP_SERVERS", "")
    if not bootstrap:
        return {"status": "ok", "metrics": []}
    try:
        result = _run_async(_lookup_metrics(session_id))
        if result is None:
            return {"status": "ok", "metrics": []}
        metrics = [{
            "window_start": result.get("last_updated_at", 0),
            "total_messages": result.get("total_messages", 0),
            "tool_calls": result.get("tool_calls", 0),
            "tool_successes": result.get("tool_successes", 0),
            "avg_latency_ms": 0.0,
        }]
        return {"status": "ok", "metrics": metrics}
    except Exception as e:
        print(f"Bridge: Telemetry Metrics Error: {e}")
        return {"status": "ok", "metrics": []}


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5001, threaded=True)


