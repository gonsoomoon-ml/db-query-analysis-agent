# db-query-analysis-agent

MySQL/SQL 쿼리 1차 리뷰 에이전트 (Strands Agents + AWS Bedrock) — 규칙 기반 체크 +
테이블 메타 조회 + LLM 심층 분석. **로컬(in-process)** 또는 **관리형 AgentCore Runtime**으로
실행. 설계: `design/2026-06-03-db-query-analysis-agent-migration-spec.md`.

## 로드맵

`Stage 1`(로컬 단일 에이전트 + plain tool 3종) → `Phase 1` SQLite 백엔드 → `Phase 2`
AgentCore Runtime → `Phase 3` Cognito/Gateway → **(다음)** Redis 캐시 → A2A/Supervisor.
**Stage 1 · Phase 1 · Phase 2 · Phase 3 완료** (모두 `main`).

### TOOLS_SOURCE 전환

로컬·Runtime 어디서나 `TOOLS_SOURCE` 환경변수로 도구 공급원을 전환합니다.

| 값 | 설명 |
|---|---|
| `inprocess` (기본) | 오프라인 in-process @tool 에이전트 — Cognito/Gateway 불필요 |
| `gateway` | Cognito-보안 Gateway MCP 도구 에이전트 |

인프라 배포: `infra/cognito-gateway/deploy.sh` (실행 후 `.env`의 Cognito/Gateway 변수가 자동 채워짐).
설계 상세: `design/2026-06-03-phase3-cognito-gateway-spec.md`.

## 빠른 시작 (로컬)

```bash
bash bootstrap.sh                # uv sync --extra dev + .env 생성 + AWS_REGION/DEMO_USER + 테스트
# 단발
uv run -m agents.db_query_analysis_agent.local.run --sql "DELETE FROM orders"
# 대화형 (멀티턴)
uv run -m agents.db_query_analysis_agent.local.chat
```

## 메타데이터 백엔드

테이블 메타데이터의 단일 출처는 `data/mock/table_meta.py`의 `TABLE_META`이며, `META_BACKEND`로
출처를 전환합니다(응답 형태는 동일):

- `mock` (기본) — `TABLE_META`를 메모리에서 직접 제공. 파일·서버 의존 0.
- `sqlite` — `TABLE_META`로 빌드한 `sample.db`를 PRAGMA로 조회 + 실제 `EXPLAIN QUERY PLAN`(read-only)으로 분석 그라운딩.
- `redis` (배포 단계 · optional) — 배포된 Runtime 인스턴스 간 공유·갱신 캐시.

```bash
META_BACKEND=sqlite uv run -m agents.db_query_analysis_agent.local.run --sql "SELECT * FROM orders WHERE user_id = 1"

# redis (optional): redis 패키지 + 서버 필요
docker run -d -p 6379:6379 redis
uv run --extra redis python -m data.seed.seed_redis
META_BACKEND=redis uv run -m agents.db_query_analysis_agent.local.run --sql "..."
```

## 원격 실행 (AgentCore Runtime · Phase 2)

SigV4 인증 + SSE 스트리밍 + 세션 기반 멀티턴. 엔트리포인트는 **`-m`(모듈)** 로 실행하세요.

```bash
# 배포 (toolkit + CodeBuild ARM64 — 로컬 Docker 불필요) → RUNTIME_ARN 을 .env 에 저장
uv run python -m agents.db_query_analysis_agent.runtime.deploy_runtime
# 단발 호출
uv run python -m agents.db_query_analysis_agent.runtime.invoke_runtime --query "SELECT * FROM orders WHERE user_id = 1"
# 멀티턴 chat
uv run python -m agents.db_query_analysis_agent.runtime.chat
```

배포·검증·정리(`teardown.sh`)의 전체 절차는 아래 **테스트 가이드 §3 (원격 e2e)** 참고.

## 타 에이전트/프로그램에서 호출

```python
from agents.db_query_analysis_agent.shared.review import review_sql
text = await review_sql("SELECT * FROM orders WHERE user_id = 1")
```

## 테스트 가이드

테스트는 4계층입니다: **단위 테스트**(오프라인·AWS 불필요) → **로컬 e2e**(실제 Bedrock 호출) → **원격 e2e**(배포된 AgentCore Runtime, SigV4) → **게이트웨이 e2e**(Cognito-보안 Gateway · Phase 3).

### 0. 사전 준비

```bash
bash bootstrap.sh            # uv sync --extra dev + .env 생성 + (가능하면) 단위 테스트
# 또는 수동:
uv sync --extra dev          # pytest·ruff·agentcore 툴킷 포함
```

- **단위 테스트**: AWS 자격증명 **불필요** — LLM/네트워크는 전부 mock입니다.
- **로컬/원격 e2e**: AWS 자격증명(`aws configure` 또는 환경변수) + Bedrock Haiku 4.5 모델 액세스 필요.

### 1. 단위 테스트 (오프라인 · 기본)

```bash
uv run pytest                # 전체 — 95 passed, 1 skipped
uv run pytest -v             # 테스트별 상세
uv run pytest tests/test_check_sql_rules.py        # 파일 단위
uv run pytest -k "explain or sse"                  # 키워드 필터
```

- **모두 hermetic** — 실제 Bedrock을 호출하지 않습니다(`monkeypatch`/가짜 모델·에이전트). 따라서 자격증명 없이 통과해야 정상입니다.
- **스킵 1건**: `test_meta_backend_parity.py`의 redis 패리티 테스트는 `redis` 패키지가 없으면 건너뜁니다. 포함하려면:

  ```bash
  uv sync --extra dev --extra redis
  uv run pytest -rs            # 스킵 사유까지 표시
  ```

커버리지(영역별):

| 영역 | 파일 | 개수 |
|---|---|---|
| 규칙 기반 체크 | `test_check_sql_rules` | 15 |
| 메타 조회 도구 | `test_get_table_meta` | 11 |
| SQLite 백엔드 / 빌드 | `test_sqlite_backend` · `test_build_sqlite` | 8 |
| 백엔드 패리티 (mock = sqlite = redis) | `test_meta_backend_parity` | 5 (redis 1 스킵) |
| EXPLAIN QUERY PLAN (read-only) | `test_explain` | 4 |
| LLM 심층 분석 도구 | `test_analyze_sql` | 5 |
| Lambda 핸들러 (Gateway 타깃) | `test_lambda_handlers` | 15 |
| Gateway 헬퍼 (토큰/MCP) | `test_gateway_helper` | 4 |
| TOOLS_SOURCE 스위치 | `test_agent_session` | 4 |
| Runtime 엔트리포인트 (세션·SSE·gateway 분기) | `test_runtime_entrypoint` | 6 |
| 원격 SSE 파싱 | `test_remote_sse` | 5 |
| 멀티라인 입력 (REPL) | `test_repl` | 5 |
| 메타 스키마·모델 헬퍼·스트리밍·facade·agent build | (5개 파일) | 9 |

린트:

```bash
uv run ruff check .
```

### 2. 로컬 e2e (실제 Bedrock 호출)

AWS 자격증명 + Bedrock 모델 액세스가 필요합니다. `META_BACKEND`로 메타데이터 출처를 바꿔가며 검증합니다(`mock` 기본, `sqlite`는 `TABLE_META`에서 빌드한 `sample.db`에 실제 `EXPLAIN QUERY PLAN`을 실행해 분석을 그라운딩):

```bash
# 단발 (mock 기본) — 규칙 위반 탐지 확인
uv run -m agents.db_query_analysis_agent.local.run --sql "DELETE FROM orders"
# SQLite 백엔드 — 실제 쿼리 플랜 그라운딩
META_BACKEND=sqlite uv run -m agents.db_query_analysis_agent.local.run \
  --sql "SELECT * FROM orders WHERE user_id = 1"
# 대화형 (멀티턴)
uv run -m agents.db_query_analysis_agent.local.chat
```

확인 포인트: `WHERE` 없는 `DELETE`/`UPDATE` 경고, 인덱스 효율 분석, 한국어 리뷰 스트리밍.

### 3. 원격 e2e (AgentCore Runtime · Phase 2)

> 엔트리포인트는 **반드시 `-m`(모듈)** 로 실행하세요. 파일 경로(`uv run …/invoke_runtime.py`)로 실행하면 repo root가 `sys.path`에 없어 import가 실패합니다.

```bash
# 1) 배포 — toolkit + CodeBuild ARM64(로컬 Docker 불필요). 첫 배포 수 분 소요.
uv run python -m agents.db_query_analysis_agent.runtime.deploy_runtime
#    → RUNTIME_NAME/ID/ARN 을 .env 에 저장

# 2) 단발 invoke (SigV4)
uv run python -m agents.db_query_analysis_agent.runtime.invoke_runtime \
  --query "SELECT * FROM orders WHERE user_id = 1"

# 3) 멀티턴 chat — 한 runtimeSessionId 를 재사용해 warm 세션
uv run python -m agents.db_query_analysis_agent.runtime.chat

# 4) 정리 — Runtime + bedrock-agentcore-* ECR repo 삭제 (자동 생성 IAM role은 수동 확인)
bash agents/db_query_analysis_agent/runtime/teardown.sh
```

**멀티턴 chat 예시** — 한 세션에서 아래를 순서대로 입력. 각 입력은 **빈 줄(Enter)** 로 전송, `/reset` 새 세션, `/quit` 종료. 턴 2·3은 일부러 직전 내용을 다시 적지 않아 warm 세션을 검증합니다:

```text
> SELECT * FROM orders WHERE user_id = 1
  → (턴1) 규칙 위반·인덱스 효율·최적화 전체 리뷰

> 방금 쿼리에서 가장 큰 문제 하나만 한 줄로 알려줘
  → (턴2) "방금 쿼리" = 턴1 — SQL 재입력 없이 맥락 참조

> 그 문제를 고친 SQL이랑 추가하면 좋은 인덱스를 같이 제안해줘
  → (턴3) "그 문제" = 턴2 답변 — 대화 누적 맥락

> orders 대신 users 테이블이었어도 같은 지적이야?
  → (턴4) 앞 대화 전체를 전제로 한 가정 질문

> /quit
```

확인 포인트:
- 단발 리뷰가 SSE로 실시간 스트리밍되는지.
- **턴 2~4가 직전 쿼리/답변을 추가 설명 없이 이해하면 warm 세션 정상** (같은 `runtimeSessionId` 재사용 → 컨테이너가 `agent.messages` 보존). 맥락을 잃거나 "어떤 쿼리?"를 되물으면 세션이 안 붙은 것.
- `/reset` 직후엔 새 세션 id라 맥락이 초기화(cold)됨 — 일부러 확인해볼 것.
- 세션 키는 `runtimeSessionId`(헤더)를 우선 사용하므로 payload 없이 헤더만으로도 세션이 격리됩니다.

### 4. 게이트웨이 e2e (Cognito + AgentCore Gateway · Phase 3)

SQL 도구를 Cognito-보안 Gateway(MCP)로 노출하고 Runtime이 `TOOLS_SOURCE=gateway`로 소비하는 경로. 인바운드는 그대로 SigV4, 아웃바운드(Runtime→Gateway)만 Cognito M2M JWT. AWS 자격증명 + CFN/Lambda/Gateway/Bedrock 권한 필요.

```bash
# 1) Cognito + Gateway + Lambda ×3 배포 (CFN + boto3). .env 에 COGNITO_*/GATEWAY_* 자동 기록.
bash infra/cognito-gateway/deploy.sh

# 2) 표준 MCP 클라이언트 검증 (SigV4 Runtime 무관 — Cognito JWT 직접):
uv run python -c "
from dotenv import load_dotenv; load_dotenv()
from agents.db_query_analysis_agent.shared.gateway import get_gateway_token, create_mcp_client
with create_mcp_client(get_gateway_token()) as m:
    print([t.tool_name for t in m.list_tools_sync()])
    print(m.call_tool_sync(tool_use_id='v', name='check-sql-rules___check_sql_rules', arguments={'sql':'SELECT * FROM orders'}))
"

# 3) Runtime 을 gateway 모드로 재배포 (OAuth2 provider 생성 + TOOLS_SOURCE=gateway 주입):
uv run python -m agents.db_query_analysis_agent.runtime.deploy_runtime
# 4) gateway 경유 invoke (Runtime → Cognito JWT → Gateway → Lambda):
uv run python -m agents.db_query_analysis_agent.runtime.invoke_runtime --query "SELECT * FROM orders WHERE user_id = 1"

# 5) 로컬에서도 gateway 모드(직접 토큰)로 호출 가능:
TOOLS_SOURCE=gateway uv run -m agents.db_query_analysis_agent.local.run --sql "SELECT * FROM orders WHERE user_id = 1"

# 6) 정리 — target → Gateway → CFN 스택 삭제:
bash infra/cognito-gateway/teardown.sh
```

확인 포인트:
- (2) `list_tools` 에 `check-sql-rules___check_sql_rules` · `get-table-meta___get_table_meta` · `analyze-sql-with-llm___analyze_sql_with_llm` 3개 노출 + 호출 시 `{"violations":[...]}` 반환.
- (4) gateway invoke 가 SigV4 invoke 와 동일한 리뷰를 스트리밍(차이는 도구가 Gateway·Lambda 경유라는 점뿐). **`runtimeUserId` 필수** — 없으면 workload identity 토큰 획득 실패.
- `TOOLS_SOURCE=inprocess`(기본)로 두면 Cognito/Gateway 없이 오프라인 동작(§2) — 회귀 확인용.
- analyze Lambda 는 `DB_PATH=/tmp` 로 EXPLAIN용 `sample.db` 를 빌드(읽기전용 `/var/task` 우회). 실패해도 graceful(분석은 정상).

**게이트웨이 chat (멀티턴)** — 로컬·원격 두 경로 모두 warm(세션 캐시):

```bash
# (A) 로컬 gateway chat — 한 세션에서 agent 재사용 → warm 멀티턴 + Gateway 경유 도구
TOOLS_SOURCE=gateway uv run python -m agents.db_query_analysis_agent.local.chat
# (B) 배포된 Runtime 의 gateway 모드 chat (SigV4 호출, 도구는 Gateway 경유)
uv run python -m agents.db_query_analysis_agent.runtime.chat
```

(A) 로컬 — 각 입력은 빈 줄(Enter)로 전송, `/reset` 새 세션, `/quit` 종료. 한 세션에서 agent가 유지돼 맥락이 누적됩니다:

```text
> SELECT * FROM orders WHERE user_id = 1
  → (턴1) Gateway→Lambda 도구로 전체 리뷰

> 방금 쿼리에서 가장 큰 문제 하나만 한 줄로
  → (턴2) "방금 쿼리" = 턴1 맥락 (로컬은 agent 재사용 → warm)

> 그걸 고친 SQL과 추천 인덱스를 같이 제안해줘
  → (턴3) 턴1·2 누적 맥락

> /quit
```

✅ **둘 다 warm**: (A) 로컬은 한 `agent_session` 안에서 agent를 재사용하고, (B) 배포된 Runtime의 gateway 모드는 **`session_id`별 (agent, 열린 MCP)을 캐시·재사용**(`_gateway_sessions`)해 같은 `runtimeSessionId`의 턴들이 맥락을 잇습니다 — 위 예시의 "방금 쿼리"가 로컬·원격 모두 통합니다. 첫 호출만 토큰+`list_tools`+agent 생성, 이후 재사용(Phase 2 inprocess 캐시와 동일 패턴). caveat: 원격은 첫 호출 JWT를 계속 써서 토큰 TTL(~1h)을 넘는 긴 세션은 만료 가능(일반 chat 길이엔 무방).

## 참고: 토큰 캐시 (Cache R/W 0/0)

단발/짧은 프롬프트에서는 토큰 usage의 `Cache R/W: 0/0`이 정상입니다. 캐시 가능한
prefix(도구 스키마 + 시스템 프롬프트)가 Bedrock 최소 캐시 크기(~1,024 토큰) 미만이면
캐시 엔트리가 생성되지 않습니다. prefix가 커지거나 멀티턴 대화가 누적되면 캐시가
활성화되어 Cache Read/Write가 0보다 커집니다. (`cache_tools`/`cachePoint`는 이미 구성됨.)
