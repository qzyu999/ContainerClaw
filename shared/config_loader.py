"""
Unified configuration loader for ContainerClaw.

Loads config.yaml from /config/config.yaml (Docker mount).
config.yaml is the single source of truth — no env var fallback.

This module is volume-mounted into all containers at /app/shared/ — it is
NOT copied. Any changes here apply to agent, gateway, and ripcurrent.
"""

import os
import yaml
from pathlib import Path
from typing import ClassVar, Union
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
    tools: Union[str, list[str]] = "default_tools"  # "default_tools" sentinel or explicit list

    def resolved_tools(self, default_tools: list[str]) -> list[str]:
        """Resolve the tools field: 'default_tools' sentinel → full list, else return as-is."""
        if isinstance(self.tools, str) and self.tools == "default_tools":
            return list(default_tools)
        elif isinstance(self.tools, list):
            return self.tools
        return list(default_tools)


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

    # Reserved names that conflict with control-plane actor IDs
    RESERVED_NAMES: ClassVar[set[str]] = {"Human", "Moderator", "System", "system"}
    RESERVED_PREFIXES: ClassVar[tuple[str, ...]] = ("Discord/", "Sub/", "discord/", "sub/")

    @field_validator("agents")
    @classmethod
    def validate_agents(cls, agents, info):
        """Fail-fast: validate agent names and provider references."""
        providers = info.data.get("providers", {})
        default = info.data.get("default_provider", "")
        for agent in agents:
            # Reserved name check
            if agent.name in cls.RESERVED_NAMES:
                raise ValueError(
                    f"Agent name '{agent.name}' is reserved for system use. "
                    f"Reserved names: {cls.RESERVED_NAMES}"
                )
            if any(agent.name.startswith(p) for p in cls.RESERVED_PREFIXES):
                raise ValueError(
                    f"Agent name '{agent.name}' uses a reserved prefix. "
                    f"Reserved prefixes: {cls.RESERVED_PREFIXES}"
                )
            # Provider reference check
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
        raise FileNotFoundError(
            f"config.yaml not found at '{path}'. Set CLAW_CONFIG_PATH or "
            f"mount config.yaml into the container at /config/config.yaml."
        )

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
            tools=entry.get("tools", "default_tools"),
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

