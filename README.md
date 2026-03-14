# ContainerClaw 🦀

ContainerClaw is a secure, defense-in-depth, and containerized runtime for autonomous AI agents. Unlike traditional agents that run natively on your machine, ContainerClaw executes in an isolated sandbox, shielding your host files and credentials from prompt-injection attacks and rouge AI behavior.

---

## 🚀 Getting Started

### 1. Prerequisites
- Docker & Docker Compose
- A Gemini API Key (from Google AI Studio)

### 2. Configuration
ContainerClaw uses **Docker Secrets** to isolate your API keys from the agent container.

1.  Create a `secrets` directory:
    ```bash
    mkdir -p secrets
    ```
2.  Put your Gemini API key in a file named `secrets/gemini_api_key.txt`:
    ```bash
    echo "your-api-key-here" > secrets/gemini_api_key.txt
    ```
3.  (Optional) Create a `.env` file based on `.env.example`:
    ```bash
    cp .env.example .env
    ```

### 3. Launching the Stack
Use the provided `claw.sh` script to manage the lifecycle of your agent sessions.

```bash
# Start a new session
./claw.sh up my-first-session

# View the status of the containers
docker ps
```

---

## 🛠 Usage

### Interacting with the Agent
In this Phase 1 MVP, the Agent is a background service. You can interact with the components:

- **Dashboard**: Open `http://localhost:3000` in your browser to interact with the modern React dashboard.
- **Log Streaming**: Follow the live logs to see what's happening:
  ```bash
  ./claw.sh logs
  ```
- **Agent Sandbox**: The agent's workspace is mirrored to your local directory. Any files the agent creates will appear in your project root, but it cannot access files outside this folder.

### Stopping the Agent
To stop the session gracefully:
```bash
./claw.sh down
```

---

## 🔒 Security Architecture

ContainerClaw follows a **Microservices Security Pattern**:

1.  **Isolated Agent**: The agent runs as a rootless user with a restrictive **Seccomp** profile and **no internet access**. It is restricted to an internal Docker network.
2.  **LLM Gateway**: Only this hardened container has access to your API keys (via Docker Secrets). The agent must ask the Gateway to make LLM calls on its behalf.
3.  **Audited Logs**: All agent actions are designed to be streamed to an external Log Streamer (Apache Fluss) so they cannot be tampered with by a compromised agent.

---

## 📜 Project Structure

- `agent/`: The autonomous execution engine.
- `llm-gateway/`: The credential-isolated proxy for LLM APIs.
- `bridge/`: Flask proxy bridging gRPC streams to SSE for the browser.
- `ui/`: Modern Vite/React frontend dashboard.
- `proto/`: gRPC definitions for internal communication.
- `claw.sh`: The main control script.

---

## 🗺 Roadmap
- [ ] **Phase 2**: Implement full gRPC protocol for Agent ↔ UI interaction.
- [ ] **Phase 2b**: Session Persistence — the agent resumes its thought process after a restart.
- [ ] **Phase 3**: Real-time log processing and anomaly detection via Apache Fluss.
