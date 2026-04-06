#!/usr/bin/env python3
import sys
import os

# Add shared to path
sys.path.append(os.path.join(os.path.dirname(__file__), ".."))

from shared.config_loader import load_config

def main():
    try:
        config = load_config()
        server = config.llm_server
        
        # Output as shell variables
        print(f'LLM_SERVER_ENABLED="{str(server.enabled).lower()}"')
        print(f'LLM_SERVER_PORT="{server.port}"')
        print(f'LLM_SERVER_HOST="{server.host}"')
        
        model_path = server.model
        args = [
            f"--model {model_path}",
            f"--port {server.port}",
            f"--host {server.host}",
            f"--max-tokens {server.max_tokens}",
            f"--prompt-cache-size {server.prompt_cache_size}",
            f"--log-level {server.log_level}"
        ]
        print(f'LLM_SERVER_ARGS="{" ".join(args)}"')
        
    except Exception as e:
        print(f"Error loading config: {e}", file=sys.stderr)
        sys.exit(1)

if __name__ == "__main__":
    main()
