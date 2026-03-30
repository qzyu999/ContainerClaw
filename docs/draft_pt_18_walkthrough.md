# Walkthrough: Modular LLM Backend & Unified Config Refactor

## Overview

Refactored ContainerClaw from a Gemini-only, env-var-scattered system into a **provider-agnostic, OpenAI-wire-compatible architecture** with centralized YAML configuration and first-class local inference support (MLX).

## Changes Made

### Phase 1: Config Foundation

| File | Action | Purpose |
|---|---|---|
| [config.yaml](file:///.../ContainerClaw/config.yaml) | **NEW** | Single source of truth — providers, roster, settings |
| [shared/config_loader.py](file:///.../ContainerClaw/shared/config_loader.py) | **NEW** | Pydantic-validated config parser with Docker secret resolution + env var fallback |
| [agent/requirements.txt](file:///.../ContainerClaw/agent/requirements.txt) | **MOD** | Added `pyyaml`, `pydantic` |
| [agent/src/config.py](file:///.../ContainerClaw/agent/src/config.py) | **MOD** | Replaced with thin wrapper over `config_loader` — backward-compatible module-level constants |

---

### Phase 2: Gateway Strategy Pattern

| File | Action | Purpose |
|---|---|---|
| [llm-gateway/src/main.py](file:///.../ContainerClaw/llm-gateway/src/main.py) | **MOD** | Replaced monolithic Gemini handler with strategy router |
| [llm-gateway/src/providers/\_\_init\_\_.py](file:///.../ContainerClaw/llm-gateway/src/providers/__init__.py) | **NEW** | Package init |
| [llm-gateway/src/providers/openai_strategy.py](file:///.../ContainerClaw/llm-gateway/src/providers/openai_strategy.py) | **NEW** | Zero-translation passthrough for MLX/vLLM/Ollama/OpenAI |
| [llm-gateway/src/providers/gemini_strategy.py](file:///.../ContainerClaw/llm-gateway/src/providers/gemini_strategy.py) | **NEW** | Full bidirectional translation (OpenAI ↔ Gemini), including tools |
| [llm-gateway/Dockerfile](file:///.../ContainerClaw/llm-gateway/Dockerfile) | **MOD** | Added `pyyaml` to pip install |

---

### Phase 3: Agent Wire Protocol Migration

| File | Action | Purpose |
|---|---|---|
| [agent/src/agent.py](file:///.../ContainerClaw/agent/src/agent.py) | **MOD** | `GeminiAgent` → `LLMAgent` with full OpenAI Chat Completions wire format |
| [agent/src/tool_executor.py](file:///.../ContainerClaw/agent/src/tool_executor.py) | **MOD** | Updated docstring to reflect OpenAI protocol |
| [agent/src/subagent_manager.py](file:///.../ContainerClaw/agent/src/subagent_manager.py) | **MOD** | `GeminiAgent` → `LLMAgent`, `api_key` → `provider`/`model` |

**Key wire format changes:**
- Message format: `{role, parts: [{text}]}` → `{role, content}`
- Role names: `"model"` → `"assistant"`
- System instruction: raw string → `{role: "system", content}` message
- Tools: `function_declarations` → `{type: "function", function: {name, description, parameters}}`
- Tool choice: `function_calling_config.mode` → `tool_choice`
- Tool results: `functionResponse` parts → `{role: "tool", tool_call_id, content}` messages
- Response parsing: `candidates[0].content.parts` → `choices[0].message`

---

### Phase 4: Declarative Agent Roster

| File | Action | Purpose |
|---|---|---|
| [agent/src/main.py](file:///.../ContainerClaw/agent/src/main.py) | **MOD** | Config-driven agent creation from `config.yaml` roster; config-driven toolsets |

Adding/removing agents is now a YAML edit — no code changes required.

---

### Phase 5: MLX Local Inference

| File | Action | Purpose |
|---|---|---|
| [docker-compose.yml](file:///.../ContainerClaw/docker-compose.yml) | **MOD** | `extra_hosts` for host.docker.internal, config.yaml + shared/ volume mounts |

To use MLX locally, change `config.yaml`:
```yaml
llm:
  default_provider: "mlx-local"
  default_model: "Qwen2.5-3B-Instruct-4bit"
```

---

### Phase 6: Cleanup

| File | Action | Purpose |
|---|---|---|
| [scripts/validate_config.py](file:///.../ContainerClaw/scripts/validate_config.py) | **NEW** | Pre-flight config validator (agent→provider cross-refs, secret checks) |

## Verification

### Config Validation
```
🔍 Validating config.yaml...
✅ Config valid!
   Providers: ['mlx-local', 'gemini-cloud', 'openai-cloud']
   Default: gemini-cloud / gemini-3-flash-preview
   Agents: ['Alice', 'Bob', 'Carol', 'David', 'Eve']
```

### Env Var Fallback
```
Fallback OK: gemini-cloud, agents=['Alice', 'Bob', 'Carol', 'David', 'Eve']
Gateway: http://llm-gateway:8000, Model: gemini-3-flash-preview
```

### Backward Compatibility
- `GeminiAgent` aliased to `LLMAgent` in `agent.py`
- `config.py` re-exports same module-level constants (`LLM_GATEWAY_URL`, `DEFAULT_MODEL`, etc.)
- `_from_env()` fallback builds full config from env vars when `config.yaml` is absent
- Existing Docker Secrets pattern preserved

## Remaining
- Full Docker E2E test via `./claw.sh up`
- `.env.example` cleanup (deferred to avoid breaking existing deploys during transition)
