# ContainerClaw — AWS ECS Deployment

Reference deployment for running ContainerClaw on AWS ECS Fargate.

## Prerequisites

- AWS CLI configured with appropriate credentials
- Docker (for building images)
- An ECR registry to push images
- A VPC with private subnets
- An IAM role with S3/Glue/CloudWatch permissions

## Quick Deploy

```bash
# Set your environment
export AWS_REGION=us-gov-west-1
export ECR_REGISTRY=123456789012.dkr.ecr.us-gov-west-1.amazonaws.com
export ENVIRONMENT=dev

# Build and push images
./scripts/deploy.sh

# Verify
./scripts/healthcheck.sh
```

## Architecture

```
ECS Cluster: containerclaw
├── Service: fluss         (1 task, always-on during work hours)
│   └── Container: fluss   (ZK + Coordinator + TabletServer)
├── Service: agent         (1 task)
│   ├── Container: claw-agent
│   ├── Container: llm-gateway
│   └── Container: ui-bridge
└── Service: tiering       (1 task, optional)
    └── Container: flink   (JobManager + TaskManager)
```

## Task Definitions

| File | Service | CPU | Memory |
|------|---------|-----|--------|
| `task-definitions/fluss-cluster.json` | Fluss cluster | 4 vCPU | 8 GB |
| `task-definitions/agent.json` | Agent + Gateway + Bridge | 2 vCPU | 4 GB |
| `task-definitions/tiering.json` | Flink tiering (optional) | 2 vCPU | 4 GB |

## Environment Variables

### Fluss Container
| Variable | Description | Example |
|----------|-------------|---------|
| `FLUSS_REMOTE_DATA_DIR` | S3 path for persistent data | `s3://my-bucket/fluss/remote-data` |
| `FLUSS_ADVERTISED_HOST` | Hostname clients use to connect | `fluss.containerclaw.local` |

### Agent Container
| Variable | Description | Example |
|----------|-------------|---------|
| `FLUSS_BOOTSTRAP_SERVERS` | Fluss coordinator address | `fluss.containerclaw.local:9123` |
| `LLM_GATEWAY_URL` | Gateway service URL | `http://localhost:8000` |
| `CLAW_CONFIG_PATH` | Path to config.yaml | `/config/config.yaml` |

## Configs

- `configs/server.cloud.yaml` — Fluss server config with S3 remote data dir
- `configs/config.cloud.yaml` — Agent config for cloud (provider, timeouts)

Copy and customize these for your environment.

## Integration with Terraform

This directory provides the **reference task definitions**. Your IaC repo wraps them with:
- VPC/subnet/security group references
- IAM roles (ECS task + execution roles)
- Cloud Map service discovery
- EventBridge scheduled scaling
- CloudWatch log groups

See the existing `esor-claw` Terraform module for the pattern.
