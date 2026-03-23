#!/bin/bash
# claw.sh — ContainerClaw Lifecycle Manager

COMMAND=$1
SESSION_ID=${2:-"default-session"}

export CLAW_SESSION_ID=$SESSION_ID

# Use docker compose (V2) or docker-compose (V1)
DOCKER_COMPOSE="docker compose"
if ! docker compose version >/dev/null 2>&1; then
  DOCKER_COMPOSE="docker-compose"
fi

case $COMMAND in
  up)
    echo "Starting ContainerClaw session: $SESSION_ID"
    # Ensure secrets directory exists if referenced in compose
    if [ ! -d "secrets" ]; then
      mkdir -p secrets
      touch secrets/gemini_api_key.txt secrets/anthropic_api_key.txt secrets/openai_api_key.txt
    fi
    mkdir -p workspace .zk_data/data .zk_data/datalog .fluss_data
    $DOCKER_COMPOSE up -d --build --remove-orphans
    ;;
  down)
    echo "Gracefully stopping ContainerClaw session: $SESSION_ID"
    $DOCKER_COMPOSE stop claw-agent
    $DOCKER_COMPOSE down --remove-orphans
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
    $DOCKER_COMPOSE ps
    echo -e "\n--- LLM Gateway Health ---"
    $DOCKER_COMPOSE logs --tail=50 llm-gateway | grep -E "429|500" || echo "No API errors detected in recent logs."
    ;;
  restart)
    echo "Restarting ContainerClaw session: $SESSION_ID"
    $DOCKER_COMPOSE down --remove-orphans
    $DOCKER_COMPOSE up -d --build
    ;;
  clean)
    echo "Deep cleaning ContainerClaw environment..."
    $DOCKER_COMPOSE down -v --remove-orphans
    docker network prune -f
    ;;
  logs)
    echo "Streaming logs for session: $SESSION_ID"
    # Try to stream from Fluss log server, fallback to docker logs if it fails
    if ! curl -s --fail http://localhost:9092/v1/logs/$SESSION_ID/stream; then
      echo "Failed to reach log server. Falling back to docker compose logs..."
      $DOCKER_COMPOSE logs -f
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
    echo "Usage: $0 {up|down|purge|status|restart|clean|logs|clear-workspace} [session_id]"
    exit 1
    ;;
esac
