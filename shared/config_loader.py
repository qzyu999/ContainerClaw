"""
Unified configuration loader for ContainerClaw.

Loads config.yaml from /config/config.yaml (Docker mount) or falls back
to environment variables for backward compatibility.

This module is volume-mounted into all containers at /app/shared/ — it is
NOT copied. Any changes here apply to agent, gateway, and ripcurrent.
"""

import os
import yaml
from pathlib import Path
from pydantic import BaseModel, field_validator


CONFIG_PATH = os.getenv("CLAW_CONFIG_PATH", "/config/config.yaml")


# ── Pydantic Models ─────────────────────────────────────────────


class ProviderConfig(BaseModel):
    """Configuration for a single LLM provider."""
    name: str
    type: str                    # "openai" | "gemini"
    base_url: str
    api_key: str = ""            # Resolved from secret or inline
    api_key_secret: str = ""     # Docker secret name
    models: list[str] = []
    settings: dict = {}


class AgentConfig(BaseModel):
    """Configuration for a single agent in the roster."""
    name: str
    persona: str
    provider: str = ""           # Provider name (resolved to ProviderConfig)
    model: str = ""              # Model override


class PromptsConfig(BaseModel):
    """Configuration for agent system prompts. Derived purely from config.yaml."""
    vote: str
    vote_debate: str
    think: str
    think_with_tools: str
    send_function_responses: str
    reflect: str
    subagent_spawn: str


class ClawConfig(BaseModel):
    """Root configuration object for ContainerClaw."""
    providers: dict[str, ProviderConfig]
    agents: list[AgentConfig]
    prompts: PromptsConfig
    default_provider: str
    default_model: str
    # Agent settings
    default_persona: str = "General purpose software engineering assistant."
    default_tools: list[str] = []
    max_history_messages: int = 100
    max_history_chars: int = 480000
    max_tool_rounds: int = 30
    autonomous_steps: int = -1
    conchshell_enabled: bool = True
    subagent_ttl_seconds: int = 120
    # Gateway settings
    gateway_port: int = 8000
    gateway_url: str = "http://llm-gateway:8000"
    rate_limit_rpm: int = 60
    max_tokens_per_request: int = 8192
    # Infrastructure
    fluss_bootstrap_servers: str = "coordinator-server:9123"
    session_id: str | None = None
    # Integrations
    discord_bot_token: str = ""
    discord_webhook_url: str = ""
    discord_channel_id: str = ""

    @field_validator("agents")
    @classmethod
    def validate_agent_providers(cls, agents, info):
        """Fail-fast: every agent must reference a valid provider or be empty."""
        providers = info.data.get("providers", {})
        default = info.data.get("default_provider", "")
        for agent in agents:
            effective_provider = agent.provider or default
            if effective_provider and effective_provider not in providers:
                raise ValueError(
                    f"Agent '{agent.name}' references unknown provider "
                    f"'{effective_provider}'. Available: {list(providers.keys())}"
                )
        return agents


# ── Secret Resolution ────────────────────────────────────────────


def _resolve_secret(secret_name: str) -> str:
    """Read a Docker secret from /run/secrets/."""
    try:
        return Path(f"/run/secrets/{secret_name}").read_text().strip()
    except Exception:
        return ""


# ── Main Loader ──────────────────────────────────────────────────


def load_config(config_path: str | None = None) -> ClawConfig:
    """Load and validate the unified configuration.

    Args:
        config_path: Override path (for testing). Defaults to CONFIG_PATH env.

    Returns:
        Validated ClawConfig instance.

    Raises:
        pydantic.ValidationError: If config has type errors or invalid references.
    """
    path = config_path or CONFIG_PATH
    if not Path(path).exists():
        # Fallback: build config from env vars (backward compatibility)
        return _from_env()

    with open(path) as f:
        raw = yaml.safe_load(f)

    # Parse providers
    providers = {}
    for name, prov in raw.get("llm", {}).get("providers", {}).items():
        api_key = prov.get("api_key", "")
        if not api_key and prov.get("api_key_secret"):
            api_key = _resolve_secret(prov["api_key_secret"])
        providers[name] = ProviderConfig(
            name=name,
            type=prov["type"],
            base_url=prov["base_url"],
            api_key=api_key,
            api_key_secret=prov.get("api_key_secret", ""),
            models=prov.get("models", []),
            settings=prov.get("settings", {}),
        )

    # Parse agents
    default_prov = raw.get("llm", {}).get("default_provider", "")
    default_model = raw.get("llm", {}).get("default_model", "")
    agent_settings = raw.get("agents", {}).get("settings", {})
    agents = []
    for entry in raw.get("agents", {}).get("roster", []):
        agents.append(AgentConfig(
            name=entry["name"],
            persona=entry["persona"],
            provider=entry.get("provider", default_prov),
            model=entry.get("model", default_model),
        ))
        
    prompts_raw = raw.get("agents", {}).get("prompts", {})
    prompts = PromptsConfig(**prompts_raw)

    gateway_cfg = raw.get("gateway", {})
    infra = raw.get("infrastructure", {})

    return ClawConfig(
        providers=providers,
        agents=agents,
        prompts=prompts,
        default_provider=default_prov,
        default_model=default_model,
        default_persona=agent_settings.get("default_persona", "General purpose software engineering assistant."),
        default_tools=agent_settings.get("default_tools", [
            "board", "test_runner", "diff", "surgical_edit", "advanced_read", 
            "repo_map", "structured_search", "linter", "session_shell", 
            "create_file", "delegate"
        ]),
        max_history_messages=agent_settings.get("max_history_messages", 100),
        max_history_chars=agent_settings.get("max_history_chars", 480000),
        max_tool_rounds=agent_settings.get("max_tool_rounds", 30),
        autonomous_steps=agent_settings.get("autonomous_steps", -1),
        conchshell_enabled=agent_settings.get("conchshell_enabled", True),
        subagent_ttl_seconds=agent_settings.get("subagent_ttl_seconds", 120),
        gateway_port=gateway_cfg.get("port", 8000),
        gateway_url=os.getenv("LLM_GATEWAY_URL", "http://llm-gateway:8000"),
        rate_limit_rpm=gateway_cfg.get("rate_limit_rpm", 60),
        max_tokens_per_request=gateway_cfg.get("max_tokens_per_request", 8192),
        fluss_bootstrap_servers=(
            infra.get("fluss", {}).get("bootstrap_servers", "coordinator-server:9123")
        ),
        session_id=(
            infra.get("session", {}).get("default_id", "default-session")
        ),
        discord_bot_token=_resolve_secret(raw.get("integrations", {}).get("discord", {}).get("bot_token_secret", "")),
        discord_webhook_url=_resolve_secret(raw.get("integrations", {}).get("discord", {}).get("webhook_url_secret", "")),
        discord_channel_id=_resolve_secret(raw.get("integrations", {}).get("discord", {}).get("channel_id_secret", "")),
    )


def _from_env() -> ClawConfig:
    """Backward-compatible: build ClawConfig from env vars.

    This mirrors the old agent/src/config.py behavior so that containers
    continue to work even if config.yaml is not yet mounted.
    """
    # Build a single provider from env vars
    api_key = ""
    try:
        api_key = Path("/run/secrets/gemini_api_key").read_text().strip()
    except Exception:
        api_key = os.getenv("GEMINI_API_KEY", "")

    default_model = os.getenv("DEFAULT_MODEL", "gemini-3-flash-preview")
    gateway_url = os.getenv("LLM_GATEWAY_URL", "http://llm-gateway:8000")

    providers = {
        "gemini-cloud": ProviderConfig(
            name="gemini-cloud",
            type="gemini",
            base_url="https://generativelanguage.googleapis.com/v1beta",
            api_key=api_key,
            api_key_secret="gemini_api_key",
            models=[default_model],
        )
    }

    agents = [
        AgentConfig(name="Alice", persona="Software architect.",
                    provider="gemini-cloud", model=default_model),
        AgentConfig(name="Bob", persona="Project manager.",
                    provider="gemini-cloud", model=default_model),
        AgentConfig(name="Carol", persona="Software engineer.",
                    provider="gemini-cloud", model=default_model),
        AgentConfig(name="David", persona="Software QA tester.",
                    provider="gemini-cloud", model=default_model),
        AgentConfig(name="Eve", persona="Business user.",
                    provider="gemini-cloud", model=default_model),
    ]

    try:
        local_config = Path(__file__).parent.parent / "config.yaml"
        with open(local_config) as f:
            local_raw = yaml.safe_load(f)
            fallback_prompts = PromptsConfig(**local_raw.get("agents", {}).get("prompts", {}))
    except Exception:
        # Fallback to empty strings if neither mount nor local file is available
        fallback_prompts = PromptsConfig(
            vote="", vote_debate="", think="", think_with_tools="", 
            send_function_responses="", reflect="", subagent_spawn=""
        )

    return ClawConfig(
        providers=providers,
        agents=agents,
        prompts=fallback_prompts,
        default_provider="gemini-cloud",
        default_model=default_model,
        default_persona="General purpose software engineering assistant.",
        default_tools=[
            "board", "test_runner", "diff", "surgical_edit", "advanced_read", 
            "repo_map", "structured_search", "linter", "session_shell", 
            "create_file", "delegate"
        ],
        max_history_messages=int(os.getenv("MAX_HISTORY_MESSAGES", "100")),
        max_history_chars=480000,
        max_tool_rounds=int(os.getenv("MAX_TOOL_ROUNDS", "30")),
        autonomous_steps=int(os.getenv("AUTONOMOUS_STEPS", "-1")),
        conchshell_enabled=os.getenv("CONCHSHELL_ENABLED", "true").lower() == "true",
        subagent_ttl_seconds=int(os.getenv("SUBAGENT_TTL_SECONDS", "120")),
        gateway_port=int(os.getenv("LLM_GATEWAY_PORT", "8000")),
        gateway_url=gateway_url,
        rate_limit_rpm=int(os.getenv("RATE_LIMIT_RPM", "60")),
        max_tokens_per_request=int(os.getenv("MAX_TOKENS_PER_REQUEST", "8192")),
        fluss_bootstrap_servers=os.getenv("FLUSS_BOOTSTRAP_SERVERS", "coordinator-server:9123"),
        session_id=os.getenv("CLAW_SESSION_ID"),
        discord_bot_token=os.getenv("DISCORD_BOT_TOKEN", ""),
        discord_webhook_url=os.getenv("DISCORD_WEBHOOK_URL", ""),
        discord_channel_id=os.getenv("DISCORD_CHANNEL_ID", ""),
    )
