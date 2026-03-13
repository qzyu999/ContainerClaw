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
    $DOCKER_COMPOSE up -d --build --remove-orphans
    ;;
  down)
    echo "Stopping ContainerClaw session: $SESSION_ID"
    $DOCKER_COMPOSE down --remove-orphans
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
  status)
    echo "ContainerClaw Status:"
    $DOCKER_COMPOSE ps
    echo -e "\nRecent Logs (Gateway 429 Check):"
    $DOCKER_COMPOSE logs --tail=20 llm-gateway | grep -i "429" && echo "WARNING: Quota limit hit!" || echo "Quota status: OK"
    ;;
  logs)
    $DOCKER_COMPOSE logs -f
    ;;
  *)
    echo "Usage: $0 {up|down|restart|clean|status|logs} [session_id]"
    exit 1
    ;;
esac
