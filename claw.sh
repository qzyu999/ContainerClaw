#!/bin/bash
# claw.sh — ContainerClaw Lifecycle Manager

COMMAND=$1
shift  # Remove the command from args

# Parse remaining flags
SESSION_ID="default-session"
TELEMETRY_PROFILE=""

while [[ $# -gt 0 ]]; do
  case $1 in
    --telemetry)
      case $2 in
        local)
          TELEMETRY_PROFILE="telemetry-local"
          ;;
        enterprise)
          TELEMETRY_PROFILE="telemetry-enterprise"
          ;;
        *)
          echo "❌ Unknown telemetry mode: $2. Use 'local' or 'enterprise'."
          exit 1
          ;;
      esac
      shift 2
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
  echo "📊 Telemetry mode: $TELEMETRY_PROFILE"
  # Export engine type so docker-compose passes it to the bridge container
  if [ "$TELEMETRY_PROFILE" = "telemetry-local" ]; then
    export TELEMETRY_ENGINE="duckdb"
  elif [ "$TELEMETRY_PROFILE" = "telemetry-enterprise" ]; then
    export TELEMETRY_ENGINE="starrocks"
  fi
fi

# For teardown commands (down/clean/restart), always activate ALL profiles
# so docker compose can see and stop every container, even profiled ones.
ALL_PROFILES="--profile telemetry-local --profile telemetry-enterprise"

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
      if ! $PYTHON_BIN scripts/validate_config.py config.yaml; then
        echo -e "\n❌ Startup aborted. Please fix configuration errors above."
        exit 1
      fi
    else
      echo "⚠️ Skipping pre-flight check: 'pyyaml' or 'pydantic' missing in host Python."
    fi
    
    # Ensure secrets directory exists if referenced in compose
    if [ ! -d "secrets" ]; then
      mkdir -p secrets
      touch secrets/gemini_api_key.txt secrets/anthropic_api_key.txt secrets/openai_api_key.txt
      echo "⚠️ Created empty secrets files in secrets/. Please populate them before continuing."
    fi
    mkdir -p workspace .zk_data/data .zk_data/datalog .fluss_data .claw_state
    $DOCKER_COMPOSE $PROFILE_FLAG up -d --build --remove-orphans
    ;;
  down)
    echo "Gracefully stopping ContainerClaw session: $SESSION_ID"
    $DOCKER_COMPOSE $ALL_PROFILES stop claw-agent 2>/dev/null
    $DOCKER_COMPOSE $ALL_PROFILES down --remove-orphans
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
    $DOCKER_COMPOSE $PROFILE_FLAG ps
    echo -e "\n--- LLM Gateway Health ---"
    $DOCKER_COMPOSE logs --tail=50 llm-gateway | grep -E "429|500" || echo "No API errors detected in recent logs."
    ;;
  restart)
    echo "Restarting ContainerClaw session: $SESSION_ID"
    $DOCKER_COMPOSE $ALL_PROFILES down --remove-orphans
    $DOCKER_COMPOSE $PROFILE_FLAG up -d --build
    ;;
  clean)
    echo "Deep cleaning ContainerClaw environment..."
    # Use ALL_PROFILES to guarantee every profiled container is stopped
    $DOCKER_COMPOSE $ALL_PROFILES down -v --remove-orphans
    rm -rf .fluss_data .zk_data .claw_state/telemetry.duckdb
    docker network prune -f
    ;;
  logs)
    echo "Streaming logs for session: $SESSION_ID"
    # Try to stream from Fluss log server, fallback to docker logs if it fails
    if ! curl -s --fail http://localhost:9092/v1/logs/$SESSION_ID/stream; then
      echo "Failed to reach log server. Falling back to docker compose logs..."
      $DOCKER_COMPOSE $PROFILE_FLAG logs -f
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
    echo "Usage: $0 {up|down|purge|status|restart|clean|logs|clear-workspace} [session_id] [--telemetry local|enterprise]"
    exit 1
    ;;
esac

