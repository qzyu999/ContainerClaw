# ContainerClaw 🦀

A multi-agent system (MAS) where autonomous AI agents collaborate through elections, debate, and tool use — coordinated by a persistent event log (Apache Fluss).

## What it does

ContainerClaw runs a team of AI agents (Alice, Bob, Carol, David, Eve) that:
- **Vote** to decide who acts next (election protocol)
- **Use tools** — read/write files, run tests, manage a project board
- **Persist everything** to an append-only log (Fluss) — full audit trail
- **Work autonomously** or with human steering via the chat UI

## Architecture

```
Browser (React UI)  →  Bridge (SSE/REST)  →  Agent (gRPC, asyncio)
                                                ↓
                                          LLM Gateway  →  GPT / DeepSeek / Gemini / Local
                                                ↓
                                             Fluss (event log, Arrow-native)
```

All services run in Docker. One command starts everything.

## Quick Start

```bash
# Clone with submodule
git clone --recursive https://github.com/qzyu999/ContainerClaw.git
cd ContainerClaw

# Start the stack
docker compose up --build

# Open the UI
cd ui && npm install && npm run dev
# → http://localhost:5173/
```

**Requirements:** Docker, Docker Compose, Node.js 22+ (for UI dev server)

## Configuration

All configuration lives in `config.yaml`. Key sections:

| Section | Controls |
|---------|----------|
| `llm.providers` | LLM backends (OpenAI, Gemini, DeepSeek, local MLX) |
| `llm.default_provider` | Which provider to use |
| `agents.roster` | Agent names, personas, tools |
| `agents.settings` | Tool rounds, context limits, autonomy budget |
| `gateway` | Timeout, retry, rate limits |

### Enterprise / Custom Providers

For non-standard LLM endpoints (custom auth, non-standard URLs):

```bash
cp config.local.yaml.example config.local.yaml
# Edit with your provider details — this file is gitignored
```

The config loader deep-merges `config.local.yaml` on top of `config.yaml` at startup.

## Project Structure

```
ContainerClaw/
├── agent/              # Multi-agent system (Python, asyncio, gRPC)
│   └── src/            # reconciler, moderator, election, tools, publisher
├── llm-gateway/        # Provider-agnostic LLM router (FastAPI)
├── bridge/             # UI backend + programmatic API (Flask)
├── fluss/              # Universal Fluss cluster image (ZK + Coordinator + Tablet)
├── ui/                 # React frontend (Vite, TypeScript)
├── vendor/fluss-rust/  # Submodule: apache/fluss-rust (Python bindings via PyO3)
├── shared/             # Config loader (shared across containers)
├── config.yaml         # System configuration (tracked)
├── docker-compose.yml  # Full local stack
└── secrets/            # API keys (gitignored except placeholders)
```

## Programmatic API

No browser needed. Submit tasks and get results via HTTP:

```python
import requests

result = requests.post("http://localhost:5001/api/v1/run", json={
    "prompt": "Read main.py and explain what it does",
    "timeout_s": 120
}).json()

print(result["result"])       # Agent's final output
print(result["event_count"])  # How many events were generated
print(result["elapsed_s"])    # Wall-clock time
```

### Endpoints

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/api/v1/run` | POST | Submit task, wait for completion, return results |
| `/api/v1/status/{session_id}` | GET | Check session state (active/idle) |
| `/api/v1/sessions` | GET | List all sessions |
| `/api/v1/history/{session_id}` | GET | Full event trace |
| `/events/{session_id}` | GET (SSE) | Real-time event stream |

## How It Works

1. **Human sends a message** → published to Fluss event log
2. **Election** → each agent votes for who should act (via LLM)
3. **Winner executes** → reads files, writes code, runs commands
4. **Results published** → all events go to Fluss
5. **Next election** → repeat until agents vote "done"

The entire history is replayable from the Fluss log. Sessions persist across restarts.

## Key Design Decisions

- **Fluss over Kafka/Redis** — Arrow-native, zero-copy Python↔Rust, lightweight single-node
- **Election protocol** — prevents conflicting tool mutations (single-writer-per-cycle)
- **Gateway abstraction** — agent never knows which LLM it's talking to
- **Config overlays** — enterprise settings stay gitignored, OSS defaults are tracked

## Submodule (fluss-rust)

ContainerClaw uses `apache/fluss-rust` for Python bindings to Fluss:

```bash
# If vendor/fluss-rust is empty after clone:
git submodule update --init --recursive
```

This is compiled at Docker build time via maturin (~3 min first build, cached after).

## Development

```bash
# Format + lint
cd agent && pip install ruff && ruff check src/

# Run just the gateway (for LLM testing)
docker compose up llm-gateway

# Run the full stack without the UI
docker compose up fluss llm-gateway claw-agent ui-bridge

# Purge all state (fresh start)
docker compose down -v
rm -rf .fluss_data .zk_data .claw_state
```

## License

Apache 2.0
