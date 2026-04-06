#!/usr/bin/env python3
import sys
import os

# Add shared to path
sys.path.append(os.path.join(os.path.dirname(__file__), ".."))

from shared.config_loader import load_config

def main():
    try:
        # Ensure CLAW_CONFIG_PATH is set for the test
        os.environ["CLAW_CONFIG_PATH"] = "config.yaml"
        
        config = load_config()
        print(f"llm_server.model: {config.llm_server.model}")
        print(f"default_model: {config.default_model}")
        print(f"llm_server.max_tokens: {config.llm_server.max_tokens}")
        print(f"max_tokens_per_request: {config.max_tokens_per_request}")
        
        expected_model = config.llm_server.model
        assert config.default_model == expected_model, f"Expected {expected_model}, got {config.default_model}"
        assert config.llm_server.max_tokens == config.max_tokens_per_request, f"Expected {config.max_tokens_per_request}, got {config.llm_server.max_tokens}"
        
        # Check provider list
        if "mlx-local" in config.providers:
            models = config.providers["mlx-local"].models
            print(f"mlx-local models: {models}")
            assert expected_model in models, f"Expected {expected_model} in mlx-local models"
            
        print("✅ Auto-inference verified!")
        
    except Exception as e:
        print(f"Test failed: {e}", file=sys.stderr)
        sys.exit(1)

if __name__ == "__main__":
    main()
