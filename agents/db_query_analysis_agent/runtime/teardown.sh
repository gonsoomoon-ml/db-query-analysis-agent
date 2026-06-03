#!/usr/bin/env bash
# db-query-analysis-agent Runtime 정리: Runtime → ECR repo → CodeBuild 프로젝트 → 실행/빌드 IAM role.
# best-effort(일부 실패해도 나머지 계속) — 그래서 set -e 미사용.
# 주의: toolkit이 만든 IAM role은 보통 이 에이전트 전용이나, 계정에서 공유 설정이면 다른 에이전트에
#       영향 줄 수 있음 — 실행 전 확인 권장.
set -uo pipefail
PROJECT_ROOT="$(cd "$(dirname "$0")/../../.." && pwd)"
source "$PROJECT_ROOT/.env" 2>/dev/null || true
REGION="${AWS_REGION:-us-east-1}"
NAME="${RUNTIME_NAME:-db_query_analysis_agent_${DEMO_USER:-ubuntu}}"

delete_role() {  # 인라인·관리형 정책 제거 후 role 삭제 (정책이 남으면 delete-role 실패하므로 선제거)
  local role="${1:-}" p arn
  [ -z "$role" ] && return 0
  for p in $(aws iam list-role-policies --role-name "$role" --query 'PolicyNames[]' --output text 2>/dev/null); do
    aws iam delete-role-policy --role-name "$role" --policy-name "$p" 2>/dev/null || true
  done
  for arn in $(aws iam list-attached-role-policies --role-name "$role" --query 'AttachedPolicies[].PolicyArn' --output text 2>/dev/null); do
    aws iam detach-role-policy --role-name "$role" --policy-arn "$arn" 2>/dev/null || true
  done
  if aws iam delete-role --role-name "$role" 2>/dev/null; then
    echo "✅ IAM role 삭제: $role"
  else
    echo "⚠️  IAM role 삭제 실패/없음: $role"
  fi
}

# 실행 role은 Runtime 삭제 전에 조회 (삭제 후엔 얻을 수 없음)
EXEC_ROLE=""
if [ -n "${RUNTIME_ID:-}" ]; then
  EXEC_ROLE=$(aws bedrock-agentcore-control get-agent-runtime --agent-runtime-id "$RUNTIME_ID" \
      --region "$REGION" --query roleArn --output text 2>/dev/null | sed 's#.*/##') || EXEC_ROLE=""
fi

# 1) Runtime
if [ -n "${RUNTIME_ID:-}" ]; then
  if aws bedrock-agentcore-control delete-agent-runtime --agent-runtime-id "$RUNTIME_ID" --region "$REGION" 2>/dev/null; then
    echo "✅ Runtime 삭제: $RUNTIME_ID"
  else
    echo "⚠️  Runtime 삭제 실패/없음: $RUNTIME_ID"
  fi
else
  echo "RUNTIME_ID 없음 — Runtime 스킵"
fi

# 2) ECR repo (toolkit 접두사)
ECR_NAME="bedrock-agentcore-${NAME}"
if aws ecr delete-repository --repository-name "$ECR_NAME" --force --region "$REGION" 2>/dev/null; then
  echo "✅ ECR repo 삭제: $ECR_NAME"
else
  echo "⚠️  ECR repo 없음/스킵: $ECR_NAME"
fi

# 3) CodeBuild 프로젝트
CB_PROJECT="bedrock-agentcore-${NAME}-builder"
if aws codebuild delete-project --name "$CB_PROJECT" --region "$REGION" 2>/dev/null; then
  echo "✅ CodeBuild 프로젝트 삭제: $CB_PROJECT"
else
  echo "⚠️  CodeBuild 프로젝트 없음/스킵: $CB_PROJECT"
fi

# 4) IAM roles — 실행 role + 빌드 role(이름 규칙: ...SDKRuntime-... → ...SDKCodeBuild-...)
delete_role "$EXEC_ROLE"
[ -n "$EXEC_ROLE" ] && delete_role "${EXEC_ROLE/Runtime/CodeBuild}"

echo "✅ teardown 완료"
