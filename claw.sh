#!/bin/bash
# claw.sh — ContainerClaw Lifecycle Manager

COMMAND=$1
SESSION_ID=${2:-"default-session"}

export CLAW_SESSION_ID=$SESSION_ID

case $COMMAND in
  up)
    echo "Starting ContainerClaw session: $SESSION_ID"
    docker-compose up -d
    ;;
  down)
    echo "Stopping ContainerClaw session: $SESSION_ID"
    docker-compose down
    ;;
  restart)
    echo "Restarting ContainerClaw session: $SESSION_ID"
    docker-compose restart
    ;;
  logs)
    docker-compose logs -f
    ;;
  *)
    echo "Usage: $0 {up|down|restart|logs} [session_id]"
    exit 1
    ;;
esac
