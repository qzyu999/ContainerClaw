#!/bin/bash
# claw.sh — ContainerClaw Lifecycle Manager

COMMAND=$1
shift  # Remove the command from args

# Parse remaining flags
SESSION_ID="default-session"
TELEMETRY_PROFILE=""
COMPOSE_FILES="-f docker-compose.yml"

while [[ $# -gt 0 ]]; do
  case $1 in
    --telemetry)
      TELEMETRY_PROFILE="telemetry"
      shift
      ;;
    --bench)
      # SWE-bench mode: load the overlay and run as root
      export CLAW_USER="root"
      export CLAW_READ_ONLY="false"
      export CONCHSHELL_ENABLED="true"
      export SWE_BENCH_MODE="true"
      export CLAW_HOME="/root"
      COMPOSE_FILES="$COMPOSE_FILES -f docker-compose.swebench.yml"
      echo "🧪 SWE-bench mode enabled (root user, writable fs, docker-overlay)"
      shift
      ;;
    --sidecar)
      # Sidecar mode: load sidecar overlay (Docker socket + sidecar containers)
      # Docker socket requires root access on macOS Docker Desktop
      export CLAW_USER="root"
      export CLAW_READ_ONLY="false"
      export CLAW_HOME="/root"
      export CONCHSHELL_ENABLED="true"
      COMPOSE_FILES="$COMPOSE_FILES -f docker-compose.sidecar.yml"
      echo "🐳 Sidecar mode enabled (Python 3.12 + Node.js 20 sidecars)"
      shift
      ;;
    *)
      SESSION_ID=$1
      shift
      ;;
  esac
done

export CLAW_SESSION_ID=$SESSION_ID

# Use docker compose (V2) or docker-compose (V1)
DOCKER_COMPOSE="docker compose"
if ! docker compose version >/dev/null 2>&1; then
  DOCKER_COMPOSE="docker-compose"
fi

# Build the profile flag for 'up' (only the requested profile)
PROFILE_FLAG=""
if [ -n "$TELEMETRY_PROFILE" ]; then
  PROFILE_FLAG="--profile $TELEMETRY_PROFILE"
  echo "📊 Telemetry enabled (Fluss-native pipeline)"
fi

# Pre-evaluate MLX status to conditionally include MLX sidecar compose file
PYTHON_BIN="python3"
if [ -f ".venv/bin/python3" ]; then
  PYTHON_BIN=".venv/bin/python3"
fi
eval $(CLAW_CONFIG_PATH=config.yaml $PYTHON_BIN scripts/get_llm_info.py 2>/dev/null)
if [ "$LLM_SERVER_ENABLED" = "true" ]; then
  ARCH=$(uname -m)
  OS=$(uname -s)
  if [ "$OS" = "Darwin" ] && [ "$ARCH" = "arm64" ]; then
    COMPOSE_FILES="$COMPOSE_FILES -f docker-compose.mlx.yml"
  fi
fi

# For teardown commands (down/clean/restart), always activate ALL profiles
# so docker compose can see and stop every container, even profiled ones.
ALL_PROFILES="--profile telemetry"

stop_mlx() {
  if [ -f ".claw_state/mlx.pid" ]; then
    MLX_PID=$(cat .claw_state/mlx.pid)
    if [ -n "$MLX_PID" ]; then
      # Verify PID belongs to mlx_lm
      if ps -p $MLX_PID -o command= | grep -q "mlx_lm"; then
        echo "Stopping MLX server (PID: $MLX_PID)..."
        kill $MLX_PID
      else
        echo "⚠️ MLX PID file found but process is not mlx_lm. Skipping kill."
      fi
    fi
    rm -f .claw_state/mlx.pid
  fi
}

case $COMMAND in
  up)
    echo "Starting ContainerClaw session: $SESSION_ID"
    
    # Pre-flight config validation
    echo "Running pre-flight config check..."
    PYTHON_BIN="python3"
    if [ -f ".venv/bin/python3" ]; then
      PYTHON_BIN=".venv/bin/python3"
    fi
    
    if $PYTHON_BIN -c "import yaml, pydantic" 2>/dev/null; then
      if ! CLAW_CONFIG_PATH=config.yaml $PYTHON_BIN scripts/validate_config.py config.yaml; then
        echo -e "\n❌ Startup aborted. Please fix configuration errors above."
        exit 1
      fi
    else
      echo "⚠️ Skipping pre-flight check: 'pyyaml' or 'pydantic' missing in host Python."
    fi
    
    if [ ! -d "secrets" ]; then
      mkdir -p secrets
      touch secrets/gemini_api_key.txt secrets/anthropic_api_key.txt secrets/openai_api_key.txt
      echo "⚠️ Created empty secrets files in secrets/. Please populate them before continuing."
    fi
    mkdir -p workspace .zk_data/data .zk_data/datalog .fluss_data .claw_state

    # MLX Server (Host-side)
    if [ "$LLM_SERVER_ENABLED" = "true" ]; then
      ARCH=$(uname -m)
      OS=$(uname -s)
      if [ "$OS" = "Darwin" ] && [ "$ARCH" = "arm64" ]; then
        echo "🚀 Starting host-side MLX server on port $LLM_SERVER_PORT..."
        if ! $PYTHON_BIN -c "import mlx_lm" 2>/dev/null; then
          echo "❌ Error: 'mlx-lm' not found in $PYTHON_BIN. Please install it."
          exit 1
        fi
        
        stop_mlx # Ensure no stale process
        rm -f .claw_state/mlx.log
        touch .claw_state/mlx.log
        
        # Modern MLX command with HF offline safety
        HF_HUB_OFFLINE=1 nohup $PYTHON_BIN -m mlx_lm server $LLM_SERVER_ARGS > .claw_state/mlx.log 2>&1 &
        MLX_PID=$!
        echo $MLX_PID > .claw_state/mlx.pid
        
        echo "Waiting for MLX server to be ready..."
        MAX_RETRIES=60
        RETRY_COUNT=0
        until curl -s -f "http://$LLM_SERVER_HOST:$LLM_SERVER_PORT/v1/models" >/dev/null 2>&1; do
          if ! ps -p $MLX_PID > /dev/null; then
            echo -e "\n❌ MLX server crashed on startup. See .claw_state/mlx.log"
            exit 1
          fi
          RETRY_COUNT=$((RETRY_COUNT+1))
          if [ $RETRY_COUNT -ge $MAX_RETRIES ]; then
            echo "❌ MLX server failed to start within timeout. Check .claw_state/mlx.log"
            stop_mlx
            exit 1
          fi
          echo -n "."
          sleep 2
        done
        echo -e "\n✅ MLX server is ready."
      else
        echo "⚠️ Skipping MLX server: Host is not Darwin/arm64 ($OS/$ARCH)."
      fi
    fi

    $DOCKER_COMPOSE $COMPOSE_FILES $PROFILE_FLAG up -d --build --remove-orphans
    ;;
  down)
    echo "Gracefully stopping ContainerClaw session: $SESSION_ID"
    $DOCKER_COMPOSE $COMPOSE_FILES $ALL_PROFILES stop claw-agent 2>/dev/null
    $DOCKER_COMPOSE $COMPOSE_FILES $ALL_PROFILES down --remove-orphans
    stop_mlx
    ;;
  purge)
    echo "Purging state for session: $SESSION_ID"
    rm -rf ".claw_state/$SESSION_ID"
    rm -rf ".fluss_data"
    rm -rf ".zk_data"
    echo "State cleared."
    ;;
  status)
    echo "--- ContainerClaw Swarm Status ---"
    $DOCKER_COMPOSE $COMPOSE_FILES $PROFILE_FLAG ps
    echo -e "\n--- LLM Gateway Health ---"
    $DOCKER_COMPOSE $COMPOSE_FILES logs --tail=50 llm-gateway | grep -E "429|500" || echo "No API errors detected in recent logs."
    ;;
  restart)
    echo "Restarting ContainerClaw session: $SESSION_ID"
    $DOCKER_COMPOSE $COMPOSE_FILES $ALL_PROFILES down --remove-orphans
    stop_mlx
    $0 up $SESSION_ID $PROFILE_FLAG
    ;;
  clean)
    echo "Deep cleaning ContainerClaw environment..."
    # Use ALL_PROFILES to guarantee every profiled container is stopped
    $DOCKER_COMPOSE $COMPOSE_FILES $ALL_PROFILES down -v --remove-orphans
    stop_mlx
    rm -rf .fluss_data .zk_data .claw_state/mlx.log .claw_state/mlx.pid
    docker network prune -f
    ;;
  logs)
    echo "Streaming logs for session: $SESSION_ID"
    # Try to stream from Fluss log server, fallback to docker logs if it fails
    if ! curl -s --fail http://localhost:9092/v1/logs/$SESSION_ID/stream; then
      echo "Failed to reach log server. Falling back to docker compose logs..."
      $DOCKER_COMPOSE $COMPOSE_FILES $PROFILE_FLAG logs -f
    fi
    ;;
  clear-workspace)
    echo "Clearing /workspace contents..."
    # Keep the directory but empty it. Handle hidden files too.
    rm -rf workspace/* workspace/.[!.]* workspace/..?* 2>/dev/null || true
    # Restore .gitkeep if it's tracked, otherwise just touch it
    git checkout -- workspace/.gitkeep 2>/dev/null || touch workspace/.gitkeep 2>/dev/null
    echo "Workspace cleared."
    ;;
  *)
    echo "Usage: $0 {up|down|purge|status|restart|clean|logs|clear-workspace} [session_id] [--telemetry] [--sidecar] [--bench]"
    exit 1
    ;;
esac
