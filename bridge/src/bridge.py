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


# ── Telemetry Endpoints (Engine-Agnostic) ─────────────────────────

def _init_duckdb(db_path):
    """Create and initialize the DuckDB file with telemetry schema if it doesn't exist."""
    import duckdb
    conn = duckdb.connect(db_path)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS dag_edges (
            session_id VARCHAR NOT NULL,
            parent_id VARCHAR NOT NULL,
            child_id VARCHAR NOT NULL,
            status VARCHAR DEFAULT 'ACTIVE',
            created_at BIGINT,
            updated_at BIGINT,
            PRIMARY KEY (session_id, parent_id, child_id)
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS agent_context_snorkel (
            agent_id VARCHAR NOT NULL,
            session_id VARCHAR NOT NULL,
            run_id VARCHAR DEFAULT '',
            context_json JSON,
            last_updated_at BIGINT,
            PRIMARY KEY (agent_id, session_id)
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS live_metrics (
            session_id VARCHAR NOT NULL,
            window_start BIGINT NOT NULL,
            total_messages INTEGER DEFAULT 0,
            tool_calls INTEGER DEFAULT 0,
            tool_successes INTEGER DEFAULT 0,
            avg_latency_ms DOUBLE DEFAULT 0.0,
            PRIMARY KEY (session_id, window_start)
        )
    """)
    conn.close()
    print(f"Bridge: Initialized DuckDB at {db_path}")


def get_telemetry_connection():
    """Return a DB-API 2.0 connection based on the configured engine.

    Returns None if telemetry is not enabled, allowing endpoints
    to gracefully degrade with a 404.
    """
    engine = os.getenv("TELEMETRY_ENGINE", "")
    if not engine:
        return None
    if engine == "duckdb":
        import duckdb
        db_path = os.getenv("TELEMETRY_DUCKDB_PATH", "/state/telemetry.duckdb")
        if not os.path.exists(db_path):
            _init_duckdb(db_path)
        return duckdb.connect(db_path, read_only=True)
    elif engine == "starrocks":
        import pymysql
        return pymysql.connect(
            host=os.getenv("STARROCKS_HOST", "starrocks-fe"),
            port=int(os.getenv("STARROCKS_PORT", "9030")),
            user="root",
            database="containerclaw",
        )
    else:
        print(f"⚠️ Bridge: Unknown telemetry engine: {engine}")
        return None


@app.route("/telemetry/dag/<session_id>")
def telemetry_dag(session_id):
    """Return DAG edges for the swarm visualization."""
    conn = get_telemetry_connection()
    if not conn:
        return {"status": "error", "message": "Telemetry not enabled"}, 404
    try:
        rows = conn.execute(
            "SELECT parent_id, child_id, status, updated_at FROM dag_edges WHERE session_id = ?",
            [session_id],
        ).fetchall()
        edges = [
            {"parent": r[0], "child": r[1], "status": r[2], "updated_at": r[3]}
            for r in rows
        ]
        return {"status": "ok", "edges": edges}
    except Exception as e:
        print(f"Bridge: Telemetry DAG Error: {e}")
        return {"status": "error", "message": str(e)}, 500


@app.route("/telemetry/snorkel/<agent_id>/<session_id>")
def telemetry_snorkel(agent_id, session_id):
    """Return the materialized context window for a specific agent."""
    conn = get_telemetry_connection()
    if not conn:
        return {"status": "error", "message": "Telemetry not enabled"}, 404
    try:
        row = conn.execute(
            "SELECT context_json, last_updated_at FROM agent_context_snorkel WHERE agent_id = ? AND session_id = ?",
            [agent_id, session_id],
        ).fetchone()
        if row:
            return {"status": "ok", "context": row[0], "updated_at": row[1]}
        return {"status": "ok", "context": None}
    except Exception as e:
        print(f"Bridge: Telemetry Snorkel Error: {e}")
        return {"status": "error", "message": str(e)}, 500


@app.route("/telemetry/metrics/<session_id>")
def telemetry_metrics(session_id):
    """Return aggregated metrics for HUD sparklines."""
    conn = get_telemetry_connection()
    if not conn:
        return {"status": "error", "message": "Telemetry not enabled"}, 404
    try:
        rows = conn.execute(
            "SELECT window_start, total_messages, tool_calls, tool_successes, avg_latency_ms "
            "FROM live_metrics WHERE session_id = ? ORDER BY window_start DESC LIMIT 60",
            [session_id],
        ).fetchall()
        metrics = [
            {
                "window_start": r[0],
                "total_messages": r[1],
                "tool_calls": r[2],
                "tool_successes": r[3],
                "avg_latency_ms": r[4],
            }
            for r in rows
        ]
        return {"status": "ok", "metrics": metrics}
    except Exception as e:
        print(f"Bridge: Telemetry Metrics Error: {e}")
        return {"status": "error", "message": str(e)}, 500


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5001, threaded=True)

