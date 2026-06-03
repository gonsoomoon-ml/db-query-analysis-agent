#!/usr/bin/env bash
set -euo pipefail
PROJECT_ROOT="$(cd "$(dirname "$0")/../../.." && pwd)"
source "$PROJECT_ROOT/.env" 2>/dev/null || true
REGION="${AWS_REGION:-us-east-1}"
NAME="${RUNTIME_NAME:-db_query_analysis_agent_${DEMO_USER:-ubuntu}}"
echo "Runtime 삭제: ${RUNTIME_ID:-?}"
[ -n "${RUNTIME_ID:-}" ] && aws bedrock-agentcore-control delete-agent-runtime --agent-runtime-id "$RUNTIME_ID" --region "$REGION" || echo "RUNTIME_ID 없음/스킵"
# toolkit이 만드는 ECR repo는 "bedrock-agentcore-" 접두사가 붙는다(.bedrock_agentcore.yaml ecr_repository).
ECR_NAME="bedrock-agentcore-${NAME}"
echo "ECR repo 삭제: $ECR_NAME"
aws ecr delete-repository --repository-name "$ECR_NAME" --force --region "$REGION" 2>/dev/null || echo "ECR repo 없음/스킵"
echo "✅ teardown 완료 (IAM role은 수동 확인)"
