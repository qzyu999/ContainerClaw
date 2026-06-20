#!/bin/bash
# ContainerClaw — Deploy to AWS ECS
# Usage: ./deploy.sh
#
# Required environment variables:
#   AWS_REGION          - AWS region (e.g., us-gov-west-1)
#   ECR_REGISTRY        - ECR registry URL (e.g., 123456.dkr.ecr.us-gov-west-1.amazonaws.com)
#   ECS_CLUSTER         - ECS cluster name (default: containerclaw)
#   SUBNET_ID           - Subnet for Fargate tasks
#   SECURITY_GROUP_ID   - Security group for Fargate tasks
#   TASK_ROLE_ARN       - IAM role for task (S3/Glue access)
#   EXECUTION_ROLE_ARN  - IAM role for ECS execution (ECR pull, CloudWatch)

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../../.." && pwd)"
CLUSTER="${ECS_CLUSTER:-containerclaw}"

echo "🚀 ContainerClaw Deploy"
echo "   Region:   $AWS_REGION"
echo "   Registry: $ECR_REGISTRY"
echo "   Cluster:  $CLUSTER"
echo ""

# ── Step 1: Login to ECR ──
echo "📦 [1/4] Logging into ECR..."
aws ecr get-login-password --region "$AWS_REGION" | \
    docker login --username AWS --password-stdin "$ECR_REGISTRY"

# ── Step 2: Build images ──
echo "🔨 [2/4] Building images..."
docker build -t "$ECR_REGISTRY/containerclaw-fluss:latest" "$PROJECT_ROOT/fluss"
docker build -t "$ECR_REGISTRY/containerclaw-agent:latest" -f "$PROJECT_ROOT/agent/Dockerfile" "$PROJECT_ROOT"
docker build -t "$ECR_REGISTRY/containerclaw-llm-gateway:latest" "$PROJECT_ROOT/llm-gateway"
docker build -t "$ECR_REGISTRY/containerclaw-bridge:latest" -f "$PROJECT_ROOT/bridge/Dockerfile" "$PROJECT_ROOT"

# ── Step 3: Push images ──
echo "⬆️  [3/4] Pushing images to ECR..."
docker push "$ECR_REGISTRY/containerclaw-fluss:latest"
docker push "$ECR_REGISTRY/containerclaw-agent:latest"
docker push "$ECR_REGISTRY/containerclaw-llm-gateway:latest"
docker push "$ECR_REGISTRY/containerclaw-bridge:latest"

# ── Step 4: Register task definitions ──
echo "📋 [4/4] Registering ECS task definitions..."

# Substitute variables in task definitions
for TASK_DEF in "$SCRIPT_DIR/../task-definitions"/*.json; do
    TASK_NAME=$(basename "$TASK_DEF" .json)
    echo "   Registering: $TASK_NAME"

    # Substitute placeholders
    RENDERED=$(cat "$TASK_DEF" | \
        sed "s|\${ECR_REGISTRY}|$ECR_REGISTRY|g" | \
        sed "s|\${AWS_REGION}|$AWS_REGION|g" | \
        sed "s|\${S3_BUCKET}|${S3_BUCKET:-containerclaw-data}|g")

    echo "$RENDERED" | aws ecs register-task-definition \
        --cli-input-json "file:///dev/stdin" \
        --task-role-arn "$TASK_ROLE_ARN" \
        --execution-role-arn "$EXECUTION_ROLE_ARN" \
        --region "$AWS_REGION" > /dev/null

    echo "   ✅ $TASK_NAME registered"
done

echo ""
echo "🎉 Deploy complete!"
echo ""
echo "To run the Fluss cluster:"
echo "  aws ecs run-task --cluster $CLUSTER --task-definition containerclaw-fluss \\"
echo "    --network-configuration 'awsvpcConfiguration={subnets=[$SUBNET_ID],securityGroups=[$SECURITY_GROUP_ID]}' \\"
echo "    --launch-type FARGATE --region $AWS_REGION"
