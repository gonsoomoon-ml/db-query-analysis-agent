#!/usr/bin/env bash
# infra/cognito-gateway/teardown.sh — Gateway/Target → CFN stack → DEPLOY_BUCKET → .env 정리
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'
log()  { echo -e "${GREEN}[teardown]${NC} $1"; }
warn() { echo -e "${YELLOW}[teardown]${NC} $1"; }
fail() { echo -e "${RED}[teardown]${NC} $1"; exit 1; }

[[ -f "$PROJECT_ROOT/.env" ]] && { set -a; source "$PROJECT_ROOT/.env"; set +a; }

REGION="${AWS_REGION:-us-east-1}"
DEMO_USER="${DEMO_USER:-${USER:-ubuntu}}"
[[ "$DEMO_USER" =~ ^[a-zA-Z0-9-]{1,16}$ ]] \
    || fail "DEMO_USER='$DEMO_USER' 잘못된 형식 (영문/숫자/하이픈만 ≤16자)"
ACCOUNT_ID="$(aws sts get-caller-identity --query Account --output text 2>/dev/null || echo '')"
[[ -n "$ACCOUNT_ID" ]] || fail "AWS 자격증명 미설정"

STACK="dbq-${DEMO_USER}-cognito-gateway"
DEPLOY_BUCKET="dbq-${DEMO_USER}-deploy-${ACCOUNT_ID}-${REGION}"

log "region=$REGION demo_user=$DEMO_USER stack=$STACK"

# delete + wait + verify (idempotent — 이미 삭제된 stack 도 안전)
delete_stack() {
    local stack="$1"
    if ! aws cloudformation describe-stacks --region "$REGION" --stack-name "$stack" >/dev/null 2>&1; then
        warn "  (stack '$stack' 없음 — skip)"
        return 0
    fi
    aws cloudformation delete-stack --region "$REGION" --stack-name "$stack"
    aws cloudformation wait stack-delete-complete --region "$REGION" --stack-name "$stack" 2>/dev/null || true
    if aws cloudformation describe-stacks --region "$REGION" --stack-name "$stack" \
            --query 'Stacks[0].StackStatus' --output text 2>/dev/null \
            | grep -qv "DELETE_COMPLETE"; then
        fail "  stack '$stack' 삭제 실패. 'aws cloudformation describe-stack-events --stack-name $stack' 로 원인 확인"
    fi
    log "  stack '$stack' 삭제 완료"
}

# ── 1. boto3 자원 먼저 삭제 (Gateway + Targets) ──
# CFN stack 의 Lambda invoke 권한이 살아있는 동안 호출 필요
log "Gateway + Target 정리 (boto3, idempotent)"
DEMO_USER="$DEMO_USER" AWS_REGION="$REGION" \
    uv run python "$PROJECT_ROOT/infra/cognito-gateway/cleanup_gateway.py" \
    || warn "cleanup_gateway.py 실패 (무시 후 진행)"

# ── 1b. OAuth2 credential provider 삭제 ──────────
# deploy_runtime.py 가 생성. Cognito(issuer/clientId/secret)를 박아두므로 Cognito 재생성 시
# stale 가 됨 — deploy_runtime 은 존재 시 idempotent skip 하여 옛 Cognito 를 가리킨 채 남는다.
# 따라서 Cognito teardown 시 함께 제거(미존재해도 안전).
OAUTH_PROVIDER_NAME="dbq-${DEMO_USER}-oauth"
log "OAuth2 provider 삭제: $OAUTH_PROVIDER_NAME"
aws bedrock-agentcore-control delete-oauth2-credential-provider \
    --name "$OAUTH_PROVIDER_NAME" --region "$REGION" 2>/dev/null \
    && log "  provider 삭제됨" || warn "  (provider 미존재/이미 삭제 — skip)"

# ── 2. CFN stack 삭제 ─────────────────────────────
log "CFN stack 삭제: $STACK"
delete_stack "$STACK"

# ── Lambda CW Log Group 삭제 (CFN 가 cascade 안 함) ──
for LAMBDA_SUFFIX in check-sql-rules get-table-meta analyze-sql-with-llm; do
    LAMBDA_LG="/aws/lambda/dbq-${DEMO_USER}-${LAMBDA_SUFFIX}"
    if aws logs delete-log-group --region "$REGION" --log-group-name "$LAMBDA_LG" 2>/dev/null; then
        log "  Lambda log group ${LAMBDA_LG} 삭제"
    fi
done

# ── 3. DEPLOY_BUCKET 비우기 + 삭제 ──────────────
if aws s3api head-bucket --bucket "$DEPLOY_BUCKET" --region "$REGION" 2>/dev/null; then
    log "DEPLOY_BUCKET 정리: s3://$DEPLOY_BUCKET"
    aws s3 rm "s3://$DEPLOY_BUCKET" --recursive --region "$REGION" >/dev/null
    aws s3 rb "s3://$DEPLOY_BUCKET" --region "$REGION"
fi

# ── 4. 벤더링 빌드 아티팩트 정리 (handler.py 만 남기고 전부 제거) ──
# agents/·data/ 벤더링 + analyze 의 pip 패키지(pydantic/strands/… ~40개) + __pycache__ 모두 청소.
# 패키지별 열거(누락 발생)를 폐기하고 "handler.py 제외 전부" — deploy.sh 가 재생성하므로 안전.
for TOOL in check_sql_rules get_table_meta analyze_sql_with_llm; do
    TOOL_DIR="$PROJECT_ROOT/infra/cognito-gateway/lambda/${TOOL}"
    find "$TOOL_DIR" -mindepth 1 -maxdepth 1 ! -name handler.py -exec rm -rf {} + 2>/dev/null || true
done
rm -f "$PROJECT_ROOT/infra/cognito-gateway/cognito.packaged.yaml"

# ── 5. .env Stage B 변수 비우기 ─────────────────
log ".env Stage B 변수 비우기"
for key in COGNITO_USER_POOL_ID COGNITO_DOMAIN COGNITO_CLIENT_ID COGNITO_CLIENT_SECRET \
           COGNITO_GATEWAY_SCOPE GATEWAY_ID GATEWAY_URL \
           LAMBDA_CHECK_SQL_RULES_ARN LAMBDA_GET_TABLE_META_ARN LAMBDA_ANALYZE_SQL_WITH_LLM_ARN; do
    sed -i "s|^${key}=.*|${key}=|" "$PROJECT_ROOT/.env" 2>/dev/null || true
done

log "Stage B teardown 완료"
