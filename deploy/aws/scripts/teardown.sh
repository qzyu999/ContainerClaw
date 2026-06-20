#!/bin/bash
# ContainerClaw — Stop all ECS services
# Usage: ./teardown.sh

set -e

CLUSTER="${ECS_CLUSTER:-containerclaw}"
REGION="${AWS_REGION:-us-gov-west-1}"

echo "🛑 ContainerClaw Teardown"
echo "   Cluster: $CLUSTER"
echo "   Region:  $REGION"
echo ""

# Stop all running tasks
TASKS=$(aws ecs list-tasks --cluster "$CLUSTER" --region "$REGION" \
    --desired-status RUNNING --query 'taskArns[]' --output text)

if [ -n "$TASKS" ]; then
    for TASK in $TASKS; do
        echo "   Stopping: $TASK"
        aws ecs stop-task --cluster "$CLUSTER" --task "$TASK" --region "$REGION" > /dev/null
    done
    echo "✅ All tasks stopped"
else
    echo "   No running tasks found"
fi
