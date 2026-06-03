#!/usr/bin/env bash
set -euo pipefail
GREEN='\033[0;32m'; YELLOW='\033[0;33m'; BLUE='\033[0;34m'; RED='\033[0;31m'; NC='\033[0m'
pass(){ echo -e "${GREEN}[완료]${NC} $1"; }
fail(){ echo -e "${RED}[실패]${NC} $1"; exit 1; }
info(){ echo -e "${BLUE}[정보]${NC} $1"; }
warn(){ echo -e "${YELLOW}[건너뜀]${NC} $1"; }
PROJECT_ROOT="$(cd "$(dirname "$0")" && pwd)"; cd "$PROJECT_ROOT"

echo -e "${BLUE}== db-query-analysis-agent 부트스트랩 ==${NC}"

command -v uv &>/dev/null || fail "'uv' 미설치 — curl -LsSf https://astral.sh/uv/install.sh | sh"
uv sync --extra dev; pass "의존성 설치 (uv sync --extra dev)"

if [[ -f .env ]]; then warn ".env 이미 존재 (유지)"; else cp .env.example .env; pass ".env 생성 (.env.example 복사)"; fi

update_env(){ local k="$1" v="$2"; if grep -q "^${k}=" .env; then sed -i "s|^${k}=.*|${k}=${v}|" .env; else echo "${k}=${v}" >> .env; fi; }
read_env(){ grep "^$1=" .env 2>/dev/null | head -1 | cut -d= -f2- || echo ""; }

existing="$(read_env AWS_REGION)"; default_region="${existing:-us-east-1}"
if [[ -t 0 ]]; then echo -n "  AWS 리전 (Enter=기본 '$default_region'): "; read -r r; r="${r:-$default_region}"; else r="$default_region"; fi
update_env AWS_REGION "$r"; pass "AWS_REGION=$r"

existing_user="$(read_env DEMO_USER)"; default_user="${existing_user:-${USER:-ubuntu}}"
if [[ -t 0 ]]; then
  echo "  DEMO_USER — 공유 환경에서 시연자 충돌 방지 prefix (영문/숫자/하이픈 ≤16자)."
  echo -n "  DEMO_USER (Enter=기본 '$default_user'): "; read -r du; du="${du:-$default_user}"
else du="$default_user"; fi
[[ "$du" =~ ^[a-zA-Z0-9-]{1,16}$ ]] || fail "DEMO_USER='$du' 형식 오류 (영문/숫자/하이픈 ≤16자)"
update_env DEMO_USER "$du"; pass "DEMO_USER=$du"

if aws sts get-caller-identity --query Account --output text >/dev/null 2>&1; then
  pass "AWS 자격증명 검증 (Bedrock 호출 가능)"
else
  warn "AWS 자격증명 미설정 — 단위테스트는 OK, 실제 리뷰엔 'aws configure' 필요"
fi

if uv run pytest -q; then pass "테스트 통과"; else warn "테스트 실패 — 로그 확인"; fi

echo -e "${GREEN}부트스트랩 완료${NC}"
info "단발:   uv run -m agents.db_query_analysis_agent.local.run --sql \"DELETE FROM orders\""
info "대화형: uv run -m agents.db_query_analysis_agent.local.chat"
