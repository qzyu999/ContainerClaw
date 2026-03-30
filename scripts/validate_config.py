#!/usr/bin/env python3
"""
Pre-flight configuration validator for ContainerClaw.

Run before `docker compose up` to catch config errors early:
    python scripts/validate_config.py [config-path]

Checks:
1. YAML syntax (via yaml.safe_load)
2. Pydantic type validation (via ClawConfig)
3. Agent → provider cross-references
4. Secret file existence (in secrets/)
"""

import sys
import os
from pathlib import Path

# Add shared/ to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "shared"))

from config_loader import load_config  # noqa: E402


def validate(config_path: str) -> list[str]:
    """Validate a config.yaml file. Returns list of error strings."""
    errors = []

    if not Path(config_path).exists():
        errors.append(f"Config file not found: {config_path}")
        return errors

    try:
        cfg = load_config(config_path)
    except Exception as e:
        errors.append(f"Config validation failed: {e}")
        return errors

    # Check that every agent's model exists in its provider's model list
    for agent in cfg.agents:
        provider_name = agent.provider or cfg.default_provider
        provider = cfg.providers.get(provider_name)
        if not provider:
            errors.append(
                f"Agent '{agent.name}' references unknown provider '{provider_name}'"
            )
            continue
        model = agent.model or cfg.default_model
        if provider.models and model not in provider.models:
            errors.append(
                f"Agent '{agent.name}' uses model '{model}' which is not in "
                f"provider '{provider_name}' model list: {provider.models}"
            )

    # Check that referenced secrets exist (best-effort, may not be in secrets/ locally)
    secrets_dir = Path(__file__).resolve().parent.parent / "secrets"
    for name, prov in cfg.providers.items():
        if prov.api_key_secret:
            secret_file = secrets_dir / f"{prov.api_key_secret}.txt"
            if not secret_file.exists():
                errors.append(
                    f"Provider '{name}' references secret '{prov.api_key_secret}' "
                    f"but {secret_file} not found (may be OK in Docker)"
                )

    return errors


def main():
    config_path = sys.argv[1] if len(sys.argv) > 1 else "config.yaml"

    print(f"🔍 Validating {config_path}...")
    errors = validate(config_path)

    if errors:
        print(f"\n❌ {len(errors)} issue(s) found:\n")
        for i, err in enumerate(errors, 1):
            print(f"  {i}. {err}")
        sys.exit(1)
    else:
        cfg = load_config(config_path)
        print(f"✅ Config valid!")
        print(f"   Providers: {list(cfg.providers.keys())}")
        print(f"   Default: {cfg.default_provider} / {cfg.default_model}")
        print(f"   Agents: {[a.name for a in cfg.agents]}")
        sys.exit(0)


if __name__ == "__main__":
    main()
