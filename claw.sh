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
    mkdir -p workspace
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
    $DOCKER_COMPOSE logs -f
    ;;
  *)
    echo "Usage: $0 {up|down|purge|status|restart|clean|logs} [session_id]"
    exit 1
    ;;
esac
