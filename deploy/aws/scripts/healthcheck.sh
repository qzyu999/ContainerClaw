#!/bin/bash
# ContainerClaw — Verify ECS deployment health
# Usage: ./healthcheck.sh

set -e

CLUSTER="${ECS_CLUSTER:-containerclaw}"
REGION="${AWS_REGION:-us-gov-west-1}"

echo "🏥 ContainerClaw Health Check"
echo "   Cluster: $CLUSTER"
echo "   Region:  $REGION"
echo ""

# Check running tasks
echo "📋 Running tasks:"
aws ecs list-tasks --cluster "$CLUSTER" --region "$REGION" --desired-status RUNNING \
    --query 'taskArns' --output table

echo ""

# Check service statuses
for SERVICE in fluss agent; do
    STATUS=$(aws ecs describe-services --cluster "$CLUSTER" --services "containerclaw-$SERVICE" \
        --region "$REGION" --query 'services[0].{status:status,running:runningCount,desired:desiredCount}' \
        --output text 2>/dev/null || echo "NOT_FOUND 0 0")
    echo "   $SERVICE: $STATUS"
done

echo ""
echo "📊 Recent CloudWatch logs (last 5 min):"
aws logs filter-log-events \
    --log-group-name "/ecs/containerclaw/fluss" \
    --start-time $(($(date +%s) * 1000 - 300000)) \
    --region "$REGION" \
    --query 'events[-5:].message' \
    --output text 2>/dev/null || echo "   (no logs found)"
