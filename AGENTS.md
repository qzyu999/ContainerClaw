# ContainerClaw — AI Agent Coding Guide

Guidance for AI coding agents contributing to ContainerClaw.

## Project Overview

ContainerClaw is a multi-agent system (MAS) where AI agents collaborate via elections and tool use, coordinated by an append-only event log (Apache Fluss). The codebase spans Python (agent, bridge), TypeScript (UI), Rust (Fluss SDK bindings), and Docker infrastructure.

## Architecture

```
agent/           → Python 3.12, asyncio, gRPC server
llm-gateway/     → Python 3.11, FastAPI/Gunicorn, provider routing
bridge/          → Python 3.12, Flask, SSE + REST API
fluss/           → Dockerfile for unified ZK + Fluss cluster
ui/              → TypeScript, React 19, Vite 8, Node 22
vendor/fluss-rust/ → Submodule (apache/fluss-rust), compiled via maturin
shared/          → Config loader (mounted into all containers)
```

## Critical Rules

### Dependencies
- **Fluss SDK**: Built from `vendor/fluss-rust` submodule via maturin. Do NOT add `pyfluss` to requirements.txt (PyPI version is incompatible).
- **Config**: All configuration comes from `config.yaml` via `shared/config_loader.py`. No scattered `os.getenv()` — use the `ClawConfig` Pydantic model.

### Async/Concurrency
- The agent runs on a **single asyncio event loop**. All Fluss calls use `future_into_py` from PyO3 — they are awaitable but NOT Python coroutines.
- Use `asyncio.ensure_future()` + `asyncio.shield()` when awaiting Fluss SDK futures concurrently with other operations (prevents deadlocks).
- The reconciliation loop body MUST complete in < 600ms. Long operations are dispatched as `asyncio.Task` and checked via `.done()`.

### Fluss SDK API
```python
# Connection
conn = await fluss.FlussConnection.create(config)
admin = conn.get_admin()              # SYNC — no await
table = await conn.get_table(path)    # ASYNC

# Writing
writer = table.new_append().create_writer()  # SYNC
writer.write_arrow_batch(batch)              # SYNC
await writer.flush()                         # ASYNC

# Reading
scanner = await table.new_scan().create_record_batch_log_scanner()  # ASYNC
scanner.subscribe_buckets({...})                                     # SYNC
batches = await scanner.poll_record_batch(timeout_ms)                # ASYNC (future_into_py)
```

### Code Style
- Type hints on all function signatures
- Constants at the top of modules (no magic numbers in function bodies)
- f-strings for logging with emoji prefixes (📤, ✅, ❌, ⏳, etc.)
- No star imports

### Testing
- Unit tests in `tests/`
- E2E via Docker Compose: `docker compose up --build`
- Programmatic API test: `POST http://localhost:5001/api/v1/run`

## Module Guide

### agent/src/

| File | Role |
|------|------|
| `main.py` | gRPC server, session lifecycle |
| `reconciler.py` | State machine: IDLE → ELECTING → EXECUTING → PUBLISHING |
| `moderator.py` | Election orchestration, context management, publisher init |
| `agent.py` | Per-agent LLM interaction, tool calling |
| `election.py` | Voting protocol (parallel LLM calls) |
| `publisher.py` | Batched async Fluss writer (100ms flush interval) |
| `fluss_client.py` | Connection management, scanner creation, session CRUD |
| `schemas.py` | PyArrow schemas for all Fluss tables |
| `tools.py` | Tool definitions (file ops, shell, board, search, etc.) |
| `tool_executor.py` | Tool call loop (send → execute → send results) |
| `context.py` | Token-budget-aware context window management |

### bridge/src/

| File | Role |
|------|------|
| `bridge.py` | Flask app: UI endpoints + `/api/v1/*` programmatic API + Fluss telemetry |

### llm-gateway/src/

| File | Role |
|------|------|
| `main.py` | FastAPI router, strategy loading |
| `providers/openai_strategy.py` | OpenAI-compatible passthrough (configurable auth/URL/SSL) |
| `providers/gemini_strategy.py` | Gemini protocol translation |

### shared/

| File | Role |
|------|------|
| `config_loader.py` | Loads `config.yaml` + optional `config.local.yaml` overlay, Pydantic validation |

## Build & Run

```bash
# Full stack
docker compose up --build

# Just the backend (no UI)
docker compose up fluss llm-gateway claw-agent ui-bridge

# Rebuild a single service
docker compose build claw-agent

# Clean state (fresh start)
docker compose down -v
rm -rf .fluss_data .zk_data .claw_state

# UI dev server (separate terminal)
cd ui && npm install && npm run dev
```

## Key Patterns

### Config Overlay
`config.local.yaml` (gitignored) is deep-merged on top of `config.yaml`. Use it for private provider credentials:
```yaml
llm:
  providers:
    my-provider:
      type: openai
      base_url: "https://my-endpoint.example.com"
      endpoint_path: "/chat"
      auth_scheme: "basic"
      api_key_secret: "my_secret"
  default_provider: "my-provider"
```

### Provider Settings Injection
The `settings` dict in a provider config is auto-injected into every LLM request payload as defaults:
```yaml
settings:
  conversation_source: "containerclaw"
  stream: false
```

### Publisher Pattern
All writes to Fluss go through `FlussPublisher`:
```python
event_id = await publisher.publish(actor_id, content, m_type, ...)
```
Records are buffered and flushed every 100ms or when batch size (50) is reached.

### Election Flow
```
Human message → Reconciler activates → Election (N agents vote in parallel)
→ Winner chosen → Tool execution rounds → Publish results
→ Next election → Repeat until is_done consensus
```

## Common Pitfalls

1. **Fluss `flush()` hangs** — wrap in `asyncio.ensure_future()` + `asyncio.shield()` if called concurrently with other Fluss operations
2. **`_publisher_ready` race** — always call `self.mod._publisher_ready.set()` after creating the publisher (see `reconciler.py`)
3. **Docker apt fails** — corporate networks block port 80; the Dockerfiles force HTTPS apt sources
4. **UI won't build in Docker** — npm registry is unreliable from Docker on restricted networks; run UI dev server locally instead
5. **`poll_record_batch` returns None** — always default with `or []` before iterating

## Git Conventions

- Commit prefix: `[component]` — e.g. `[agent]`, `[gateway]`, `[infra]`, `[ui]`, `[bridge]`
- Never commit `config.local.yaml`, `docker-compose.enterprise.yml`, or private secret files
- Submodule: `vendor/fluss-rust` → pinned at a specific commit on `apache/fluss-rust`
