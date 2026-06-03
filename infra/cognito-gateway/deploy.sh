#!/usr/bin/env bash
# infra/cognito-gateway/deploy.sh — Cognito + 3 Lambda + IAM (CFN) + Gateway + 3 Target (boto3)
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$PROJECT_ROOT/infra/cognito-gateway"

GREEN='\033[0;32m'
RED='\033[0;31m'
NC='\033[0m'
log()  { echo -e "${GREEN}[deploy]${NC} $1"; }
fail() { echo -e "${RED}[deploy]${NC} $1"; exit 1; }

# ── 사전 검증 ────────────────────────────────────
aws sts get-caller-identity --query Account --output text >/dev/null 2>&1 \
    || fail "AWS 자격증명 미설정"

[[ -f "$PROJECT_ROOT/.env" ]] || fail ".env 미존재. cp .env.example .env 후 재실행"

set -a
source "$PROJECT_ROOT/.env"
set +a

REGION="${AWS_REGION:-us-east-1}"
DEMO_USER="${DEMO_USER:-${USER:-ubuntu}}"
[[ "$DEMO_USER" =~ ^[a-zA-Z0-9-]{1,16}$ ]] \
    || fail "DEMO_USER='$DEMO_USER' 잘못된 형식 (영문/숫자/하이픈만 ≤16자)"

ACCOUNT_ID="$(aws sts get-caller-identity --query Account --output text)"
STACK="dbq-${DEMO_USER}-cognito-gateway"
DEPLOY_BUCKET="dbq-${DEMO_USER}-deploy-${ACCOUNT_ID}-${REGION}"

log "region=$REGION demo_user=$DEMO_USER account=$ACCOUNT_ID"
log "stack=$STACK / deploy bucket=$DEPLOY_BUCKET"

# ── 0. DEPLOY_BUCKET 보장 (idempotent) ───────────
if ! aws s3api head-bucket --bucket "$DEPLOY_BUCKET" --region "$REGION" 2>/dev/null; then
    log "DEPLOY_BUCKET 생성: s3://$DEPLOY_BUCKET"
    aws s3 mb "s3://$DEPLOY_BUCKET" --region "$REGION"
    aws s3api put-public-access-block --bucket "$DEPLOY_BUCKET" \
        --public-access-block-configuration "BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true"
else
    log "DEPLOY_BUCKET 이미 존재 (재사용)"
fi

# ── 1. 에이전트 패키지 벤더링 (Lambda zip 포함용) ──────────────────
# 각 Lambda 디렉토리에 필요한 패키지 서브셋을 복사.
# handler.py 의 import 경로(from agents.db_query_analysis_agent...  from data...)가
# Lambda zip 내에서 해석되려면 agents/ + data/ 가 핸들러 옆에 존재해야 함.
# runtime/ 과 local/ 는 Lambda 불필요 — 제외.

vendor_agent_pkg() {
    local dest_dir="$1"
    # 두 번째 인자로 복사할 서브패키지를 지정(기본 전체). check/meta 는 불필요한 shared(strands
    # 의존 agent.py 포함)를 제외해 zip 경량화 — check='tools', meta='tools meta', analyze='tools meta shared'.
    local subpkgs="${2:-tools meta shared}"
    log "벤더링: agents/[$subpkgs] → $dest_dir"
    rm -rf "$dest_dir/agents" "$dest_dir/data"

    # agents 패키지 — runtime/ local/ 제외
    mkdir -p "$dest_dir/agents/db_query_analysis_agent"
    cp "$PROJECT_ROOT/agents/__init__.py" "$dest_dir/agents/" 2>/dev/null || touch "$dest_dir/agents/__init__.py"
    cp "$PROJECT_ROOT/agents/db_query_analysis_agent/__init__.py" \
       "$dest_dir/agents/db_query_analysis_agent/" 2>/dev/null || touch "$dest_dir/agents/db_query_analysis_agent/__init__.py"

    for subpkg in $subpkgs; do
        if [[ -d "$PROJECT_ROOT/agents/db_query_analysis_agent/$subpkg" ]]; then
            cp -r "$PROJECT_ROOT/agents/db_query_analysis_agent/$subpkg" \
                  "$dest_dir/agents/db_query_analysis_agent/$subpkg"
        fi
    done

    # data 패키지
    cp -r "$PROJECT_ROOT/data" "$dest_dir/data"
    # pycache 제거 (zip 크기 최소화)
    find "$dest_dir/agents" "$dest_dir/data" -type d -name '__pycache__' -exec rm -rf {} + 2>/dev/null || true
}

# check_sql_rules — strands-free, tools 만 필요 (meta·shared 불필요)
log "Lambda 벤더링: check_sql_rules (tools)"
vendor_agent_pkg "$PROJECT_ROOT/infra/cognito-gateway/lambda/check_sql_rules" "tools"

# get_table_meta — strands-free, tools+meta 필요 (shared 불필요)
log "Lambda 벤더링: get_table_meta (tools meta)"
vendor_agent_pkg "$PROJECT_ROOT/infra/cognito-gateway/lambda/get_table_meta" "tools meta"

# analyze_sql_with_llm — strands 필요. 빌드 호스트 python 과 Lambda(python3.12) 버전이 다르면
# pydantic_core 등 네이티브 확장의 ABI 가 어긋나 import 실패 → Lambda 타깃(cp312/x86_64) 휠을
# 강제 다운로드(--platform/--python-version/--only-binary). 스테일 휠 혼입 방지 위해 pip 산출물을
# 먼저 청소(handler.py 만 보존) 후 재벤더링·재설치.
log "Lambda 벤더링: analyze_sql_with_llm (tools meta shared + pip install strands-agents[cp312])"
ANALYZE_DIR="$PROJECT_ROOT/infra/cognito-gateway/lambda/analyze_sql_with_llm"
find "$ANALYZE_DIR" -mindepth 1 -maxdepth 1 ! -name handler.py -exec rm -rf {} +
vendor_agent_pkg "$ANALYZE_DIR" "tools meta shared"
pip install strands-agents \
    -t "$ANALYZE_DIR" \
    --platform manylinux2014_x86_64 \
    --python-version 3.12 \
    --implementation cp \
    --only-binary=:all: \
    --quiet

# ── 2. cfn package (Lambda Code 디렉토리 zip + S3 업로드) ─
log "cfn package — Lambda Code 디렉토리 zip + S3 업로드"
aws cloudformation package \
    --template-file "$PROJECT_ROOT/infra/cognito-gateway/cognito.yaml" \
    --s3-bucket "$DEPLOY_BUCKET" \
    --s3-prefix "cognito-gateway" \
    --region "$REGION" \
    --output-template-file "$PROJECT_ROOT/infra/cognito-gateway/cognito.packaged.yaml" >/dev/null

# ── 3. CFN deploy (Cognito + Lambda + IAM 통합) ──
log "CFN deploy: $STACK"
aws cloudformation deploy \
    --region "$REGION" \
    --template-file "$PROJECT_ROOT/infra/cognito-gateway/cognito.packaged.yaml" \
    --stack-name "$STACK" \
    --capabilities CAPABILITY_NAMED_IAM \
    --parameter-overrides "DemoUser=${DEMO_USER}"

# ── 4. CFN outputs 환경변수 export ──────────────
log "CFN outputs 캡처"
get_output() {
    aws cloudformation describe-stacks --region "$REGION" --stack-name "$STACK" \
        --query "Stacks[0].Outputs[?OutputKey=='$1'].OutputValue" --output text
}
export COGNITO_USER_POOL_ID="$(get_output UserPoolId)"
export COGNITO_DOMAIN="$(get_output Domain)"
export COGNITO_CLIENT_ID="$(get_output ClientId)"
export COGNITO_GATEWAY_SCOPE="$(get_output ResourceServerScope)"
export GATEWAY_IAM_ROLE_ARN="$(get_output GatewayIamRoleArn)"
export LAMBDA_CHECK_SQL_RULES_ARN="$(get_output LambdaCheckSqlRulesArn)"
export LAMBDA_GET_TABLE_META_ARN="$(get_output LambdaGetTableMetaArn)"
export LAMBDA_ANALYZE_SQL_WITH_LLM_ARN="$(get_output LambdaAnalyzeSqlWithLlmArn)"

# Cognito Client Secret 별도 조회 (CFN output 미노출)
export COGNITO_CLIENT_SECRET="$(aws cognito-idp describe-user-pool-client \
    --region "$REGION" \
    --user-pool-id "$COGNITO_USER_POOL_ID" \
    --client-id "$COGNITO_CLIENT_ID" \
    --query 'UserPoolClient.ClientSecret' --output text)"

# ── 5. boto3 setup — Gateway + 3 Target ─────────
log "boto3: Gateway + GatewayTarget × 3 생성"
TMP_OUT="$(mktemp)"
trap 'rm -f "$TMP_OUT"' EXIT
DEMO_USER="$DEMO_USER" AWS_REGION="$REGION" \
    uv run python "$PROJECT_ROOT/infra/cognito-gateway/setup_gateway.py" \
    | tee "$TMP_OUT"

GATEWAY_ID="$(grep '^GATEWAY_ID=' "$TMP_OUT" | cut -d= -f2-)"
GATEWAY_URL="$(grep '^GATEWAY_URL=' "$TMP_OUT" | cut -d= -f2-)"
[[ -n "$GATEWAY_ID" && -n "$GATEWAY_URL" ]] \
    || fail "setup_gateway.py 출력에서 GATEWAY_ID/URL 캡처 실패"

# ── 6. .env 갱신 ─────────────────────────────────
log ".env 갱신"
update_env() {
    local key="$1" val="$2"
    if grep -qE "^${key}=" "$PROJECT_ROOT/.env"; then
        sed -i "s|^${key}=.*|${key}=${val}|" "$PROJECT_ROOT/.env"
    else
        echo "${key}=${val}" >> "$PROJECT_ROOT/.env"
    fi
}
update_env COGNITO_USER_POOL_ID              "$COGNITO_USER_POOL_ID"
update_env COGNITO_DOMAIN                    "$COGNITO_DOMAIN"
update_env COGNITO_CLIENT_ID                 "$COGNITO_CLIENT_ID"
update_env COGNITO_CLIENT_SECRET             "$COGNITO_CLIENT_SECRET"
update_env COGNITO_GATEWAY_SCOPE             "$COGNITO_GATEWAY_SCOPE"
update_env GATEWAY_ID                        "$GATEWAY_ID"
update_env GATEWAY_URL                       "$GATEWAY_URL"
update_env LAMBDA_CHECK_SQL_RULES_ARN        "$LAMBDA_CHECK_SQL_RULES_ARN"
update_env LAMBDA_GET_TABLE_META_ARN         "$LAMBDA_GET_TABLE_META_ARN"
update_env LAMBDA_ANALYZE_SQL_WITH_LLM_ARN   "$LAMBDA_ANALYZE_SQL_WITH_LLM_ARN"

log "Stage B deploy 완료"
log "  Gateway URL: $GATEWAY_URL"
log "  Lambda (check_sql_rules):     $LAMBDA_CHECK_SQL_RULES_ARN"
log "  Lambda (get_table_meta):      $LAMBDA_GET_TABLE_META_ARN"
log "  Lambda (analyze_sql_with_llm): $LAMBDA_ANALYZE_SQL_WITH_LLM_ARN"
